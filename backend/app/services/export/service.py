import hashlib
import json
from dataclasses import dataclass
from io import BytesIO
from typing import Literal
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from app.services.llm.bundles import TestBundle
from app.services.llm.contracts import GenerationContext
from app.services.llm.scenarios import ScenarioSet
from app.services.parsing.errors import InputError
from app.services.parsing.paths import unique_paths
from app.services.validation.gate import QualityGate
from app.services.validation.gherkin import render_feature


@dataclass(frozen=True, slots=True, repr=False)
class ExportArtifact:
    filename: str
    media_type: str
    content: bytes

    @property
    def size(self) -> int:
        return len(self.content)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


class ExportService:
    def __init__(self, gate: QualityGate | None = None) -> None:
        self._gate = gate or QualityGate()

    def build(
        self,
        context: GenerationContext,
        scenarios: ScenarioSet,
        bundle: TestBundle,
        *,
        format: Literal["feature", "py", "zip"] = "zip",
        path: str | None = None,
    ) -> ExportArtifact:
        result = self._gate.validate(context, scenarios, bundle)
        if not result.passed:
            raise InputError("export_validation_failed")
        if format == "feature":
            return ExportArtifact(
                "scenarios.feature",
                "text/plain; charset=utf-8",
                render_feature(context, scenarios).encode("utf-8"),
            )
        if format == "py":
            if path is None and len(bundle.files) != 1:
                raise InputError("export_path_required")
            selected = next(
                (file for file in bundle.files if path is None or file.path == path),
                None,
            )
            if selected is None:
                raise InputError("export_path_not_found")
            return ExportArtifact(
                selected.path.rsplit("/", 1)[-1],
                "text/x-python; charset=utf-8",
                selected.content.encode("utf-8"),
            )
        if format != "zip":
            raise InputError("unsupported_export_format")
        entries = {file.path: file.content.encode("utf-8") for file in bundle.files}
        entries["scenarios.feature"] = render_feature(context, scenarios).encode("utf-8")
        manifest = {
            "schema_version": "1",
            "run_id": context.run_id,
            "snapshot_id": context.snapshot.snapshot_id,
            "snapshot_hash": context.snapshot.content_hash,
            "bundle_hash": bundle.bundle_hash,
            "expectation_origin": scenarios.expectation_origin,
            "validation": "static_only",
            "execution": "not_attested",
            "scenarios": [
                {"scenario_id": item.scenario_id, "source_ids": list(item.source_ids)}
                for item in scenarios.scenarios
            ],
            "files": [
                {
                    "path": name,
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
                for name, content in sorted(entries.items())
            ],
        }
        entries["manifest.json"] = json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        unique_paths(list(entries))
        buffer = BytesIO()
        with ZipFile(buffer, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
            for name, content in sorted(entries.items()):
                entry = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                entry.create_system = 3
                entry.external_attr = 0o100644 << 16
                entry.compress_type = ZIP_DEFLATED
                archive.writestr(entry, content, compresslevel=6)
        return ExportArtifact("generated-tests.zip", "application/zip", buffer.getvalue())
