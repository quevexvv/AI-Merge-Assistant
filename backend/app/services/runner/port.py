"""Abstract runner port and fake implementation for sandboxed execution."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Iterable

from app.schemas import ExecutionReport, RunnerJob, RunnerStatus
from app.services.llm.bundles import TestBundle


class RunnerPort(ABC):
    """Abstract port for isolated sandbox execution."""

    @abstractmethod
    def run_tests(self, job: RunnerJob, bundle: TestBundle) -> ExecutionReport:
        """Run tests in isolated sandbox and produce an ExecutionReport."""
        raise NotImplementedError


class FakeRunner(RunnerPort):
    """Scriptable in-memory runner for testing and offline execution."""

    def __init__(
        self,
        responses: Iterable[ExecutionReport | Exception] = (),
        *,
        default_status: RunnerStatus = RunnerStatus.SUCCESS,
        default_exit_code: int = 0,
    ) -> None:
        self._responses = deque(responses)
        self._default_status = default_status
        self._default_exit_code = default_exit_code
        self.jobs: list[RunnerJob] = []
        self.bundles: list[TestBundle] = []

    def run_tests(self, job: RunnerJob, bundle: TestBundle) -> ExecutionReport:
        self.jobs.append(job)
        self.bundles.append(bundle)

        if self._responses:
            item = self._responses.popleft()
            if isinstance(item, Exception):
                raise item
            return item

        # Default fallback response
        return ExecutionReport(
            job_id=job.job_id,
            run_id=job.run_id,
            snapshot_id=job.snapshot_id,
            attempt_id=job.attempt_id,
            test_bundle_hash=job.test_bundle_hash,
            image_digest="sha256:" + "0" * 57,
            status=self._default_status,
            exit_code=self._default_exit_code,
            collect_result={"collected": len(bundle.files), "errors": 0},
            test_result={"passed": len(bundle.files), "failed": 0},
            raw_coverage_reports=[],
        )
