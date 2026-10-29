import unicodedata

from app.services.llm.contracts import GenerationContext
from app.services.llm.scenarios import ScenarioSet
from app.services.parsing.errors import InputError
from gherkin.parser import Parser


def _single_line(value: str) -> str:
    if any(unicodedata.category(char).startswith("C") or char in "\u2028\u2029" for char in value):
        raise InputError("invalid_gherkin_text")
    return value


def render_feature(context: GenerationContext, scenarios: ScenarioSet) -> str:
    expected_origin = "inferred" if context.mode == "lazy" else "requirements"
    if scenarios.expectation_origin != expected_origin:
        raise InputError("invalid_expectation_origin")
    sources = {source.source_id: source for source in context.sources}
    lines = [f"@{expected_origin}", "Feature: Generated acceptance scenarios", ""]
    for scenario in scenarios.scenarios:
        if any(source_id not in sources for source_id in scenario.source_ids):
            raise InputError("unknown_scenario_source")
        if context.mode == "default" and not any(
            sources[source_id].kind in {"description", "requirements"}
            for source_id in scenario.source_ids
        ):
            raise InputError("requirements_source_missing")
        lines.append(f"  Scenario: {_single_line(scenario.scenario_id)}")
        for keyword, steps in (
            ("Given", scenario.given),
            ("When", scenario.when),
            ("Then", scenario.then),
        ):
            for index, step in enumerate(steps):
                lines.append(f"    {keyword if index == 0 else 'And'} {_single_line(step)}")
        lines.append("")
    feature = "\n".join(lines)
    try:
        parsed = Parser().parse(feature)
        children = parsed["feature"]["children"]
        if len(children) != len(scenarios.scenarios):
            raise InputError("invalid_gherkin")
    except Exception:
        raise InputError("invalid_gherkin") from None
    return feature
