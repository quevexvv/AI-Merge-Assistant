import ast
import hashlib
import io
import json
from bisect import bisect_left
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


def utf8_bytes(text: str) -> bytes:
    try:
        return text.encode("utf-8")
    except UnicodeError:
        raise InputError("invalid_text_encoding") from None


def parse_python(content: str, limits: InputLimits = DEFAULT_LIMITS) -> ast.Module:
    if len(utf8_bytes(content)) > limits.source_bytes:
        raise InputError("source_size_limit")
    try:
        tree = ast.parse(content, filename="<source>", feature_version=(3, 13))
    except (SyntaxError, ValueError, RecursionError):
        raise InputError("invalid_python") from None
    for count, _ in enumerate(ast.walk(tree), 1):
        if count > limits.ast_nodes:
            raise InputError("ast_node_limit")
    return tree


def validate_files(
    files: tuple[SourceFile, ...], limits: InputLimits, *, allow_empty: bool = False
) -> None:
    if (not files and not allow_empty) or len(files) > limits.source_files:
        raise InputError("source_file_limit")
    unique_paths([file.path for file in files])
    total = 0
    for file in files:
        if not file.path.endswith(".py"):
            raise InputError("unsupported_source")
        size = len(utf8_bytes(file.content))
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
    return hashlib.sha256(utf8_bytes(serialized)).hexdigest()


def _include_dependencies(tree: ast.Module, selected: set[int]) -> None:
    definitions: dict[str, set[int]] = {}
    references: list[set[str]] = []
    for index, node in enumerate(tree.body):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            definitions.setdefault(node.name, set()).add(index)
        else:
            selected.add(index)
        references.append({item.id for item in ast.walk(node) if isinstance(item, ast.Name)})
    pending = list(selected)
    visited_names: set[str] = set()
    while pending:
        for name in references[pending.pop()] - visited_names:
            visited_names.add(name)
            dependencies = definitions.get(name, set()) - selected
            selected.update(dependencies)
            pending.extend(dependencies)


def analyze_code(
    file: SourceFile,
    changed_lines: Iterable[int] | None = None,
    limits: InputLimits = DEFAULT_LIMITS,
) -> CodeAnalysis:
    safe_path(file.path)
    tree = parse_python(file.content, limits)
    lines = io.StringIO(file.content, newline="").readlines()
    changed = None if changed_lines is None else tuple(changed_lines)
    if changed is not None and any(
        type(line) is not int or not 1 <= line <= len(lines) for line in changed
    ):
        raise InputError("changed_line_out_of_range")
    changed = None if changed is None else sorted(set(changed))
    symbols: list[Symbol] = []
    ranges: list[tuple[int, int]] = []
    selected: set[int] = set()
    for index, node in enumerate(tree.body):
        decorators = getattr(node, "decorator_list", ())
        start = min([node.lineno, *(item.lineno for item in decorators)])
        end = node.end_lineno or node.lineno
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.append(Symbol(node.name, type(node).__name__, start, end))
        ranges.append((start, end))
        offset = bisect_left(changed, start) if changed is not None else 0
        if changed is None or (offset < len(changed) and changed[offset] <= end):
            selected.add(index)
    if not changed:
        return CodeAnalysis(file.path, tuple(symbols), file.content, 1)
    _include_dependencies(tree, selected)
    spans = [ranges[index] for index in selected]
    spans.append((changed[0], changed[-1]))
    start = min(value[0] for value in spans)
    end = max(value[1] for value in spans)
    return CodeAnalysis(file.path, tuple(symbols), "".join(lines[start - 1 : end]), start)
