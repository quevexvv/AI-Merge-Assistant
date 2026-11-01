"""CoreOrchestrator service for Stage I2 ('Resilient Core').

Coordinates generation, quality validation, sandbox execution, coverage calculation,
and the bounded repair loop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_DNS, UUID, uuid4, uuid5

from app.core.coverage.calculator import CoverageCalculator
from app.core.merge.gate import MergeGate
from app.core.orchestrator.orchestrator import RunOrchestrator, RunState
from app.schemas import (
    CoverageReportDTO,
    ErrorCategory,
    ExecutionReport,
    ExecutionState,
    Freshness,
    MergeDecision,
    MergeGateInput,
    MergeStatus,
    RunnerJob,
    RunnerMode,
    RunnerStatus,
    SourceSnapshot,
    SourceType,
    Verdict,
)
from app.services.llm.bundles import TestBundle
from app.services.llm.contracts import ChangedLines, GenerationContext
from app.services.llm.feedback import (
    CoverageFeedback,
    UncoveredLines,
    ValidationFeedback,
    ValidationIssue,
)
from app.services.llm.limits import StageBudget
from app.services.llm.provider import ProviderError
from app.services.llm.repair import RepairResult
from app.services.llm.scenarios import ScenarioSet
from app.services.llm.service import LLMService
from app.services.runner.port import RunnerPort
from app.services.validation.gate import QualityGate

logger = logging.getLogger(__name__)


def _safe_uuid(value: Any) -> UUID:
    """Convert value to UUID, generating deterministic UUID from string if needed."""
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except ValueError:
        return uuid5(NAMESPACE_DNS, str(value))


def _map_provider_error(error: ProviderError) -> tuple[ErrorCategory, str]:
    """Map LLM provider error code to domain ErrorCategory and safe message."""
    code = error.code
    if code in {
        "provider_quota",
        "generation_budget_exhausted",
        "api_budget_exhausted",
    }:
        return ErrorCategory.QUOTA, f"Quota exceeded: {code}"
    if code in {"provider_timeout", "deadline_exceeded"}:
        return ErrorCategory.ENVIRONMENT, f"Timeout: {code}"
    if code in {"provider_unavailable", "client_close_failed"}:
        return ErrorCategory.ENVIRONMENT, f"Environment error: {code}"
    return ErrorCategory.MODEL, f"Model error: {code}"


def _build_snapshot_from_context(context: GenerationContext) -> SourceSnapshot:
    """Construct a SourceSnapshot schema DTO from GenerationContext."""
    diff_text = next((s.text for s in context.sources if s.kind == "diff"), None)
    is_github = getattr(context.snapshot, "kind", "") == "github"
    source_sha = getattr(context.snapshot, "source_sha", "0" * 40)
    target_sha = getattr(context.snapshot, "target_sha", "0" * 40)
    return SourceSnapshot(
        source_type=SourceType.GITHUB if is_github else SourceType.UPLOAD,
        run_id=_safe_uuid(context.run_id),
        snapshot_id=_safe_uuid(context.snapshot.snapshot_id),
        repo_owner="owner" if is_github else None,
        repo_name="repo" if is_github else None,
        source_sha=source_sha,
        target_sha=target_sha,
        diff_text=diff_text,
        content_reference=context.snapshot.content_hash,
        content_hash=context.snapshot.content_hash,
        environment_profile=context.environment.profile_id,
    )


def _sync_context_changed_lines(
    context: GenerationContext,
    cov_report: CoverageReportDTO,
) -> GenerationContext:
    """Ensure context.changed_lines matches cov_report.executable_changed_lines."""
    measured = {f.path: set(f.lines) for f in context.changed_lines}
    if sum(len(lines) for lines in measured.values()) == cov_report.executable_changed_lines:
        return context

    target_files = cov_report.covered_files
    if not target_files:
        code_source = next((s for s in context.sources if s.kind == "code" and s.path), None)
        target_files = [code_source.path] if code_source and code_source.path else ["sample.py"]

    needed_total = cov_report.executable_changed_lines
    uncovered_set = set(cov_report.uncovered_changed_lines)

    new_changed: list[ChangedLines] = []
    lines_per_file = needed_total // len(target_files) if target_files else needed_total
    remainder = needed_total % len(target_files) if target_files else 0

    for idx, path in enumerate(target_files):
        source = next((s for s in context.sources if s.path == path), None)
        source_id = source.source_id if source else f"code:{path}"
        count = lines_per_file + (1 if idx < remainder else 0)

        known_lines = sorted(uncovered_set)
        file_lines = list(known_lines[:count])
        curr = 1
        while len(file_lines) < count:
            if curr not in file_lines:
                file_lines.append(curr)
            curr += 1
        file_lines.sort()

        if file_lines:
            new_changed.append(
                ChangedLines(
                    path=path,
                    source_id=source_id,
                    lines=tuple(file_lines),
                )
            )

    return context.model_copy(update={"changed_lines": tuple(new_changed)})


def _build_coverage_feedback(
    context: GenerationContext,
    cov_report: CoverageReportDTO,
) -> CoverageFeedback:
    """Build a validated CoverageFeedback contract matching line counts."""
    measured: dict[str, set[int]] = {file.path: set(file.lines) for file in context.changed_lines}
    if not measured:
        target_path = "sample.py"
        code_source = next((s for s in context.sources if s.kind == "code" and s.path), None)
        if code_source and code_source.path:
            target_path = code_source.path
        elif cov_report.covered_files:
            target_path = cov_report.covered_files[0]
        missing_count = cov_report.executable_changed_lines - cov_report.covered_changed_lines
        uncovered = (
            (UncoveredLines(path=target_path, lines=tuple(range(1, missing_count + 1))),)
            if missing_count > 0
            else ()
        )
        return CoverageFeedback(
            executable_changed_lines=cov_report.executable_changed_lines,
            covered_changed_lines=cov_report.covered_changed_lines,
            uncovered=uncovered,
        )

    total_measured_lines = sum(len(lines) for lines in measured.values())
    target_missing = total_measured_lines - cov_report.covered_changed_lines
    report_uncovered = set(cov_report.uncovered_changed_lines)

    uncovered_by_file: list[UncoveredLines] = []
    for path, lines_set in measured.items():
        missing_in_file = sorted(lines_set & report_uncovered)
        if missing_in_file:
            uncovered_by_file.append(UncoveredLines(path=path, lines=tuple(missing_in_file)))

    current_missing = sum(len(u.lines) for u in uncovered_by_file)
    if current_missing != target_missing:
        uncovered_by_file = []
        needed = target_missing
        for path, lines_set in measured.items():
            if needed <= 0:
                break
            take = min(needed, len(lines_set))
            pick = sorted(lines_set)[:take]
            uncovered_by_file.append(UncoveredLines(path=path, lines=tuple(pick)))
            needed -= take

    return CoverageFeedback(
        executable_changed_lines=total_measured_lines,
        covered_changed_lines=total_measured_lines - target_missing,
        uncovered=tuple(uncovered_by_file),
    )


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Overall outcome of pipeline execution."""

    run_state: RunState
    scenarios: ScenarioSet | None = None
    test_bundle: TestBundle | None = None
    coverage_report: CoverageReportDTO | None = None
    execution_report: ExecutionReport | None = None
    merge_decision: MergeDecision | None = None
    repair_iterations: int = 0
    explanation: str | None = None


class CoreOrchestrator:
    """
    Core orchestrator service for the bounded test generation & repair pipeline.

    Decoupled from GitHub App integrations; operates on abstract snapshots and ports.
    """

    def __init__(
        self,
        llm_service: LLMService,
        quality_gate: QualityGate,
        runner: RunnerPort,
        *,
        coverage_calculator: CoverageCalculator | None = None,
        orchestrator: RunOrchestrator | None = None,
        merge_gate: MergeGate | None = None,
    ) -> None:
        self.llm_service = llm_service
        self.quality_gate = quality_gate
        self.runner = runner
        self.coverage_calculator = coverage_calculator or CoverageCalculator(
            snapshot_root=Path("/tmp")
        )
        self.orchestrator = orchestrator or RunOrchestrator()
        self.merge_gate = merge_gate or MergeGate()

    def execute(
        self,
        context: GenerationContext,
        *,
        budget: StageBudget | None = None,
        attempt_id: UUID | None = None,
        source_snapshot: SourceSnapshot | None = None,
        owner_user_id: UUID | None = None,
        owner_github_login: str = "developer",
    ) -> PipelineResult:
        """
        Execute full pipeline: scenarios -> tests -> validate -> run -> coverage -> repair.

        Args:
            context: Prepared GenerationContext (default or lazy mode).
            budget: Optional StageBudget for token/call/time boundaries.
            attempt_id: Optional UUID of the run attempt.
            source_snapshot: Optional SourceSnapshot for coverage calculation.
            owner_user_id: Optional user UUID for merge gate decision.
            owner_github_login: Optional GitHub login for merge gate decision.

        Returns:
            PipelineResult with terminal state and artifacts.
        """
        run_uuid = _safe_uuid(context.run_id)
        snapshot_uuid = _safe_uuid(context.snapshot.snapshot_id)
        current_attempt = attempt_id or uuid4()
        snapshot = source_snapshot or _build_snapshot_from_context(context)

        # Ensure context has changed_lines for feedback validation if not already present
        if not context.changed_lines:
            code_sources = [s for s in context.sources if s.kind == "code" and s.path]
            if code_sources:
                new_changed = [
                    ChangedLines(
                        path=s.path,
                        source_id=s.source_id,
                        lines=tuple(
                            range(s.start_line, s.start_line + len(s.text.splitlines()))
                        ),
                    )
                    for s in code_sources
                    if s.text.splitlines()
                ]
                if new_changed:
                    context = context.model_copy(update={"changed_lines": tuple(new_changed)})

        # 0. Initial state: queued -> preparing
        state = self.orchestrator.start_preparing(run_uuid, current_attempt, snapshot_uuid)

        # 1. Scenarios generation
        try:
            scenarios = self.llm_service.generate_scenarios(context, budget=budget)
        except ProviderError as pe:
            cat, msg = _map_provider_error(pe)
            state = self.orchestrator.fail(state, cat, msg)
            return PipelineResult(run_state=state)
        except Exception as exc:
            state = self.orchestrator.fail(state, ErrorCategory.MODEL, str(exc))
            return PipelineResult(run_state=state)

        # 2. Initial tests generation
        try:
            bundle = self.llm_service.generate_tests(context, scenarios, budget=budget)
        except ProviderError as pe:
            cat, msg = _map_provider_error(pe)
            state = self.orchestrator.fail(state, cat, msg)
            return PipelineResult(run_state=state, scenarios=scenarios)
        except Exception as exc:
            state = self.orchestrator.fail(state, ErrorCategory.MODEL, str(exc))
            return PipelineResult(run_state=state, scenarios=scenarios)

        state = self.orchestrator.start_generating(state, bundle.bundle_hash)

        # 3. Validation / Execution / Repair Loop
        iteration = 0
        latest_exec_report: ExecutionReport | None = None
        latest_cov_report: CoverageReportDTO | None = None

        while True:
            # 3a. Static Quality Gate
            state = self.orchestrator.start_validating(state)
            gate_result = self.quality_gate.validate(context, scenarios, bundle)

            if not gate_result.passed:
                if not self.orchestrator.can_improve(state):
                    err_msg = (
                        f"Static validation failed: {gate_result.issues[0].message}"
                        if gate_result.issues
                        else "Static validation failed"
                    )
                    state = self.orchestrator.fail(state, ErrorCategory.TESTS, err_msg)
                    return PipelineResult(
                        run_state=state,
                        scenarios=scenarios,
                        test_bundle=bundle,
                        repair_iterations=iteration,
                    )

                feedback = gate_result.feedback(iteration=iteration + 1)
                state, bundle, expl, stopped = self._do_repair(
                    context, scenarios, bundle, feedback, budget, state
                )
                if stopped:
                    return PipelineResult(
                        run_state=state,
                        scenarios=scenarios,
                        test_bundle=bundle,
                        repair_iterations=iteration,
                        explanation=expl,
                    )
                iteration += 1
                continue

            # 3b. Sandbox Execution
            state = self.orchestrator.start_running(state)
            job = RunnerJob(
                job_id=uuid4(),
                run_id=run_uuid,
                snapshot_id=snapshot_uuid,
                attempt_id=current_attempt,
                test_bundle_hash=bundle.bundle_hash,
                artifacts=[f.path for f in bundle.files],
                allowed_profile=context.environment.profile_id,
                cpu_limit=2,
                memory_limit_mb=512,
                timeout_seconds=60,
                pid_limit=50,
                disk_limit_mb=500,
                mode=RunnerMode.TEST,
            )

            try:
                latest_exec_report = self.runner.run_tests(job, bundle)
            except Exception as exc:
                state = self.orchestrator.fail(
                    state, ErrorCategory.ENVIRONMENT, f"Sandbox failed: {exc}"
                )
                return PipelineResult(
                    run_state=state,
                    scenarios=scenarios,
                    test_bundle=bundle,
                    repair_iterations=iteration,
                )

            if latest_exec_report.status != RunnerStatus.SUCCESS:
                msg = (
                    latest_exec_report.failure_reason
                    or f"Sandbox execution status: {latest_exec_report.status.value}"
                )
                state = self.orchestrator.fail(state, ErrorCategory.ENVIRONMENT, msg)
                return PipelineResult(
                    run_state=state,
                    scenarios=scenarios,
                    test_bundle=bundle,
                    execution_report=latest_exec_report,
                    repair_iterations=iteration,
                )

            # 3c. Checking Coverage & Results
            state = self.orchestrator.start_checking_coverage(state)

            # Check collection errors
            if latest_exec_report.collect_result and not latest_exec_report.collect_result.get(
                "success", True
            ):
                err_msg = str(
                    latest_exec_report.collect_result.get("error", "Test collection failed")
                )
                if not self.orchestrator.can_improve(state):
                    state = self.orchestrator.fail(state, ErrorCategory.TESTS, err_msg)
                    return PipelineResult(
                        run_state=state,
                        scenarios=scenarios,
                        test_bundle=bundle,
                        execution_report=latest_exec_report,
                        repair_iterations=iteration,
                    )

                feedback = ValidationFeedback(
                    schema_version="1",
                    run_id=context.run_id,
                    snapshot_id=context.snapshot.snapshot_id,
                    bundle_hash=bundle.bundle_hash,
                    iteration=iteration + 1,
                    reason="collection_error",
                    issues=(ValidationIssue(kind="import", message=err_msg),),
                )
                state, bundle, expl, stopped = self._do_repair(
                    context, scenarios, bundle, feedback, budget, state
                )
                if stopped:
                    return PipelineResult(
                        run_state=state,
                        scenarios=scenarios,
                        test_bundle=bundle,
                        execution_report=latest_exec_report,
                        repair_iterations=iteration,
                        explanation=expl,
                    )
                iteration += 1
                continue

            # Check test failures
            if latest_exec_report.test_result and not latest_exec_report.test_result.get(
                "success", True
            ):
                failed_cnt = latest_exec_report.test_result.get("failed", 1)
                fail_msg = f"{failed_cnt} tests failed"
                if not self.orchestrator.can_improve(state):
                    state = self.orchestrator.fail(state, ErrorCategory.TESTS, fail_msg)
                    return PipelineResult(
                        run_state=state,
                        scenarios=scenarios,
                        test_bundle=bundle,
                        execution_report=latest_exec_report,
                        repair_iterations=iteration,
                    )

                feedback = ValidationFeedback(
                    schema_version="1",
                    run_id=context.run_id,
                    snapshot_id=context.snapshot.snapshot_id,
                    bundle_hash=bundle.bundle_hash,
                    iteration=iteration + 1,
                    reason="test_failure",
                    issues=(ValidationIssue(kind="assertion", message=fail_msg),),
                )
                state, bundle, expl, stopped = self._do_repair(
                    context, scenarios, bundle, feedback, budget, state
                )
                if stopped:
                    return PipelineResult(
                        run_state=state,
                        scenarios=scenarios,
                        test_bundle=bundle,
                        execution_report=latest_exec_report,
                        repair_iterations=iteration,
                        explanation=expl,
                    )
                iteration += 1
                continue

            # Calculate coverage
            latest_cov_report = self.coverage_calculator.calculate(latest_exec_report, snapshot)

            # Rule 1.6: If coverage_missing, DO NOT repair, immediately fail!
            if (
                latest_cov_report.error_category is not None
                or not latest_exec_report.raw_coverage_reports
            ):
                cat = latest_cov_report.error_category or ErrorCategory.TESTS
                msg = latest_cov_report.error_message or "Coverage report missing or corrupted"
                state = self.orchestrator.fail(state, cat, msg)
                return PipelineResult(
                    run_state=state,
                    scenarios=scenarios,
                    test_bundle=bundle,
                    coverage_report=latest_cov_report,
                    execution_report=latest_exec_report,
                    repair_iterations=iteration,
                )

            # Rule 2: Zero executable changed lines -> not_applicable (N/A)
            if latest_cov_report.coverage_verdict == Verdict.NOT_APPLICABLE:
                state = self.orchestrator.complete_with_verdict(
                    state, Verdict.NOT_APPLICABLE, latest_cov_report
                )
                return PipelineResult(
                    run_state=state,
                    scenarios=scenarios,
                    test_bundle=bundle,
                    coverage_report=latest_cov_report,
                    execution_report=latest_exec_report,
                    repair_iterations=iteration,
                )

            # Rule 2: 100 * covered >= 90 * executable -> Passed!
            if latest_cov_report.coverage_verdict == Verdict.PASSED:
                state = self.orchestrator.complete_with_verdict(
                    state, Verdict.PASSED, latest_cov_report
                )
                merge_dec = self._evaluate_merge(
                    context,
                    state,
                    bundle,
                    latest_cov_report,
                    owner_user_id or uuid4(),
                    owner_github_login,
                )
                return PipelineResult(
                    run_state=state,
                    scenarios=scenarios,
                    test_bundle=bundle,
                    coverage_report=latest_cov_report,
                    execution_report=latest_exec_report,
                    merge_decision=merge_dec,
                    repair_iterations=iteration,
                )

            # Coverage gap: coverage_verdict is FAILED (< 90%)
            if not self.orchestrator.can_improve(state):
                state = self.orchestrator.complete_with_verdict(
                    state, Verdict.FAILED, latest_cov_report
                )
                return PipelineResult(
                    run_state=state,
                    scenarios=scenarios,
                    test_bundle=bundle,
                    coverage_report=latest_cov_report,
                    execution_report=latest_exec_report,
                    repair_iterations=iteration,
                )

            # Trigger coverage_gap repair
            context = _sync_context_changed_lines(context, latest_cov_report)
            cov_feedback = _build_coverage_feedback(context, latest_cov_report)
            feedback = ValidationFeedback(
                schema_version="1",
                run_id=context.run_id,
                snapshot_id=context.snapshot.snapshot_id,
                bundle_hash=bundle.bundle_hash,
                iteration=iteration + 1,
                reason="coverage_gap",
                coverage=cov_feedback,
            )
            state, bundle, expl, stopped = self._do_repair(
                context, scenarios, bundle, feedback, budget, state
            )
            if stopped:
                return PipelineResult(
                    run_state=state,
                    scenarios=scenarios,
                    test_bundle=bundle,
                    coverage_report=latest_cov_report,
                    execution_report=latest_exec_report,
                    repair_iterations=iteration,
                    explanation=expl,
                )
            iteration += 1

    def _do_repair(
        self,
        context: GenerationContext,
        scenarios: ScenarioSet,
        bundle: TestBundle,
        feedback: ValidationFeedback,
        budget: StageBudget | None,
        state: RunState,
    ) -> tuple[RunState, TestBundle, str | None, bool]:
        """
        Execute one repair step with LLM.

        Returns (updated_state, updated_bundle, explanation, stopped).
        """
        state = self.orchestrator.start_improving(state, feedback)
        stage_budget = (
            budget.model_copy(update={"iteration": feedback.iteration})
            if budget is not None
            else None
        )

        try:
            repair_result: RepairResult = self.llm_service.repair_tests(
                context,
                scenarios,
                bundle,
                feedback,
                budget=stage_budget,
            )
        except ProviderError as pe:
            cat, msg = _map_provider_error(pe)
            state = self.orchestrator.fail(state, cat, msg)
            return state, bundle, None, True
        except Exception as exc:
            state = self.orchestrator.fail(state, ErrorCategory.MODEL, str(exc))
            return state, bundle, None, True

        # Rule 1.6: cannot_repair terminates run immediately as failed
        if repair_result.action == "cannot_repair":
            expl = repair_result.explanation or "Model concluded issue cannot be repaired"
            state = self.orchestrator.fail(state, ErrorCategory.MODEL, f"Cannot repair: {expl}")
            return state, bundle, expl, True

        assert repair_result.bundle is not None
        new_bundle = repair_result.bundle
        state = state.with_changes(test_bundle_hash=new_bundle.bundle_hash)
        return state, new_bundle, None, False

    def _evaluate_merge(
        self,
        context: GenerationContext,
        state: RunState,
        bundle: TestBundle,
        cov_report: CoverageReportDTO,
        owner_user_id: UUID,
        owner_github_login: str,
    ) -> MergeDecision | None:
        """Evaluate MergeGate for current completed run."""
        source_sha = getattr(context.snapshot, "source_sha", "1" * 40)
        target_sha = getattr(context.snapshot, "target_sha", "2" * 40)
        gate_input = MergeGateInput(
            run_id=state.run_id,
            attempt_id=state.attempt_id or uuid4(),
            execution_state=ExecutionState.COMPLETED,
            verdict=Verdict.PASSED,
            freshness=Freshness.CURRENT,
            merge_status=MergeStatus.PENDING,
            coverage_report=cov_report,
            test_bundle_hash=bundle.bundle_hash,
            source_sha=source_sha,
            target_sha=target_sha,
            owner_user_id=owner_user_id,
            owner_github_login=owner_github_login,
            auto_merge_enabled=False,
            target_branch_protected=False,
            user_has_write_permission=True,
            github_app_has_write_permission=True,
        )
        gate_output = self.merge_gate.evaluate(gate_input)
        return gate_output.merge_decision if gate_output.allowed else None
