import pytest
from app.services.llm.bundles import TestBundle as Bundle
from app.services.llm.bundles import compute_bundle_hash
from app.services.parsing import InputError
from app.services.validation import QualityGate
from app.services.validation.gherkin import render_feature


def test_valid_bundle_and_feedback(context, scenarios, bundle_factory):
    result = QualityGate().validate(context, scenarios, bundle_factory())
    assert result.passed
    with pytest.raises(InputError, match="successful_validation_has_no_feedback"):
        result.feedback(1)
    bundle = bundle_factory("def test_invalid(:\n    pass\n")
    rejected = QualityGate().validate(context, scenarios, bundle)
    feedback = rejected.feedback(1)
    assert feedback.reason == "validation_error"
    assert feedback.bundle_hash == bundle.bundle_hash
    assert feedback.validate_for(context, bundle) is feedback
    assert feedback.issues[0].kind == "syntax"


@pytest.mark.parametrize(
    "code,expected",
    [
        (
            "import subprocess\n\ndef test_x():\n    assert subprocess.run(['id'])\n",
            "import_not_allowed",
        ),
        ("def test_x():\n    exec('x=1')\n    assert 1 == 1\n", "forbidden_name"),
        ("def test_x():\n    assert True\n", "constant_assertion"),
        ("def test_x():\n    pass\n", "test_has_no_assertion"),
        (
            "import pytest as p\n@p.mark.skip\ndef test_x():\n    assert 1 == 1\n",
            "test_suppression",
        ),
        (
            "from pytest import xfail as ignore\ndef test_x():\n    ignore()\n    assert 1 == 1\n",
            "test_suppression",
        ),
        (
            "def test_x():\n    assert 1 == 1  # pragma: no cover\n",
            "coverage_exclusion",
        ),
        (
            "pytest_plugins = ['evil']\ndef test_x():\n    assert 1 == 1\n",
            "forbidden_name",
        ),
        (
            "from calculator import double\nx = double(1)\ndef test_x():\n    assert x == 2\n",
            "module_execution",
        ),
        (
            "from calculator import double\ndef test_x(x=double(1)):\n    assert x == 2\n",
            "nonliteral_default",
        ),
        ("def test_x():\n    assert (1).__class__\n", "forbidden_attribute"),
        (
            "def test_x(monkeypatch):\n    monkeypatch.setenv('X', '1')\n    assert 1 == 1\n",
            "forbidden_attribute",
        ),
        ("def test_x():\n    global value\n    assert 1 == 1\n", "global_mutation"),
        (
            "import calculator\ndef test_x():\n    calculator.double = 1\n    assert 1 == 1\n",
            "external_mutation",
        ),
        (
            "from calculator import *\ndef test_x():\n    assert 1 == 1\n",
            "relative_or_star_import",
        ),
        (
            "class TestBad(object):\n    def test_x(self):\n        assert 1 == 1\n",
            "class_not_allowed",
        ),
        ("def helper():\n    assert 1 == 1\n", "no_collectable_tests"),
    ],
)
def test_static_rejections(context, scenarios, bundle_factory, code, expected):
    result = QualityGate().validate(context, scenarios, bundle_factory(code))
    assert not result.passed
    assert expected in {issue.message for issue in result.issues}


def test_parametrize_fixture_class_and_exception_assertions(context, scenarios, bundle_factory):
    code = (
        "import pytest\nfrom calculator import double\n\n"
        "@pytest.fixture\ndef value():\n    return 3\n\n"
        "@pytest.mark.parametrize('number,expected', [(1, 2), (-1, -2)])\n"
        "def test_double(number, expected):\n    assert double(number) == expected\n\n"
        "class TestErrors:\n    def test_error(self):\n"
        "        with pytest.raises(TypeError):\n            double(None)\n"
    )
    assert QualityGate().validate(context, scenarios, bundle_factory(code)).passed


def test_stale_ids_and_forged_hash_are_not_repairable(context, scenarios, bundle_factory):
    bundle = bundle_factory()
    with pytest.raises(InputError, match="artifact_identity_mismatch"):
        QualityGate().validate(
            context, scenarios, bundle.model_copy(update={"snapshot_id": "other"})
        )
    with pytest.raises(InputError, match="invalid_artifact_contract"):
        QualityGate().validate(
            context, scenarios, bundle.model_copy(update={"bundle_hash": "a" * 64})
        )


def test_protected_source_is_enforced_even_if_context_list_is_missing(
    context, scenarios, bundle_factory
):
    bundle = bundle_factory()
    files = (bundle.files[0].model_copy(update={"path": "generated_tests/test_original.py"}),)
    bundle = Bundle(
        **{
            **bundle.model_dump(),
            "files": files,
            "bundle_hash": compute_bundle_hash(files),
        }
    )
    source = context.sources[-1].model_copy(update={"path": "generated_tests/test_original.py"})
    context = context.model_copy(
        update={"protected_paths": (), "sources": (*context.sources[:-1], source)}
    )
    assert not QualityGate().validate(context, scenarios, bundle).passed


@pytest.mark.parametrize("update", [{"expectation_origin": "inferred"}, {"run_id": "other"}])
def test_scenario_lineage(context, scenarios, bundle_factory, update):
    changed = scenarios.model_copy(update=update)
    if "run_id" in update:
        with pytest.raises(InputError, match="artifact_identity_mismatch"):
            QualityGate().validate(context, changed, bundle_factory())
    else:
        assert not QualityGate().validate(context, changed, bundle_factory()).passed


def test_gherkin_injection_and_missing_requirements(context, scenarios):
    first = scenarios.scenarios[0]
    for update, code in [
        ({"then": ("Expected\n  Scenario: injected",)}, "invalid_gherkin_text"),
        ({"source_ids": ("code:calculator.py",)}, "requirements_source_missing"),
        ({"source_ids": ("unknown",)}, "unknown_scenario_source"),
    ]:
        changed = scenarios.model_copy(update={"scenarios": (first.model_copy(update=update),)})
        with pytest.raises(InputError, match=code):
            render_feature(context, changed)


@pytest.mark.parametrize(
    "code,expected",
    [
        (
            "from calculator import double\ndef test_x(value: double(1)):\n    assert value == 2\n",
            "annotation_execution",
        ),
        ("def test_x():\n    def unused():\n        assert 1 == 1\n", "test_has_no_assertion"),
        (
            "import pytest\n@pytest.mark.parametrize('x', range(3))\n"
            "def test_x(x):\n    assert x > 0\n",
            "nonliteral_decorator",
        ),
        (
            "class TestBad:\n    def __init__(self):\n        pass\n"
            "    def test_x(self):\n        assert 1 == 1\n",
            "pytest_hook_or_constructor",
        ),
    ],
)
def test_definition_time_code_and_unused_assertions(
    context, scenarios, bundle_factory, code, expected
):
    result = QualityGate().validate(context, scenarios, bundle_factory(code))
    assert expected in {issue.message for issue in result.issues}


def test_artifact_limits_and_bounded_issues(context, scenarios, bundle_factory):
    from app.services.validation import StaticPolicy

    with pytest.raises(InputError, match="artifact_size_limit"):
        QualityGate(StaticPolicy(max_bytes=1)).validate(context, scenarios, bundle_factory())
    result = QualityGate(StaticPolicy(max_issues=1)).validate(
        context,
        scenarios,
        bundle_factory("import os\nimport sys\ndef test_x():\n    assert True\n"),
    )
    assert len(result.issues) == 1


def test_no_code_execution_during_validation(context, scenarios, bundle_factory, tmp_path):
    sentinel = tmp_path / "executed"
    content = f"open({str(sentinel)!r}, 'w')\ndef test_x():\n    assert 1 == 1\n"
    result = QualityGate().validate(context, scenarios, bundle_factory(content))
    assert not result.passed
    assert not sentinel.exists()


@pytest.mark.parametrize(
    "body,expected",
    [
        ("assert 1 == 1", "constant_assertion"),
        ("assert 2 * 3 == 6", "constant_assertion"),
        (
            "try:\n        assert double(2) == 5\n    except AssertionError:\n        pass",
            "assertion_suppression",
        ),
        (
            "try:\n        assert double(2) == 5\n"
            "    except (ValueError, Exception):\n        pass",
            "assertion_suppression",
        ),
        (
            "try:\n        assert double(2) == 5\n    except:\n        pass",
            "assertion_suppression",
        ),
        (
            "try:\n        assert double(2) == 5\n    finally:\n        return",
            "assertion_suppression",
        ),
    ],
)
def test_false_positive_test_results_are_rejected(
    context, scenarios, bundle_factory, body, expected
):
    code = f"from calculator import double\ndef test_double():\n    {body}\n"
    result = QualityGate().validate(context, scenarios, bundle_factory(code))
    assert expected in {issue.message for issue in result.issues}


def test_specific_exception_handler_keeps_assertion_failures_visible(
    context, scenarios, bundle_factory
):
    code = (
        "from calculator import double\ndef test_double():\n"
        "    try:\n        result = double(2)\n"
        "    except ValueError:\n        result = 0\n"
        "    assert result == 4\n"
    )
    assert QualityGate().validate(context, scenarios, bundle_factory(code)).passed
