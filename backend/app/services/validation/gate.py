from dataclasses import dataclass

from app.services.llm.bundles import TestBundle, TestBundleDraft
from app.services.llm.contracts import GenerationContext
from app.services.llm.feedback import ValidationFeedback, ValidationIssue
from app.services.llm.scenarios import ScenarioSet
from app.services.parsing.errors import InputError
from app.services.parsing.limits import DEFAULT_LIMITS, InputLimits
from app.services.parsing.paths import safe_path, unique_paths

from .gherkin import render_feature
from .python import DEFAULT_POLICY, StaticPolicy, validate_python


@dataclass(frozen=True, slots=True, repr=False)
class GateResult:
    run_id: str
    snapshot_id: str
    bundle_hash: str
    issues: tuple[ValidationIssue, ...]

    @property
    def passed(self) -> bool:
        return not self.issues

    def feedback(self, iteration: int) -> ValidationFeedback:
        if self.passed:
            raise InputError("successful_validation_has_no_feedback")
        return ValidationFeedback(
            schema_version="1",
            run_id=self.run_id,
            snapshot_id=self.snapshot_id,
            bundle_hash=self.bundle_hash,
            iteration=iteration,
            reason="validation_error",
            issues=self.issues,
        )


class QualityGate:
    def __init__(
        self, policy: StaticPolicy = DEFAULT_POLICY, limits: InputLimits = DEFAULT_LIMITS
    ) -> None:
        self._policy = policy
        self._limits = limits

    def validate(
        self, context: GenerationContext, scenarios: ScenarioSet, bundle: TestBundle
    ) -> GateResult:
        if (
            len(bundle.files) > self._policy.max_files
            or len(scenarios.scenarios) > self._policy.max_scenarios
            or sum(len(file.content.encode("utf-8")) for file in bundle.files)
            > self._policy.max_bytes
            or len(scenarios.model_dump_json().encode("utf-8")) > self._policy.max_bytes
        ):
            raise InputError("artifact_size_limit")
        try:
            context = GenerationContext.model_validate(context.model_dump())
            scenarios = ScenarioSet.model_validate(scenarios.model_dump())
            bundle = TestBundle.model_validate(bundle.model_dump())
        except ValueError:
            raise InputError("invalid_artifact_contract") from None
        identity = (context.run_id, context.snapshot.snapshot_id)
        if (scenarios.run_id, scenarios.snapshot_id) != identity or (
            bundle.run_id,
            bundle.snapshot_id,
        ) != identity:
            raise InputError("artifact_identity_mismatch")
        issues: list[ValidationIssue] = []
        try:
            safe_path(context.generated_tests_dir)
            unique_paths([file.path for file in bundle.files])
            source_paths = tuple(
                source.path
                for source in context.sources
                if source.path is not None and source.kind in {"code", "existing_test"}
            )
            protected = tuple(set((*context.protected_paths, *source_paths)))
            for path in protected:
                safe_path(path)
            checked_context = context.model_copy(update={"protected_paths": protected})
            TestBundle.from_context(TestBundleDraft(files=bundle.files), checked_context, scenarios)
            render_feature(context, scenarios)
        except ValueError as error:
            code = error.code if isinstance(error, InputError) else "invalid_artifact_relationship"
            issues.append(ValidationIssue(kind="static", message=code))
        imports = self._policy.allowed_imports | self._application_imports(context)
        for file in bundle.files:
            if len(issues) >= self._policy.max_issues:
                break
            filename = file.path.rsplit("/", 1)[-1]
            if not filename.startswith("test_") or filename in {"test_.py"}:
                issues.append(
                    ValidationIssue(kind="static", message="invalid_test_filename", path=file.path)
                )
                continue
            issues.extend(
                validate_python(
                    file.path,
                    file.content,
                    imports,
                    self._limits,
                    self._policy.max_issues - len(issues),
                )
            )
        return GateResult(*identity, bundle.bundle_hash, tuple(issues))

    @staticmethod
    def _application_imports(context: GenerationContext) -> frozenset[str]:
        roots: set[str] = set()
        for source in context.sources:
            if source.kind == "code" and source.path:
                parts = safe_path(source.path)
                if parts[0] == "src" and len(parts) > 1:
                    parts = parts[1:]
                root = parts[0].removesuffix(".py")
                if root.isidentifier():
                    roots.add(root)
        return frozenset(roots)
