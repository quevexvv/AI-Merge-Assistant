from dataclasses import dataclass

from app.services.llm.contracts import (
    EnvironmentProfile,
    GenerationContext,
    GitHubSnapshot,
    Mode,
    PreparedSource,
    UploadSnapshot,
)

from .code import CodeAnalysis, SourceFile, analyze_code, snapshot_hash, utf8_bytes, validate_files
from .diff import FileChange, parse_diff
from .documents import DocumentText
from .errors import InputError
from .limits import DEFAULT_LIMITS, InputLimits
from .paths import safe_path, unique_paths


@dataclass(frozen=True, slots=True, repr=False)
class GitHubInput:
    repository_id: str
    source_sha: str
    target_sha: str
    diff: str


@dataclass(frozen=True, slots=True, repr=False)
class PreparedInput:
    context: GenerationContext
    changes: tuple[FileChange, ...]
    analysis: tuple[CodeAnalysis, ...]
    measurement_paths: tuple[str, ...]


class ContextBuilder:
    def __init__(self, limits: InputLimits = DEFAULT_LIMITS) -> None:
        self._limits = limits

    def build(
        self,
        *,
        run_id: str,
        snapshot_id: str,
        files: tuple[SourceFile, ...],
        environment: EnvironmentProfile,
        documents: tuple[DocumentText, ...] = (),
        description: str | None = None,
        github: GitHubInput | None = None,
        mode: Mode = "default",
        generated_tests_dir: str = "generated_tests",
        protected_paths: tuple[str, ...] = (),
    ) -> PreparedInput:
        validate_files(files, self._limits, allow_empty=github is not None)
        root = safe_path(generated_tests_dir)
        protected = tuple(sorted({*protected_paths, *(file.path for file in files)}))
        for path in protected:
            safe_path(path)
        if any(
            tuple(part.casefold() for part in safe_path(file.path))[: len(root)]
            == tuple(part.casefold() for part in root)
            for file in files
        ):
            raise InputError("generated_directory_occupied")
        if len(documents) > self._limits.source_files:
            raise InputError("document_count_limit")
        unique_paths([document.path for document in documents])
        sources: list[PreparedSource] = []
        for document in sorted(documents, key=lambda item: item.path):
            if not document.text.strip() or len(document.text) > self._limits.text_characters:
                raise InputError("document_text_limit")
            utf8_bytes(document.text)
            sources.append(
                PreparedSource(
                    source_id=f"requirements:{document.path}",
                    kind="requirements",
                    path=document.path,
                    text=document.text,
                )
            )
        if description is not None:
            if not description.strip() or len(description) > self._limits.text_characters:
                raise InputError("description_limit")
            utf8_bytes(description)
            sources.append(
                PreparedSource(source_id="description", kind="description", text=description)
            )
        changes = parse_diff(github.diff, files, self._limits) if github else ()
        changes_by_path = {change.path: change for change in changes}
        if github:
            sources.append(PreparedSource(source_id="diff", kind="diff", text=github.diff))
        analysis: list[CodeAnalysis] = []
        measurement_paths: list[str] = []
        for file in sorted(files, key=lambda item: item.path):
            change = changes_by_path.get(file.path)
            code = analyze_code(file, change.added_lines if change else None, self._limits)
            analysis.append(code)
            if not file.is_test and (github is None or change is not None):
                measurement_paths.append(file.path)
            if code.fragment.strip():
                sources.append(
                    PreparedSource(
                        source_id=f"code:{file.path}",
                        kind="existing_test" if file.is_test else "code",
                        text=code.fragment,
                        path=file.path,
                        start_line=code.start_line,
                        optional=github is not None and change is None,
                    )
                )
        if (
            sum(len(utf8_bytes(source.text)) for source in sources)
            > self._limits.total_source_bytes
        ):
            raise InputError("context_size_limit")
        content_hash = snapshot_hash(files, github.diff if github else None)
        try:
            snapshot = (
                GitHubSnapshot(
                    kind="github",
                    snapshot_id=snapshot_id,
                    content_hash=content_hash,
                    repository_id=github.repository_id,
                    source_sha=github.source_sha,
                    target_sha=github.target_sha,
                )
                if github
                else UploadSnapshot(
                    kind="upload", snapshot_id=snapshot_id, content_hash=content_hash
                )
            )
            context = GenerationContext(
                schema_version="1",
                run_id=run_id,
                snapshot=snapshot,
                mode=mode,
                sources=tuple(sources),
                environment=environment,
                generated_tests_dir=generated_tests_dir,
                protected_paths=protected,
            )
        except ValueError:
            raise InputError("invalid_generation_context") from None
        return PreparedInput(context, changes, tuple(analysis), tuple(measurement_paths))
