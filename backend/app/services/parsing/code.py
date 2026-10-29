import ast
import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass

from .archives import read_archive
from .errors import InputError
from .limits import DEFAULT_LIMITS, InputLimits
from .paths import safe_path, unique_paths


@dataclass(frozen=True, slots=True, repr=False)
class SourceFile:
    path: str
    content: str

    @property
    def is_test(self) -> bool:
        parts = self.path.split("/")
        return (
            any(part in {"tests", "test"} for part in parts[:-1])
            or parts[-1].startswith("test_")
            or parts[-1].endswith("_test.py")
            or parts[-1] == "conftest.py"
        )


@dataclass(frozen=True, slots=True)
class Symbol:
    name: str
    kind: str
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True, repr=False)
class CodeAnalysis:
    path: str
    symbols: tuple[Symbol, ...]
    fragment: str
    start_line: int


def parse_python(content: str, limits: InputLimits = DEFAULT_LIMITS) -> ast.Module:
    if len(content.encode("utf-8")) > limits.source_bytes:
        raise InputError("source_size_limit")
    try:
        tree = ast.parse(content, filename="<source>", feature_version=(3, 13))
    except (SyntaxError, ValueError, RecursionError):
        raise InputError("invalid_python") from None
    for count, _ in enumerate(ast.walk(tree), 1):
        if count > limits.ast_nodes:
            raise InputError("ast_node_limit")
    return tree


def validate_files(files: tuple[SourceFile, ...], limits: InputLimits) -> None:
    if not files or len(files) > limits.source_files:
        raise InputError("source_file_limit")
    unique_paths([file.path for file in files])
    total = 0
    for file in files:
        if not file.path.endswith(".py"):
            raise InputError("unsupported_source")
        size = len(file.content.encode("utf-8"))
        total += size
        if size > limits.source_bytes or total > limits.total_source_bytes:
            raise InputError("source_size_limit")


def read_code_archive(data: bytes, limits: InputLimits = DEFAULT_LIMITS) -> tuple[SourceFile, ...]:
    entries = read_archive(data, limits)
    try:
        files = tuple(
            SourceFile(path, content.decode("utf-8-sig"))
            for path, content in sorted(entries.items())
        )
    except UnicodeError:
        raise InputError("invalid_text_encoding") from None
    validate_files(files, limits)
    return files


def snapshot_hash(files: tuple[SourceFile, ...], diff: str | None = None) -> str:
    payload = {
        "files": [[file.path, file.content] for file in sorted(files, key=lambda f: f.path)],
        "diff": diff,
    }
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def analyze_code(
    file: SourceFile,
    changed_lines: Iterable[int] | None = None,
    limits: InputLimits = DEFAULT_LIMITS,
) -> CodeAnalysis:
    safe_path(file.path)
    tree = parse_python(file.content, limits)
    lines = file.content.splitlines(keepends=True)
    changed = None if changed_lines is None else set(changed_lines)
    if changed is not None and any(
        type(line) is not int or not 1 <= line <= len(lines) for line in changed
    ):
        raise InputError("changed_line_out_of_range")
    symbols: list[Symbol] = []
    selected: list[tuple[int, int]] = []
    for node in tree.body:
        decorators = getattr(node, "decorator_list", ())
        start = min([node.lineno, *(item.lineno for item in decorators)])
        end = node.end_lineno or node.lineno
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.append(Symbol(node.name, type(node).__name__, start, end))
        if changed is None or any(start <= line <= end for line in changed):
            selected.append((start, end))
    if changed:
        selected.extend((line, line) for line in changed)
    if not selected:
        return CodeAnalysis(file.path, tuple(symbols), file.content, 1)
    selected.extend(
        (node.lineno, node.end_lineno or node.lineno)
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    )
    start = min(value[0] for value in selected)
    end = max(value[1] for value in selected)
    return CodeAnalysis(file.path, tuple(symbols), "".join(lines[start - 1 : end]), start)
