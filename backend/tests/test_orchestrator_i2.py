"""Tests for Stage I2 ('Resilient Core' / CoreOrchestrator).

Verifies:
1. Successful run in default and lazy modes (coverage >= 90% on first attempt).
2. Code defect scenario: tests fail, 1 iteration repair is called, failure preserved.
3. Coverage gap scenario: coverage 80% -> repair -> coverage 100% -> passed.
4. Cannot repair from LLM and coverage missing from runner.
5. Provider failure / quota / timeout without retry multiplication.
6. Zero executable changed lines (N/A verdict, merge blocked).
7. Static validation failure in QualityGate triggers repair.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.coverage.calculator import CoverageCalculator
from app.schemas import (
    ErrorCategory,
    ExecutionReport,
    ExecutionState,
    RunnerStatus,
    Verdict,
)
from app.services.llm.contracts import EnvironmentProfile
from app.services.llm.fake import FakeProvider
from app.services.llm.limits import StageBudget
from app.services.llm.provider import ProviderError, ProviderResponse, TokenUsage
from app.services.llm.service import LLMService
from app.services.llm.settings import LLMSettings
from app.services.orchestrator.service import CoreOrchestrator
from app.services.parsing.code import SourceFile
from app.services.parsing.context import ContextBuilder, GitHubInput
from app.services.parsing.documents import extract_document
from app.services.runner.port import FakeRunner
from app.services.validation.gate import QualityGate

USAGE = TokenUsage(input_tokens=100, output_tokens=100, total_tokens=200)

SCENARIOS_JSON = json.dumps(
    {
        "scenarios": [
            {
                "scenario_id": "double-integers",
                "given": ["An integer 3"],
                "when": ["It is doubled"],
                "then": ["The result is 6"],
                "source_ids": ["requirements:requirements.md"],
            }
        ]
    }
)

LAZY_SCENARIOS_JSON = json.dumps(
    {
        "scenarios": [
            {
                "scenario_id": "double-integers",
                "given": ["An integer 3"],
                "when": ["It is doubled"],
                "then": ["The result is 6"],
                "source_ids": ["diff"],
            }
        ]
    }
)

TESTS_JSON = json.dumps(
    {
        "files": [
            {
                "path": "generated_tests/test_calculator.py",
                "content": (
                    "from calculator import double\n\n"
                    "def test_double():\n"
                    "    assert double(3) == 6\n"
                ),
                "scenario_ids": ["double-integers"],
            }
        ]
    }
)

INVALID_TESTS_JSON = json.dumps(
    {
        "files": [
            {
                "path": "generated_tests/test_calculator.py",
                "content": (
                    "import os\n"
                    "from calculator import double\n\n"
                    "def test_double():\n"
                    "    os.system('echo test')\n"
                    "    assert double(3) == 6\n"
                ),
                "scenario_ids": ["double-integers"],
            }
        ]
    }
)

REPAIR_REPLACE_JSON = json.dumps(
    {
        "action": "replace",
        "bundle": {
            "files": [
                {
                    "path": "generated_tests/test_calculator.py",
                    "content": (
                        "from calculator import double\n\n"
                        "def test_double():\n"
                        "    assert double(3) == 6\n"
                        "    assert double(4) == 8\n"
                    ),
                    "scenario_ids": ["double-integers"],
                }
            ]
        },
    }
)

CANNOT_REPAIR_JSON = json.dumps(
    {
        "action": "cannot_repair",
        "explanation": "Defect is in implementation logic, not test expectations.",
    }
)


def _make_diff(before: str, after: str, path: str = "calculator.py") -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def _write_cov_json(
    tmp_path: Path, executed_lines: list[int], filename: str = "coverage.json"
) -> Path:
    cov_file = tmp_path / filename
    cov_file.write_text(
        json.dumps(
            {
                "files": {
                    "/workspace/calculator.py": {
                        "executed_lines": executed_lines,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return cov_file


@pytest.fixture
def env_profile() -> EnvironmentProfile:
    return EnvironmentProfile(
        profile_id="python-pytest", python_version="3.13", dependencies=("pytest",)
    )


@pytest.fixture
def default_context(env_profile: EnvironmentProfile):
    diff = _make_diff("", "def double(value):\n    return value * 2\n")
    return (
        ContextBuilder()
        .build(
            run_id="run-default-1",
            snapshot_id="snapshot-default-1",
            environment=env_profile,
            files=(SourceFile("calculator.py", "def double(value):\n    return value * 2\n"),),
            documents=(extract_document("requirements.md", b"Double every integer."),),
            mode="default",
            github=GitHubInput("repo", "a" * 40, "b" * 40, diff),
        )
        .context
    )


@pytest.fixture
def lazy_context(env_profile: EnvironmentProfile):
    before = "def double(value):\n    return value\n"
    after = "def double(value):\n    return value * 2\n"
    diff = _make_diff(before, after)
    return (
        ContextBuilder()
        .build(
            run_id="run-lazy-1",
            snapshot_id="snapshot-lazy-1",
            environment=env_profile,
            files=(SourceFile("calculator.py", after),),
            mode="lazy",
            github=GitHubInput("repo", "a" * 40, "b" * 40, diff),
        )
        .context
    )


# =====================================================================
# 1. Successful run in default and lazy modes
# =====================================================================


def test_orchestrator_success_default_mode(default_context, tmp_path: Path):
    """Default mode with requirements achieves >= 90% coverage and merge decision."""
    cov_file = _write_cov_json(tmp_path, [1, 2])
    cov_calc = CoverageCalculator(snapshot_root=tmp_path, container_workdir=Path("/workspace"))

    provider = FakeProvider(
        responses=[
            ProviderResponse(text=SCENARIOS_JSON, finish_reason="STOP", usage=USAGE),
            ProviderResponse(text=TESTS_JSON, finish_reason="STOP", usage=USAGE),
        ],
        token_counts=[100, 100],
    )
    settings = LLMSettings(backend="fake", model_id="fake-model")
    llm_service = LLMService(provider, settings)

    exec_report = ExecutionReport(
        job_id=uuid4(),
        run_id=uuid4(),
        snapshot_id=uuid4(),
        attempt_id=uuid4(),
        test_bundle_hash="0" * 64,
        image_digest="sha256:" + "0" * 57,
        status=RunnerStatus.SUCCESS,
        exit_code=0,
        collect_result={"success": True, "collected": 1, "errors": 0},
        test_result={"success": True, "passed": 1, "failed": 0},
        raw_coverage_reports=[str(cov_file)],
    )
    runner = FakeRunner(responses=[exec_report])

    orchestrator = CoreOrchestrator(
        llm_service=llm_service,
        quality_gate=QualityGate(),
        runner=runner,
        coverage_calculator=cov_calc,
    )

    result = orchestrator.execute(default_context)

    assert result.run_state.execution_state == ExecutionState.COMPLETED
    assert result.run_state.verdict == Verdict.PASSED
    assert result.coverage_report is not None
    assert result.coverage_report.coverage_verdict == Verdict.PASSED
    assert result.coverage_report.covered_changed_lines == 2
    assert result.coverage_report.executable_changed_lines == 2
    assert result.merge_decision is not None
    assert result.merge_decision.allowed is True
    assert result.repair_iterations == 0


def test_orchestrator_success_lazy_mode(lazy_context, tmp_path: Path):
    """Lazy mode with diff achieves >= 90% coverage on first attempt."""
    cov_file = _write_cov_json(tmp_path, [2])
    cov_calc = CoverageCalculator(snapshot_root=tmp_path, container_workdir=Path("/workspace"))

    provider = FakeProvider(
        responses=[
            ProviderResponse(text=LAZY_SCENARIOS_JSON, finish_reason="STOP", usage=USAGE),
            ProviderResponse(text=TESTS_JSON, finish_reason="STOP", usage=USAGE),
        ],
        token_counts=[100, 100],
    )
    settings = LLMSettings(backend="fake", model_id="fake-model")
    llm_service = LLMService(provider, settings)

    exec_report = ExecutionReport(
        job_id=uuid4(),
        run_id=uuid4(),
        snapshot_id=uuid4(),
        attempt_id=uuid4(),
        test_bundle_hash="0" * 64,
        image_digest="sha256:" + "0" * 57,
        status=RunnerStatus.SUCCESS,
        exit_code=0,
        collect_result={"success": True, "collected": 1, "errors": 0},
        test_result={"success": True, "passed": 1, "failed": 0},
        raw_coverage_reports=[str(cov_file)],
    )
    runner = FakeRunner(responses=[exec_report])

    orchestrator = CoreOrchestrator(
        llm_service=llm_service,
        quality_gate=QualityGate(),
        runner=runner,
        coverage_calculator=cov_calc,
    )

    result = orchestrator.execute(lazy_context)

    assert result.run_state.execution_state == ExecutionState.COMPLETED
    assert result.run_state.verdict == Verdict.PASSED
    assert result.coverage_report is not None
    assert result.coverage_report.coverage_verdict == Verdict.PASSED


# =====================================================================
# 2. Defect in code: repair called, failure preserved without false success
# =====================================================================


def test_orchestrator_repair_on_test_failure_then_success(default_context, tmp_path: Path):
    """Test failure in sandbox triggers 1 repair iteration, which succeeds."""
    cov_file = _write_cov_json(tmp_path, [1, 2])
    cov_calc = CoverageCalculator(snapshot_root=tmp_path, container_workdir=Path("/workspace"))

    provider = FakeProvider(
        responses=[
            ProviderResponse(text=SCENARIOS_JSON, finish_reason="STOP", usage=USAGE),
            ProviderResponse(text=TESTS_JSON, finish_reason="STOP", usage=USAGE),
            ProviderResponse(text=REPAIR_REPLACE_JSON, finish_reason="STOP", usage=USAGE),
        ],
        token_counts=[100, 100, 100],
    )
    settings = LLMSettings(backend="fake", model_id="fake-model", max_repairs=2)
    llm_service = LLMService(provider, settings)

    failed_report = ExecutionReport(
        job_id=uuid4(),
        run_id=uuid4(),
        snapshot_id=uuid4(),
        attempt_id=uuid4(),
        test_bundle_hash="0" * 64,
        image_digest="sha256:" + "0" * 57,
        status=RunnerStatus.SUCCESS,
        exit_code=1,
        collect_result={"success": True, "collected": 1, "errors": 0},
        test_result={"success": False, "passed": 0, "failed": 1},
        raw_coverage_reports=[str(cov_file)],
    )
    success_report = ExecutionReport(
        job_id=uuid4(),
        run_id=uuid4(),
        snapshot_id=uuid4(),
        attempt_id=uuid4(),
        test_bundle_hash="0" * 64,
        image_digest="sha256:" + "0" * 57,
        status=RunnerStatus.SUCCESS,
        exit_code=0,
        collect_result={"success": True, "collected": 1, "errors": 0},
        test_result={"success": True, "passed": 1, "failed": 0},
        raw_coverage_reports=[str(cov_file)],
    )
    runner = FakeRunner(responses=[failed_report, success_report])

    orchestrator = CoreOrchestrator(
        llm_service=llm_service,
        quality_gate=QualityGate(),
        runner=runner,
        coverage_calculator=cov_calc,
    )

    result = orchestrator.execute(default_context)

    assert result.repair_iterations == 1
    assert result.run_state.execution_state == ExecutionState.COMPLETED
    assert result.run_state.verdict == Verdict.PASSED


def test_orchestrator_code_defect_remains_failed_no_false_success(
    default_context, tmp_path: Path
):
    """Persistent test failure after repair results in verdict=failed, no false success."""
    cov_file = _write_cov_json(tmp_path, [1, 2])
    cov_calc = CoverageCalculator(snapshot_root=tmp_path, container_workdir=Path("/workspace"))

    provider = FakeProvider(
        responses=[
            ProviderResponse(text=SCENARIOS_JSON, finish_reason="STOP", usage=USAGE),
            ProviderResponse(text=TESTS_JSON, finish_reason="STOP", usage=USAGE),
            ProviderResponse(text=REPAIR_REPLACE_JSON, finish_reason="STOP", usage=USAGE),
        ],
        token_counts=[100, 100, 100],
    )
    # Only 1 repair allowed
    settings = LLMSettings(backend="fake", model_id="fake-model", max_repairs=1)
    llm_service = LLMService(provider, settings)

    failed_report = ExecutionReport(
        job_id=uuid4(),
        run_id=uuid4(),
        snapshot_id=uuid4(),
        attempt_id=uuid4(),
        test_bundle_hash="0" * 64,
        image_digest="sha256:" + "0" * 57,
        status=RunnerStatus.SUCCESS,
        exit_code=1,
        collect_result={"success": True, "collected": 1, "errors": 0},
        test_result={"success": False, "passed": 0, "failed": 1},
        raw_coverage_reports=[str(cov_file)],
    )
    runner = FakeRunner(responses=[failed_report, failed_report])

    orchestrator = CoreOrchestrator(
        llm_service=llm_service,
        quality_gate=QualityGate(),
        runner=runner,
        coverage_calculator=cov_calc,
    )

    result = orchestrator.execute(default_context)

    assert result.repair_iterations == 1
    assert result.run_state.execution_state == ExecutionState.FAILED
    assert result.run_state.verdict == Verdict.FAILED
    assert result.merge_decision is None


# =====================================================================
# 3. Coverage gap scenario: 50% -> repair -> 100% -> passed
# =====================================================================


def test_orchestrator_repair_on_coverage_gap(default_context, tmp_path: Path):
    """Coverage below 90% triggers coverage_gap feedback and repair."""
    low_cov_file = _write_cov_json(tmp_path, [1], "low_cov.json")  # 1 out of 2 lines = 50%
    full_cov_file = _write_cov_json(tmp_path, [1, 2], "full_cov.json")  # 2 lines = 100%
    cov_calc = CoverageCalculator(snapshot_root=tmp_path, container_workdir=Path("/workspace"))

    provider = FakeProvider(
        responses=[
            ProviderResponse(text=SCENARIOS_JSON, finish_reason="STOP", usage=USAGE),
            ProviderResponse(text=TESTS_JSON, finish_reason="STOP", usage=USAGE),
            ProviderResponse(text=REPAIR_REPLACE_JSON, finish_reason="STOP", usage=USAGE),
        ],
        token_counts=[100, 100, 100],
    )
    settings = LLMSettings(backend="fake", model_id="fake-model", max_repairs=2)
    llm_service = LLMService(provider, settings)

    low_cov_report = ExecutionReport(
        job_id=uuid4(),
        run_id=uuid4(),
        snapshot_id=uuid4(),
        attempt_id=uuid4(),
        test_bundle_hash="0" * 64,
        image_digest="sha256:" + "0" * 57,
        status=RunnerStatus.SUCCESS,
        exit_code=0,
        collect_result={"success": True, "collected": 1, "errors": 0},
        test_result={"success": True, "passed": 1, "failed": 0},
        raw_coverage_reports=[str(low_cov_file)],
    )
    high_cov_report = ExecutionReport(
        job_id=uuid4(),
        run_id=uuid4(),
        snapshot_id=uuid4(),
        attempt_id=uuid4(),
        test_bundle_hash="0" * 64,
        image_digest="sha256:" + "0" * 57,
        status=RunnerStatus.SUCCESS,
        exit_code=0,
        collect_result={"success": True, "collected": 1, "errors": 0},
        test_result={"success": True, "passed": 1, "failed": 0},
        raw_coverage_reports=[str(full_cov_file)],
    )
    runner = FakeRunner(responses=[low_cov_report, high_cov_report])

    orchestrator = CoreOrchestrator(
        llm_service=llm_service,
        quality_gate=QualityGate(),
        runner=runner,
        coverage_calculator=cov_calc,
    )

    result = orchestrator.execute(default_context)

    assert result.repair_iterations == 1
    assert result.run_state.execution_state == ExecutionState.COMPLETED
    assert result.run_state.verdict == Verdict.PASSED


# =====================================================================
# 4. Cannot repair from LLM and coverage missing from runner
# =====================================================================


def test_orchestrator_cannot_repair_terminates_immediately(default_context, tmp_path: Path):
    """When LLM returns action='cannot_repair', run fails with explanation preserved."""
    cov_file = _write_cov_json(tmp_path, [1, 2])
    cov_calc = CoverageCalculator(snapshot_root=tmp_path, container_workdir=Path("/workspace"))

    provider = FakeProvider(
        responses=[
            ProviderResponse(text=SCENARIOS_JSON, finish_reason="STOP", usage=USAGE),
            ProviderResponse(text=TESTS_JSON, finish_reason="STOP", usage=USAGE),
            ProviderResponse(text=CANNOT_REPAIR_JSON, finish_reason="STOP", usage=USAGE),
        ],
        token_counts=[100, 100, 100],
    )
    settings = LLMSettings(backend="fake", model_id="fake-model", max_repairs=2)
    llm_service = LLMService(provider, settings)

    failed_report = ExecutionReport(
        job_id=uuid4(),
        run_id=uuid4(),
        snapshot_id=uuid4(),
        attempt_id=uuid4(),
        test_bundle_hash="0" * 64,
        image_digest="sha256:" + "0" * 57,
        status=RunnerStatus.SUCCESS,
        exit_code=1,
        collect_result={"success": True, "collected": 1, "errors": 0},
        test_result={"success": False, "passed": 0, "failed": 1},
        raw_coverage_reports=[str(cov_file)],
    )
    runner = FakeRunner(responses=[failed_report])

    orchestrator = CoreOrchestrator(
        llm_service=llm_service,
        quality_gate=QualityGate(),
        runner=runner,
        coverage_calculator=cov_calc,
    )

    result = orchestrator.execute(default_context)

    assert result.run_state.execution_state == ExecutionState.FAILED
    assert result.run_state.verdict == Verdict.FAILED
    assert result.run_state.error_category == ErrorCategory.MODEL
    assert "Defect is in implementation logic" in (result.explanation or "")


def test_orchestrator_coverage_missing_fails_immediately_no_repair(
    default_context, tmp_path: Path
):
    """Missing coverage report immediately fails without calling repair."""
    cov_calc = CoverageCalculator(snapshot_root=tmp_path, container_workdir=Path("/workspace"))

    provider = FakeProvider(
        responses=[
            ProviderResponse(text=SCENARIOS_JSON, finish_reason="STOP", usage=USAGE),
            ProviderResponse(text=TESTS_JSON, finish_reason="STOP", usage=USAGE),
        ],
        token_counts=[100, 100],
    )
    settings = LLMSettings(backend="fake", model_id="fake-model", max_repairs=2)
    llm_service = LLMService(provider, settings)

    # raw_coverage_reports is empty -> coverage_missing
    no_cov_report = ExecutionReport(
        job_id=uuid4(),
        run_id=uuid4(),
        snapshot_id=uuid4(),
        attempt_id=uuid4(),
        test_bundle_hash="0" * 64,
        image_digest="sha256:" + "0" * 57,
        status=RunnerStatus.SUCCESS,
        exit_code=0,
        collect_result={"success": True, "collected": 1, "errors": 0},
        test_result={"success": True, "passed": 1, "failed": 0},
        raw_coverage_reports=[],
    )
    runner = FakeRunner(responses=[no_cov_report])

    orchestrator = CoreOrchestrator(
        llm_service=llm_service,
        quality_gate=QualityGate(),
        runner=runner,
        coverage_calculator=cov_calc,
    )

    result = orchestrator.execute(default_context)

    assert result.run_state.execution_state == ExecutionState.FAILED
    assert result.run_state.verdict == Verdict.FAILED
    assert result.repair_iterations == 0  # Repair was NOT called!


# =====================================================================
# 5. Provider failure / quota / timeout / StageBudget
# =====================================================================


def test_orchestrator_provider_quota_exhausted(default_context):
    """Provider quota exhaustion marks run as failed with ErrorCategory.QUOTA."""
    provider = FakeProvider(
        responses=[ProviderError("provider_quota", retryable=False)],
        token_counts=[100],
    )
    settings = LLMSettings(backend="fake", model_id="fake-model")
    llm_service = LLMService(provider, settings)

    orchestrator = CoreOrchestrator(
        llm_service=llm_service,
        quality_gate=QualityGate(),
        runner=FakeRunner(),
    )

    result = orchestrator.execute(default_context)

    assert result.run_state.execution_state == ExecutionState.FAILED
    assert result.run_state.verdict == Verdict.FAILED
    assert result.run_state.error_category == ErrorCategory.QUOTA


def test_orchestrator_stage_budget_deadline_exceeded(default_context):
    """Stage budget deadline exceeded marks run as failed with ErrorCategory.ENVIRONMENT."""
    provider = FakeProvider(
        responses=[ProviderError("deadline_exceeded", retryable=False)],
        token_counts=[100],
    )
    settings = LLMSettings(backend="fake", model_id="fake-model")
    llm_service = LLMService(provider, settings)

    orchestrator = CoreOrchestrator(
        llm_service=llm_service,
        quality_gate=QualityGate(),
        runner=FakeRunner(),
    )

    budget = StageBudget(
        remaining_seconds=10.0,
        remaining_api_calls=5,
        remaining_generations=1,
    )
    result = orchestrator.execute(default_context, budget=budget)

    assert result.run_state.execution_state == ExecutionState.FAILED
    assert result.run_state.error_category == ErrorCategory.ENVIRONMENT


# =====================================================================
# 6. Zero executable changed lines (N/A verdict, merge blocked)
# =====================================================================


def test_orchestrator_zero_executable_lines_returns_not_applicable(
    env_profile, tmp_path: Path
):
    """Zero executable lines in diff yields verdict=not_applicable, blocking merge."""
    diff = "--- a/README.md\n+++ b/README.md\n@@ -1 +1,2 @@\n # Title\n+Docs update\n"
    cov_file = tmp_path / "coverage.json"
    cov_file.write_text(json.dumps({"files": {}}), encoding="utf-8")
    cov_calc = CoverageCalculator(snapshot_root=tmp_path, container_workdir=Path("/workspace"))

    context = (
        ContextBuilder()
        .build(
            run_id="run-zero-1",
            snapshot_id="snapshot-zero-1",
            environment=env_profile,
            files=(SourceFile("calculator.py", "def double(value):\n    return value * 2\n"),),
            documents=(extract_document("requirements.md", b"Update documentation."),),
            mode="default",
            github=GitHubInput("repo", "a" * 40, "b" * 40, diff),
        )
        .context
    )

    provider = FakeProvider(
        responses=[
            ProviderResponse(text=SCENARIOS_JSON, finish_reason="STOP", usage=USAGE),
            ProviderResponse(text=TESTS_JSON, finish_reason="STOP", usage=USAGE),
        ],
        token_counts=[100, 100],
    )
    settings = LLMSettings(backend="fake", model_id="fake-model")
    llm_service = LLMService(provider, settings)

    exec_report = ExecutionReport(
        job_id=uuid4(),
        run_id=uuid4(),
        snapshot_id=uuid4(),
        attempt_id=uuid4(),
        test_bundle_hash="0" * 64,
        image_digest="sha256:" + "0" * 57,
        status=RunnerStatus.SUCCESS,
        exit_code=0,
        collect_result={"success": True, "collected": 1, "errors": 0},
        test_result={"success": True, "passed": 1, "failed": 0},
        raw_coverage_reports=[str(cov_file)],
    )
    runner = FakeRunner(responses=[exec_report])

    orchestrator = CoreOrchestrator(
        llm_service=llm_service,
        quality_gate=QualityGate(),
        runner=runner,
        coverage_calculator=cov_calc,
    )

    result = orchestrator.execute(context)

    assert result.run_state.execution_state == ExecutionState.COMPLETED
    assert result.run_state.verdict == Verdict.NOT_APPLICABLE
    assert result.merge_decision is None  # Merge is strictly blocked!


# =====================================================================
# 7. Static validation failure in QualityGate triggers repair
# =====================================================================


def test_orchestrator_static_validation_failure_repair(default_context, tmp_path: Path):
    """QualityGate failure triggers validation_error feedback and succeeds after repair."""
    cov_file = _write_cov_json(tmp_path, [1, 2])
    cov_calc = CoverageCalculator(snapshot_root=tmp_path, container_workdir=Path("/workspace"))

    provider = FakeProvider(
        responses=[
            ProviderResponse(text=SCENARIOS_JSON, finish_reason="STOP", usage=USAGE),
            # First bundle has invalid filename: invalid_name.py
            ProviderResponse(text=INVALID_TESTS_JSON, finish_reason="STOP", usage=USAGE),
            # Repaired bundle has valid filename: test_calculator.py
            ProviderResponse(text=REPAIR_REPLACE_JSON, finish_reason="STOP", usage=USAGE),
        ],
        token_counts=[100, 100, 100],
    )
    settings = LLMSettings(backend="fake", model_id="fake-model", max_repairs=2)
    llm_service = LLMService(provider, settings)

    exec_report = ExecutionReport(
        job_id=uuid4(),
        run_id=uuid4(),
        snapshot_id=uuid4(),
        attempt_id=uuid4(),
        test_bundle_hash="0" * 64,
        image_digest="sha256:" + "0" * 57,
        status=RunnerStatus.SUCCESS,
        exit_code=0,
        collect_result={"success": True, "collected": 1, "errors": 0},
        test_result={"success": True, "passed": 1, "failed": 0},
        raw_coverage_reports=[str(cov_file)],
    )
    runner = FakeRunner(responses=[exec_report])

    orchestrator = CoreOrchestrator(
        llm_service=llm_service,
        quality_gate=QualityGate(),
        runner=runner,
        coverage_calculator=cov_calc,
    )

    result = orchestrator.execute(default_context)

    assert result.repair_iterations == 1
    assert result.run_state.execution_state == ExecutionState.COMPLETED
    assert result.run_state.verdict == Verdict.PASSED
