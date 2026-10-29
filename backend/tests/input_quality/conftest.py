import pytest
from app.services.llm.bundles import GeneratedFile, TestBundle, TestBundleDraft
from app.services.llm.contracts import EnvironmentProfile
from app.services.llm.scenarios import Scenario, ScenarioDraftSet, ScenarioSet
from app.services.parsing import ContextBuilder, SourceFile, extract_document


@pytest.fixture
def environment():
    return EnvironmentProfile(
        profile_id="python-pytest", python_version="3.13", dependencies=("pytest",)
    )


@pytest.fixture
def context(environment):
    return (
        ContextBuilder()
        .build(
            run_id="run-1",
            snapshot_id="snapshot-1",
            environment=environment,
            files=(SourceFile("calculator.py", "def double(value):\n    return value * 2\n"),),
            documents=(extract_document("requirements.md", b"Double every integer."),),
        )
        .context
    )


@pytest.fixture
def scenarios(context):
    return ScenarioSet.from_context(
        ScenarioDraftSet(
            scenarios=(
                Scenario(
                    scenario_id="double-integers",
                    given=("An integer",),
                    when=("It is doubled",),
                    then=("The result equals twice the input",),
                    source_ids=("requirements:requirements.md", "code:calculator.py"),
                ),
            )
        ),
        context,
    )


@pytest.fixture
def bundle_factory(context, scenarios):
    def build(
        content="from calculator import double\n\ndef test_double():\n    assert double(3) == 6\n",
        path="generated_tests/test_calculator.py",
    ):
        draft = TestBundleDraft(
            files=(GeneratedFile(path=path, content=content, scenario_ids=("double-integers",)),)
        )
        return TestBundle.from_context(draft, context, scenarios)

    return build
