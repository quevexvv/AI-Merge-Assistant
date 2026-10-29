"""
MergeGate — генератор серверного решения о мерже.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.schemas import (
    CoverageReportDTO,
    ErrorCategory,
    ExecutionState,
    Freshness,
    MergeDecision,
    MergeGateInput,
    MergeGateOutput,
    MergeStatus,
    Mode,
    Verdict,
)

logger = logging.getLogger(__name__)


class MergeGateError(Exception):
    """Ошибка шлюза мержа."""

    def __init__(self, category: ErrorCategory, message: str):
        self.category = category
        super().__init__(f"[{category.value}] {message}")


@dataclass(frozen=True, slots=True)
class MergeCheckResult:
    """Результат проверки одного условия мержа."""

    passed: bool
    reason: str | None = None
    category: ErrorCategory | None = None


class MergeGate:
    """
    Серверный шлюз принятия решения о мерже.
    Все проверки выполняются детерминированно на основе входных данных.
    """

    def evaluate(self, input_data: MergeGateInput) -> MergeGateOutput:
        denial_reasons: list[str] = []

        # 1. Проверка состояния выполнения (completed НЕ достаточно сам по себе)
        check = self._check_execution_state(input_data)
        if not check.passed and check.reason:
            denial_reasons.append(check.reason)

        # 2. Самостоятельная проверка итогового вердикта прогона (input_data.verdict == passed)
        check = self._check_run_verdict(input_data)
        if not check.passed and check.reason:
            denial_reasons.append(check.reason)

        # 3. Проверка вердикта покрытия
        check = self._check_coverage_verdict(input_data)
        if not check.passed and check.reason:
            denial_reasons.append(check.reason)

        # 4. Проверка порога покрытия (целочисленное неравенство 100 * covered >= 90 * executable)
        check = self._check_coverage_threshold(input_data)
        if not check.passed and check.reason:
            denial_reasons.append(check.reason)

        # 5. Проверка актуальности снимка (freshness == current)
        check = self._check_freshness(input_data)
        if not check.passed and check.reason:
            denial_reasons.append(check.reason)

        # 6. Проверка прав доступа
        check = self._check_permissions(input_data)
        if not check.passed and check.reason:
            denial_reasons.append(check.reason)

        # 7. Проверка режима auto (если применимо)
        if input_data.auto_merge_enabled:
            check = self._check_auto_merge_conditions(input_data)
            if not check.passed and check.reason:
                denial_reasons.append(check.reason)

        allowed = len(denial_reasons) == 0
        mode = Mode.AUTO if (allowed and input_data.auto_merge_enabled) else Mode.MANUAL

        merge_decision = MergeDecision(
            owner_user_id=input_data.owner_user_id,
            owner_github_login=input_data.owner_github_login,
            run_id=input_data.run_id,
            attempt_id=input_data.attempt_id,
            verified_source_sha=input_data.source_sha,
            verified_target_sha=input_data.target_sha,
            verified_test_bundle_hash=input_data.test_bundle_hash,
            allowed=allowed,
            denial_reasons=denial_reasons,
            mode=mode,
            server_created_at=datetime.now(UTC).isoformat(),
        )

        return MergeGateOutput(
            merge_decision=merge_decision,
            allowed=allowed,
            denial_reasons=denial_reasons,
        )

    def _check_execution_state(self, input_data: MergeGateInput) -> MergeCheckResult:
        if input_data.execution_state != ExecutionState.COMPLETED:
            return MergeCheckResult(
                passed=False,
                reason=f"Run not completed: current state is {input_data.execution_state.value}",
                category=ErrorCategory.MODEL,
            )
        return MergeCheckResult(passed=True)

    def _check_run_verdict(self, input_data: MergeGateInput) -> MergeCheckResult:
        """
        Самостоятельный запрет мержа, если итоговый вердикт прогона (тестов) != passed.
        Высокое покрытие не означает успешные тесты.
        """
        if input_data.verdict != Verdict.PASSED:
            verdict_val = input_data.verdict.value if input_data.verdict else "none"
            return MergeCheckResult(
                passed=False,
                reason=f"Run verdict is not passed: {verdict_val}",
                category=ErrorCategory.TESTS,
            )
        return MergeCheckResult(passed=True)

    def _check_coverage_verdict(self, input_data: MergeGateInput) -> MergeCheckResult:
        verdict = input_data.coverage_report.coverage_verdict

        if verdict == Verdict.FAILED:
            return MergeCheckResult(
                passed=False,
                reason="Coverage threshold not met",
                category=ErrorCategory.TESTS,
            )
        if verdict == Verdict.NOT_APPLICABLE:
            return MergeCheckResult(
                passed=False,
                reason="Coverage not applicable (no measurable changed lines)"
                " — merge blocked per T4",
                category=ErrorCategory.TESTS,
            )
        if verdict != Verdict.PASSED:
            return MergeCheckResult(
                passed=False,
                reason=f"Unexpected coverage verdict: {verdict.value}",
                category=ErrorCategory.MODEL,
            )

        if input_data.coverage_report.error_category is not None:
            return MergeCheckResult(
                passed=False,
                reason=f"Test execution error: {input_data.coverage_report.error_message}",
                category=input_data.coverage_report.error_category,
            )

        return MergeCheckResult(passed=True)

    def _check_coverage_threshold(self, input_data: MergeGateInput) -> MergeCheckResult:
        report = input_data.coverage_report
        covered = report.covered_changed_lines
        executable = report.executable_changed_lines

        if executable <= 0:
            return MergeCheckResult(
                passed=False,
                reason="No executable changed lines to measure (executable_changed_lines == 0)",
                category=ErrorCategory.TESTS,
            )

        threshold_met = 100 * covered >= 90 * executable
        if not threshold_met:
            actual_pct = (covered / executable) * 100
            return MergeCheckResult(
                passed=False,
                reason=f"Coverage threshold not met: {covered}/{executable}"
                f" ({actual_pct:.2f}% < 90%)",
                category=ErrorCategory.TESTS,
            )

        return MergeCheckResult(passed=True)

    def _check_freshness(self, input_data: MergeGateInput) -> MergeCheckResult:
        if input_data.freshness != Freshness.CURRENT:
            return MergeCheckResult(
                passed=False,
                reason=f"Snapshot is stale (freshness={input_data.freshness.value})",
                category=ErrorCategory.STALE,
            )
        return MergeCheckResult(passed=True)

    def _check_permissions(self, input_data: MergeGateInput) -> MergeCheckResult:
        if not input_data.user_has_write_permission:
            return MergeCheckResult(
                passed=False,
                reason="User does not have write permission to the repository",
                category=ErrorCategory.RIGHTS,
            )
        if not input_data.github_app_has_write_permission:
            return MergeCheckResult(
                passed=False,
                reason="GitHub App does not have write permission to the repository",
                category=ErrorCategory.RIGHTS,
            )
        return MergeCheckResult(passed=True)

    def _check_auto_merge_conditions(self, input_data: MergeGateInput) -> MergeCheckResult:
        if not input_data.target_branch_protected:
            return MergeCheckResult(
                passed=False,
                reason="Target branch is not protected"
                "(branch protection required for auto-merge)",
                category=ErrorCategory.RIGHTS,
            )
        return MergeCheckResult(passed=True)

    @staticmethod
    def verify_sha_match(
        expected_source_sha: str,
        expected_target_sha: str,
        expected_test_bundle_hash: str,
        actual_source_sha: str,
        actual_target_sha: str,
        actual_test_bundle_hash: str,
    ) -> tuple[bool, str | None]:
        if expected_source_sha != actual_source_sha:
            return (
                False,
                f"Source SHA mismatch: expected {expected_source_sha}, got {actual_source_sha}",
            )
        if expected_target_sha != actual_target_sha:
            return (
                False,
                f"Target SHA mismatch: expected {expected_target_sha}, got {actual_target_sha}",
            )
        if expected_test_bundle_hash != actual_test_bundle_hash:
            return (
                False,
                f"Test bundle hash mismatch: expected {expected_test_bundle_hash},"
        f" got {actual_test_bundle_hash}",
            )
        return True, None

    @staticmethod
    def is_coverage_applicable(report: CoverageReportDTO) -> bool:
        return report.executable_changed_lines > 0

    @staticmethod
    def calculate_coverage_percentage(covered: int, executable: int) -> float | None:
        if executable <= 0:
            return None
        return (covered / executable) * 100


class MergeDecisionBuilder:
    """Строитель MergeDecision."""

    def __init__(self, merge_gate: MergeGate):
        self._gate = merge_gate

    def build_from_run_data(
        self,
        run_id: UUID,
        attempt_id: UUID,
        execution_state: ExecutionState,
        verdict: Verdict,
        freshness: Freshness,
        merge_status: MergeStatus,
        coverage_report: CoverageReportDTO,
        test_bundle_hash: str,
        source_sha: str,
        target_sha: str,
        owner_user_id: UUID,
        owner_github_login: str,
        auto_merge_enabled: bool,
        target_branch_protected: bool,
        user_has_write_permission: bool,
        github_app_has_write_permission: bool,
    ) -> MergeGateOutput:
        input_data = MergeGateInput(
            run_id=run_id,
            attempt_id=attempt_id,
            execution_state=execution_state,
            verdict=verdict,
            freshness=freshness,
            merge_status=merge_status,
            coverage_report=coverage_report,
            test_bundle_hash=test_bundle_hash,
            source_sha=source_sha,
            target_sha=target_sha,
            owner_user_id=owner_user_id,
            owner_github_login=owner_github_login,
            auto_merge_enabled=auto_merge_enabled,
            target_branch_protected=target_branch_protected,
            user_has_write_permission=user_has_write_permission,
            github_app_has_write_permission=github_app_has_write_permission,
        )
        return self._gate.evaluate(input_data)
