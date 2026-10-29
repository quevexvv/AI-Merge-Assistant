import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath

from .archives import read_archive
from .errors import InputError
from .limits import DEFAULT_LIMITS, InputLimits
from .paths import safe_path


@dataclass(frozen=True, slots=True, repr=False)
class DocumentText:
    path: str
    text: str
    page_count: int | None


def extract_document(path: str, data: bytes, limits: InputLimits = DEFAULT_LIMITS) -> DocumentText:
    safe_path(path)
    suffix = PurePosixPath(path).suffix.lower()
    if suffix not in {".pdf", ".docx", ".md"}:
        raise InputError("unsupported_document")
    if not data or len(data) > limits.document_bytes:
        raise InputError("document_size_limit")
    if suffix == ".md":
        try:
            return DocumentText(path, _clean_text(data.decode("utf-8-sig"), limits), None)
        except UnicodeError:
            raise InputError("invalid_text_encoding") from None
    command = [
        sys.executable,
        "-m",
        "app.services.parsing.document_worker",
        suffix,
        json.dumps(asdict(limits)),
    ]
    environment = {"PYTHONPATH": str(Path(__file__).resolve().parents[3]), "PYTHONUTF8": "1"}
    try:
        with subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
        ) as process:
            try:
                output, _ = process.communicate(input=data, timeout=limits.timeout_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise InputError("document_timeout") from None
            if process.returncode != 0:
                raise InputError("document_resource_limit")
    except OSError:
        raise InputError("document_worker_unavailable") from None
    try:
        code, text, pages = json.loads(output)
    except (ValueError, TypeError):
        raise InputError("invalid_document_result") from None
    if code:
        raise InputError(code)
    return DocumentText(path, text, pages)


def _clean_text(text: str, limits: InputLimits) -> str:
    if len(text) > limits.text_characters:
        raise InputError("document_text_limit")
    if "\x00" in text:
        raise InputError("invalid_document_text")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise InputError("document_has_no_text")
    return text


def _pdf(data: bytes, limits: InputLimits) -> tuple[str, int]:
    from pypdf import PdfReader

    if not data.startswith(b"%PDF-"):
        raise InputError("invalid_pdf")
    reader = PdfReader(BytesIO(data), strict=True)
    if reader.is_encrypted:
        raise InputError("encrypted_pdf")
    pages = len(reader.pages)
    if pages > limits.pages:
        raise InputError("document_page_limit")
    chunks: list[str] = []
    total = 0
    for page in reader.pages:
        text = page.extract_text() or ""
        total += len(text) + 2
        if total > limits.text_characters:
            raise InputError("document_text_limit")
        chunks.append(text)
    return "\n\n".join(chunks), pages


def _docx(data: bytes, limits: InputLimits) -> tuple[str, None]:
    from defusedxml.ElementTree import fromstring

    entries = read_archive(data, limits)
    if "word/document.xml" not in entries or "[Content_Types].xml" not in entries:
        raise InputError("invalid_docx")
    content_types = fromstring(entries["[Content_Types].xml"])
    main_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
    if not any(
        item.attrib.get("PartName") == "/word/document.xml"
        and item.attrib.get("ContentType") == main_type
        for item in content_types
    ):
        raise InputError("unsupported_docx_content")
    if any("vba" in name.casefold() or name.startswith("word/embeddings/") for name in entries):
        raise InputError("unsupported_docx_content")
    root = fromstring(entries["word/document.xml"])
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    if root.tag != namespace + "document":
        raise InputError("invalid_docx")
    paragraphs: list[str] = []
    total = 0
    for paragraph in root.iter(namespace + "p"):
        parts: list[str] = []
        for item in paragraph.iter():
            if item.tag == namespace + "t":
                parts.append(item.text or "")
            elif item.tag == namespace + "tab":
                parts.append("\t")
            elif item.tag in {namespace + "br", namespace + "cr"}:
                parts.append("\n")
        text = "".join(parts)
        total += len(text) + 1
        if total > limits.text_characters:
            raise InputError("document_text_limit")
        paragraphs.append(text)
    return "\n".join(paragraphs), None
