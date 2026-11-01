import difflib
from dataclasses import replace

import pytest

from app.services.parsing import (
    ContextBuilder,
    GitHubInput,
    InputError,
    InputLimits,
    SourceFile,
    analyze_code,
    extract_document,
)
from app.services.parsing.code import snapshot_hash
from app.services.parsing.diff import parse_diff


def make_diff(before, after, old="sample.py", new="sample.py"):
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{old}",
            tofile=f"b/{new}",
        )
    )


def test_diff_and_ast_select_changed_function(environment):
    before = "def double(value):\n    return value\n\ndef unrelated():\n    return 42\n"
    after = before.replace("return value\n", "return value * 2\n")
    diff = make_diff(before, after)
    result = ContextBuilder().build(
        run_id="run",
        snapshot_id="snapshot",
        files=(SourceFile("sample.py", after),),
        environment=environment,
        mode="lazy",
        github=GitHubInput("repo", "a" * 40, "b" * 40, diff),
    )
    assert result.changes[0].added_lines == (2,)
    code = next(source for source in result.context.sources if source.kind == "code")
    assert code.text == "def double(value):\n    return value * 2\n"
    assert result.context.changed_lines == ()
    assert result.measurement_paths == ("sample.py",)
    assert result.context.snapshot.source_sha == "a" * 40


def test_ast_decorators_imports_and_async():
    code = "import math\n\n@decorator\nasync def calculate(value):\n    return math.sqrt(value)\n"
    result = analyze_code(SourceFile("sample.py", code), [5])
    assert result.symbols[0].start_line == 3
    assert result.symbols[0].kind == "AsyncFunctionDef"
    assert result.fragment == code


def test_upload_protects_all_files_and_measures_application_only(environment):
    files = (
        SourceFile("empty.py", ""),
        SourceFile("src/app.py", "value = 2\n"),
        SourceFile("tests/test_app.py", "def test_value():\n    assert 1 == 1\n"),
    )
    result = ContextBuilder().build(
        run_id="r",
        snapshot_id="s",
        files=files,
        environment=environment,
        documents=(extract_document("spec.md", b"A value is two."),),
    )
    assert result.measurement_paths == ("empty.py", "src/app.py")
    assert set(result.context.protected_paths) == {file.path for file in files}
    assert any(source.kind == "existing_test" for source in result.context.sources)


@pytest.mark.parametrize("mode,documents", [("default", ()), ("lazy", ())])
def test_upload_requires_requirements_and_default(environment, mode, documents):
    with pytest.raises(InputError, match="invalid_generation_context"):
        ContextBuilder().build(
            run_id="r",
            snapshot_id="s",
            environment=environment,
            files=(SourceFile("app.py", "x = 1\n"),),
            mode=mode,
            documents=documents,
            description="Description only",
        )


def test_hash_is_order_independent_and_content_sensitive():
    first = SourceFile("first.py", "x = 1\n")
    second = SourceFile("second.py", "x = 2\n")
    assert snapshot_hash((first, second)) == snapshot_hash((second, first))
    assert snapshot_hash((first,)) != snapshot_hash((second,))
    assert snapshot_hash((first,)) != snapshot_hash((first,), "diff")


def test_diff_rejects_wrong_snapshot_missing_file_and_truncation():
    diff = make_diff("x = 1\n", "x = 2\n")
    for files, code in [
        ((SourceFile("sample.py", "x = 3\n"),), "diff_snapshot_mismatch"),
        ((), "diff_source_missing"),
    ]:
        with pytest.raises(InputError, match=code):
            parse_diff(diff, files)
    with pytest.raises(InputError, match="invalid_diff"):
        parse_diff(diff.rsplit("+x = 2", 1)[0], (SourceFile("sample.py", "x = 2\n"),))


def test_diff_deletion_and_rename():
    deleted = "--- a/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x = 1\n"
    assert parse_diff(deleted, ())[0].deleted
    renamed = (
        "diff --git a/old.py b/new.py\nsimilarity index 100%\n"
        "rename from old.py\nrename to new.py\n"
    )
    result = parse_diff(renamed, (SourceFile("new.py", "x = 1\n"),))
    assert result[0].old_path == "old.py"
    assert result[0].path == "new.py"
    assert result[0].added_lines == ()


@pytest.mark.parametrize(
    "diff",
    [
        "garbage",
        "diff --cc a.py\n@@@ x",
        "--- a/../a.py\n+++ b/../a.py\n@@ -1 +1 @@\n-a\n+b\n",
    ],
)
def test_invalid_diff_inputs(diff):
    with pytest.raises(InputError):
        parse_diff(diff, ())


def test_source_is_never_executed(tmp_path):
    sentinel = tmp_path / "unsafe"
    analyze_code(SourceFile("sample.py", f"open({str(sentinel)!r}, 'w')\n"))
    assert not sentinel.exists()


def test_invalid_python_and_limits():
    with pytest.raises(InputError, match="invalid_python"):
        analyze_code(SourceFile("sample.py", "def invalid(:"))
    with pytest.raises(InputError, match="source_size_limit"):
        analyze_code(
            SourceFile("sample.py", "x = 1"),
            limits=replace(InputLimits(), source_bytes=3),
        )
    with pytest.raises(InputError, match="ast_node_limit"):
        analyze_code(SourceFile("sample.py", "x = 1"), limits=replace(InputLimits(), ast_nodes=1))
    with pytest.raises(InputError, match="changed_line_out_of_range"):
        analyze_code(SourceFile("sample.py", "x = 1"), [2])


def test_generated_directory_must_be_vacant(environment):
    with pytest.raises(InputError, match="generated_directory_occupied"):
        ContextBuilder().build(
            run_id="r",
            snapshot_id="s",
            environment=environment,
            files=(SourceFile("generated_tests/test_existing.py", "x = 1"),),
        )


def test_changed_function_keeps_transitive_module_dependencies():
    code = (
        "FACTOR = 2\n\ndef double(value):\n    return helper(value)\n\n"
        "def helper(value):\n    return value * FACTOR\n"
    )
    result = analyze_code(SourceFile("sample.py", code), [4])
    assert result.fragment == code


def test_ast_line_numbers_ignore_unicode_line_separators():
    code = 'LABEL = "first\u2028second"\n\ndef double(value):\n    return value * 2\n'
    result = analyze_code(SourceFile("sample.py", code), [4])
    assert "    return value * 2\n" in result.fragment


def test_deletion_only_github_snapshot(environment):
    diff = "--- a/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x = 1\n"
    result = ContextBuilder().build(
        run_id="r",
        snapshot_id="s",
        environment=environment,
        files=(),
        mode="lazy",
        github=GitHubInput("repo", "a" * 40, "b" * 40, diff),
    )
    assert result.measurement_paths == ()
    assert result.changes[0].deleted
    assert result.context.sources[0].text == diff


def test_diff_handles_unicode_separators_in_python_strings():
    before = 'label = "first\u2028second"\n'
    after = before.replace("second", "third")
    diff = "--- a/sample.py\n+++ b/sample.py\n@@ -1 +1 @@\n-" + before + "+" + after
    assert parse_diff(diff, (SourceFile("sample.py", after),))[0].added_lines == (1,)


def test_diff_rejects_overlapping_original_hunks():
    diff = "--- a/sample.py\n+++ b/sample.py\n@@ -1 +1 @@\n-a\n+x\n@@ -1 +2 @@\n-a\n+y\n"
    with pytest.raises(InputError, match="overlapping_diff_hunks"):
        parse_diff(diff, (SourceFile("sample.py", "x\ny\n"),))


def test_unpaired_surrogates_are_reported_as_input_errors(environment):
    from app.services.parsing.paths import safe_path

    with pytest.raises(InputError, match="invalid_text_encoding"):
        analyze_code(SourceFile("sample.py", "value = '\ud800'\n"))
    with pytest.raises(InputError, match="invalid_path"):
        safe_path("\ud800.py")
    with pytest.raises(InputError, match="invalid_text_encoding"):
        ContextBuilder().build(
            run_id="r",
            snapshot_id="s",
            files=(SourceFile("sample.py", "value = 2\n"),),
            environment=environment,
            description="\ud800",
        )


def test_empty_upload_remains_invalid(environment):
    with pytest.raises(InputError, match="source_file_limit"):
        ContextBuilder().build(run_id="r", snapshot_id="s", files=(), environment=environment)


def test_recursive_dependencies_and_crlf_preserve_exact_source():
    code = (
        "def first(value):\r\n    return second(value - 1) if value else 0\r\n\r\n"
        "def second(value):\r\n    return first(value - 1) if value else 0\r\n"
    )
    assert analyze_code(SourceFile("sample.py", code), [2]).fragment == code
