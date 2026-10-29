import ast
import io
import tokenize
from dataclasses import dataclass

from app.services.llm.feedback import ValidationIssue
from app.services.parsing.code import parse_python
from app.services.parsing.errors import InputError
from app.services.parsing.limits import InputLimits


@dataclass(frozen=True, slots=True)
class StaticPolicy:
    allowed_imports: frozenset[str] = frozenset(
        {
            "pytest",
            "math",
            "decimal",
            "fractions",
            "datetime",
            "collections",
            "itertools",
            "functools",
            "operator",
            "re",
            "string",
            "statistics",
            "enum",
            "dataclasses",
            "typing",
        }
    )
    max_files: int = 100
    max_bytes: int = 2_000_000
    max_scenarios: int = 200
    max_issues: int = 100

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value <= 0
            for value in (self.max_files, self.max_bytes, self.max_scenarios, self.max_issues)
        ):
            raise ValueError("policy limits must be positive integers")


DEFAULT_POLICY = StaticPolicy()
FORBIDDEN_IMPORTS = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "socket",
        "requests",
        "httpx",
        "urllib",
        "http",
        "ftplib",
        "pathlib",
        "shutil",
        "tempfile",
        "ctypes",
        "multiprocessing",
        "threading",
        "importlib",
        "builtins",
        "pickle",
        "marshal",
        "coverage",
        "runpy",
        "pty",
        "resource",
        "signal",
    }
)
FORBIDDEN_NAMES = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "open",
        "__import__",
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "vars",
        "breakpoint",
        "input",
        "__builtins__",
        "__test__",
        "pytest_plugins",
    }
)
FORBIDDEN_PYTEST = (
    "pytest.skip",
    "pytest.xfail",
    "pytest.mark.skip",
    "pytest.mark.xfail",
    "pytest.exit",
    "pytest.main",
    "pytest.importorskip",
)
FUNCTION_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _name(node: ast.AST, aliases: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return f"{_name(node.value, aliases)}.{node.attr}"
    return ""


def _literal(node: ast.AST) -> bool:
    try:
        ast.literal_eval(node)
        return True
    except (ValueError, TypeError, SyntaxError, RecursionError):
        return False


class _Inspector:
    def __init__(self, path: str, allowed_imports: frozenset[str], max_issues: int) -> None:
        self.path = path
        self.allowed_imports = allowed_imports - FORBIDDEN_IMPORTS
        self.max_issues = max_issues
        self.issues: list[ValidationIssue] = []
        self.aliases: dict[str, str] = {}

    def report(self, code: str, node: ast.AST | None = None, line: int | None = None) -> None:
        if len(self.issues) < self.max_issues:
            self.issues.append(
                ValidationIssue(
                    kind="static",
                    message=code,
                    path=self.path,
                    line=line or getattr(node, "lineno", None),
                )
            )

    def imports(self, nodes: tuple[ast.AST, ...]) -> None:
        for node in nodes:
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            module = node.module or "" if isinstance(node, ast.ImportFrom) else ""
            if isinstance(node, ast.ImportFrom) and (
                node.level or any(alias.name == "*" for alias in node.names)
            ):
                self.report("relative_or_star_import", node)
            for alias in node.names:
                target = f"{module}.{alias.name}" if module else alias.name
                root = target.split(".")[0]
                if root not in self.allowed_imports:
                    self.report("import_not_allowed", node)
                key = alias.asname or (alias.name if module else root)
                self.aliases[key] = target if module or alias.asname else root

    def inspect_node(self, node: ast.AST) -> None:
        if isinstance(node, ast.Name) and (
            node.id in FORBIDDEN_NAMES or node.id.startswith("pytest_")
        ):
            self.report("forbidden_name", node)
        if isinstance(node, ast.Attribute) and (
            node.attr.startswith("__")
            or node.attr in {"setattr", "delattr", "setenv", "delenv", "syspath_prepend"}
        ):
            self.report("forbidden_attribute", node)
        resolved = _name(node, self.aliases)
        if any(resolved == name or resolved.startswith(name + ".") for name in FORBIDDEN_PYTEST):
            self.report("test_suppression", node)
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            self.report("global_mutation", node)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete)):
            targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
            if any(isinstance(target, (ast.Attribute, ast.Subscript)) for target in targets):
                self.report("external_mutation", node)
        if isinstance(node, ast.Assert) and not any(
            isinstance(item, (ast.Name, ast.Call, ast.Attribute, ast.Subscript))
            for item in ast.walk(node.test)
        ):
            self.report("constant_assertion", node)
        if isinstance(node, ast.ExceptHandler) and (
            node.type is None
            or any(
                _name(item, self.aliases) in {"AssertionError", "Exception", "BaseException"}
                for item in ast.walk(node.type)
            )
        ):
            self.report("assertion_suppression", node)
        if isinstance(node, (ast.Try, ast.TryStar)) and any(
            isinstance(item, (ast.Return, ast.Break, ast.Continue))
            for statement in node.finalbody
            for item in ast.walk(statement)
        ):
            self.report("assertion_suppression", node)
        if isinstance(node, FUNCTION_TYPES):
            self.function(node)
        if isinstance(node, ast.AnnAssign):
            self.annotation(node.annotation)

    def annotation(self, annotation: ast.AST | None) -> None:
        if annotation is not None and any(
            isinstance(item, ast.Call) for item in ast.walk(annotation)
        ):
            self.report("annotation_execution", annotation)

    def function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if node.name.startswith("pytest_") or node.name in {"__init__", "__new__"}:
            self.report("pytest_hook_or_constructor", node)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None and not _literal(default):
                self.report("nonliteral_default", node)
        for argument in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
            node.args.vararg,
            node.args.kwarg,
        ):
            if argument is not None:
                self.annotation(argument.annotation)
        self.annotation(node.returns)
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if _name(target, self.aliases) not in {"pytest.fixture", "pytest.mark.parametrize"}:
                self.report("decorator_not_allowed", decorator)
            if isinstance(decorator, ast.Call) and (
                any(not _literal(arg) for arg in decorator.args)
                or any(
                    keyword.arg is None or not _literal(keyword.value)
                    for keyword in decorator.keywords
                )
            ):
                self.report("nonliteral_decorator", decorator)

    def collection(self, tree: ast.Module) -> None:
        tests = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                if (
                    not node.name.startswith("Test")
                    or node.bases
                    or node.keywords
                    or node.decorator_list
                ):
                    self.report("class_not_allowed", node)
                body = node.body
            else:
                body = [node]
            for item in body:
                if isinstance(item, FUNCTION_TYPES):
                    if item.name.startswith("test_"):
                        tests.append(item)
                elif isinstance(item, (ast.Assign, ast.AnnAssign)):
                    if item.value is None or not _literal(item.value):
                        self.report("module_execution", item)
                elif (
                    isinstance(item, ast.Expr)
                    and isinstance(item.value, ast.Constant)
                    and isinstance(item.value.value, str)
                ):
                    continue
                elif not isinstance(item, (ast.Import, ast.ImportFrom)):
                    self.report("module_execution", item)
        if not tests:
            self.report("no_collectable_tests")
        for test in tests:
            if not self.has_assertion(test):
                self.report("test_has_no_assertion", test)

    def has_assertion(self, test: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        pending = list(test.body)
        while pending:
            node = pending.pop()
            if isinstance(node, (*FUNCTION_TYPES, ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(node, ast.Assert) or (
                isinstance(node, ast.Call) and _name(node.func, self.aliases) == "pytest.raises"
            ):
                return True
            pending.extend(ast.iter_child_nodes(node))
        return False


def validate_python(
    path: str,
    content: str,
    allowed_imports: frozenset[str],
    limits: InputLimits,
    max_issues: int,
) -> tuple[ValidationIssue, ...]:
    try:
        tree = parse_python(content, limits)
    except InputError as error:
        return (ValidationIssue(kind="syntax", message=error.code, path=path),)
    inspector = _Inspector(path, allowed_imports, max_issues)
    nodes = tuple(ast.walk(tree))
    inspector.imports(nodes)
    for node in nodes:
        inspector.inspect_node(node)
    for token in tokenize.generate_tokens(io.StringIO(content).readline):
        if token.type == tokenize.COMMENT and any(
            marker in token.string.lower() for marker in ("pragma:", "no cover", "no branch")
        ):
            inspector.report("coverage_exclusion", line=token.start[0])
    inspector.collection(tree)
    return tuple(inspector.issues)
