import re
from dataclasses import dataclass

from unidiff import PatchSet
from unidiff.errors import UnidiffParseError

from .code import SourceFile
from .errors import InputError
from .limits import DEFAULT_LIMITS, InputLimits
from .paths import safe_path, unique_paths


@dataclass(frozen=True, slots=True)
class FileChange:
    path: str
    old_path: str | None
    added_lines: tuple[int, ...]
    deleted: bool


def _patch_path(value: str) -> str | None:
    if value == "/dev/null":
        return None
    path = value[2:] if value.startswith(("a/", "b/")) else value
    safe_path(path)
    return path


def _validate_envelope(diff: str) -> None:
    remaining_old = remaining_new = 0
    metadata = (
        "diff --git ",
        "index ",
        "--- ",
        "+++ ",
        "new file mode ",
        "deleted file mode ",
        "old mode ",
        "new mode ",
        "similarity index ",
        "dissimilarity index ",
        "rename from ",
        "rename to ",
        "copy from ",
        "copy to ",
    )
    for line in diff.splitlines():
        if line == "\\ No newline at end of file":
            continue
        if remaining_old or remaining_new:
            prefix = line[:1]
            if prefix not in {" ", "+", "-"}:
                raise InputError("invalid_diff")
            remaining_old -= prefix in {" ", "-"}
            remaining_new -= prefix in {" ", "+"}
            if remaining_old < 0 or remaining_new < 0:
                raise InputError("invalid_diff")
        elif line.startswith("@@"):
            match = re.fullmatch(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@.*", line)
            if match is None:
                raise InputError("invalid_diff")
            remaining_old = int(match[2] or 1)
            remaining_new = int(match[4] or 1)
        elif not line.startswith(metadata):
            raise InputError("unsupported_diff")
    if remaining_old or remaining_new:
        raise InputError("invalid_diff")


def parse_diff(
    diff: str, files: tuple[SourceFile, ...], limits: InputLimits = DEFAULT_LIMITS
) -> tuple[FileChange, ...]:
    if not diff.strip() or len(diff.encode("utf-8")) > limits.total_source_bytes:
        raise InputError("diff_size_limit")
    if any(
        line.startswith(("diff --cc ", "diff --combined ", "@@@")) for line in diff.splitlines()
    ):
        raise InputError("unsupported_diff")
    _validate_envelope(diff)
    try:
        patch = PatchSet(diff)
    except (UnidiffParseError, ValueError, UnboundLocalError, AttributeError):
        raise InputError("invalid_diff") from None
    if not patch or len(patch) > limits.source_files:
        raise InputError("invalid_diff")
    contents = {file.path: file.content.splitlines() for file in files}
    changes: list[FileChange] = []
    for item in patch:
        old_path = _patch_path(item.source_file)
        new_path = _patch_path(item.target_file)
        path = new_path or old_path
        if path is None or item.is_binary_file:
            raise InputError("unsupported_diff")
        if path.endswith(".py") and new_path is not None and path not in contents:
            raise InputError("diff_source_missing")
        if new_path is None and path in contents:
            raise InputError("diff_snapshot_mismatch")
        added: list[int] = []
        previous_end = 0
        for hunk in item:
            if hunk.target_start < previous_end:
                raise InputError("overlapping_diff_hunks")
            previous_end = hunk.target_start + hunk.target_length
            for line in hunk:
                if line.is_added:
                    added.append(line.target_line_no)
                if new_path in contents and (line.is_added or line.is_context):
                    number = line.target_line_no
                    current = contents[new_path]
                    if (
                        number is None
                        or not 1 <= number <= len(current)
                        or (current[number - 1] != line.value.rstrip("\r\n"))
                    ):
                        raise InputError("diff_snapshot_mismatch")
        changes.append(FileChange(path, old_path, tuple(added), new_path is None))
    unique_paths([change.path for change in changes])
    return tuple(changes)
