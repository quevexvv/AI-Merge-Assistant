import hashlib
import json
from io import BytesIO
from zipfile import ZipFile

import pytest
from gherkin.parser import Parser

from app.services.export import ExportService
from app.services.llm.fake import FakeProvider
from app.services.llm.provider import ProviderResponse
from app.services.llm.service import LLMService
from app.services.llm.settings import LLMSettings
from app.services.parsing import InputError


def test_zip_is_reproducible_and_has_exact_validated_contents(context, scenarios, bundle_factory):
    bundle = bundle_factory()
    service = ExportService()
    first = service.build(context, scenarios, bundle)
    second = service.build(context, scenarios, bundle)
    assert first.content == second.content
    assert first.sha256 == hashlib.sha256(first.content).hexdigest()
    assert first.size == len(first.content)
    with ZipFile(BytesIO(first.content)) as archive:
        assert set(archive.namelist()) == {
            bundle.files[0].path,
            "scenarios.feature",
            "manifest.json",
        }
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["bundle_hash"] == bundle.bundle_hash
        assert manifest["execution"] == "not_attested"
        for entry in manifest["files"]:
            assert hashlib.sha256(archive.read(entry["path"])).hexdigest() == entry["sha256"]
        feature = Parser().parse(archive.read("scenarios.feature").decode())
        assert feature["feature"]["tags"][0]["name"] == "@requirements"


def test_individual_exports_and_unknown_paths(context, scenarios, bundle_factory):
    service = ExportService()
    bundle = bundle_factory()
    assert (
        service.build(context, scenarios, bundle, format="py").content.decode()
        == bundle.files[0].content
    )
    assert (
        service.build(context, scenarios, bundle, format="feature").filename == "scenarios.feature"
    )
    with pytest.raises(InputError, match="export_path_not_found"):
        service.build(context, scenarios, bundle, format="py", path="/etc/passwd")
    with pytest.raises(InputError, match="unsupported_export_format"):
        service.build(context, scenarios, bundle, format="js")


def test_export_always_rechecks_content(context, scenarios, bundle_factory):
    with pytest.raises(InputError, match="export_validation_failed"):
        ExportService().build(
            context, scenarios, bundle_factory("def test_x():\n    assert True\n")
        )


def test_existing_llm_fake_to_export(context, scenarios, bundle_factory):
    draft_scenarios = scenarios.model_dump(include={"scenarios", "ambiguities"})
    draft_tests = bundle_factory().model_dump(include={"files"})
    fake = FakeProvider(
        responses=[
            ProviderResponse(text=json.dumps(data), finish_reason="STOP")
            for data in (draft_scenarios, draft_tests)
        ],
        token_counts=[1, 1],
    )
    service = LLMService(fake, LLMSettings(backend="fake", model_id="offline-test"))
    generated_scenarios = service.generate_scenarios(context)
    generated_tests = service.generate_tests(context, generated_scenarios)
    artifact = ExportService().build(context, generated_scenarios, generated_tests)
    assert artifact.media_type == "application/zip"
    assert generated_tests.metadata.backend == "fake"
