from dataclasses import replace
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest
from app.services.parsing import (
    InputError,
    InputLimits,
    extract_document,
    read_code_archive,
)
from app.services.parsing.archives import read_archive
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def test_document_worker_has_hard_deadline(monkeypatch):
    import subprocess
    import sys
    import time

    from app.services.parsing import documents

    created = []
    real_popen = subprocess.Popen

    def slow_worker(command, **kwargs):
        process = real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        created.append(process)
        return process

    monkeypatch.setattr(documents.subprocess, "Popen", slow_worker)
    started = time.monotonic()
    with pytest.raises(InputError, match="document_timeout"):
        extract_document("spec.pdf", b"%PDF-1.7", replace(InputLimits(), timeout_seconds=1))
    assert time.monotonic() - started < 5
    assert created[0].poll() is not None


def archive(entries, compression=ZIP_DEFLATED):
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=compression) as output:
        for name, content in entries:
            output.writestr(name, content)
    return buffer.getvalue()


def docx(text="<w:p><w:r><w:t>First requirement</w:t></w:r></w:p>"):
    document = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{text}</w:body></w:document>"
    )
    types = (
        '<Types><Override PartName="/word/document.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
    )
    return archive([("[Content_Types].xml", types), ("word/document.xml", document)])


def pdf(pages=1, encrypted=False, text=True):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=200, height=200)
        if text:
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            page[NameObject("/Resources")] = DictionaryObject(
                {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
            )
            stream = DecodedStreamObject()
            stream.set_data(b"BT /F1 12 Tf 10 100 Td (Double every integer.) Tj ET")
            page[NameObject("/Contents")] = stream
    if encrypted:
        writer.encrypt("private-password")
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_markdown_unicode():
    result = extract_document("spec.md", "\ufeffТребование\r\nОтвет".encode())
    assert result.text == "Требование\nОтвет"
    assert result.page_count is None


def test_docx_preserves_paragraph_table_and_break_order():
    body = (
        "<w:p><w:r><w:t>First</w:t><w:tab/><w:t>value</w:t></w:r></w:p>"
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Cell</w:t><w:br/><w:t>line</w:t>"
        "</w:r></w:p></w:tc></w:tr></w:tbl><w:p><w:r><w:t>Last</w:t></w:r></w:p>"
    )
    result = extract_document("spec.docx", docx(body))
    assert result.text == "First\tvalue\nCell\nline\nLast"
    assert result.page_count is None


def test_pdf_over_fifty_pages_is_accepted():
    result = extract_document("long.pdf", pdf(51))
    assert result.page_count == 51
    assert result.text.count("Double every integer.") == 51


@pytest.mark.parametrize(
    ("name", "data", "code"),
    [
        ("spec.md", b"\xff", "invalid_text_encoding"),
        ("spec.md", b"   ", "document_has_no_text"),
        ("spec.md", b"a\x00b", "invalid_document_text"),
        ("spec.md", b"", "document_size_limit"),
        ("spec.exe", b"x", "unsupported_document"),
        ("spec.pdf", b"not a PDF", "invalid_pdf"),
        ("spec.docx", b"not a ZIP", "invalid_archive"),
    ],
)
def test_document_errors_are_safe(name, data, code):
    with pytest.raises(InputError, match=f"^{code}$"):
        extract_document(name, data)


def test_encrypted_and_scanned_pdf_rejected():
    with pytest.raises(InputError, match="encrypted_pdf"):
        extract_document("secret.pdf", pdf(encrypted=True))
    with pytest.raises(InputError, match="document_has_no_text"):
        extract_document("scan.pdf", pdf(text=False))


def test_document_limits():
    with pytest.raises(InputError, match="document_size_limit"):
        extract_document("spec.md", b"1234", replace(InputLimits(), document_bytes=3))
    with pytest.raises(InputError, match="document_text_limit"):
        extract_document("spec.md", b"1234", replace(InputLimits(), text_characters=3))
    with pytest.raises(InputError, match="document_page_limit"):
        extract_document("spec.pdf", pdf(2), replace(InputLimits(), pages=1))


def test_docx_entities_are_rejected():
    payload = b'<!DOCTYPE a [<!ENTITY leak SYSTEM "file:///etc/passwd">]><a>&leak;</a>'
    data = archive([("[Content_Types].xml", payload), ("word/document.xml", b"<a/>")])
    with pytest.raises(InputError, match="invalid_document"):
        extract_document("spec.docx", data)


@pytest.mark.parametrize(
    "path",
    [
        "../escape.py",
        "/root.py",
        "a/../../b.py",
        "C:/file.py",
        "a\\b.py",
        "a//b.py",
        "a/./b.py",
        "NUL.py",
        ".git/config",
        "a/%2e%2e/b.py",
        "x\u202ey.py",
        "e\u0301.py",
    ],
)
def test_unsafe_archive_paths(path):
    with pytest.raises(InputError, match="invalid_path"):
        read_archive(archive([(path, "pass")]), InputLimits())


def test_archive_rejects_symlink_and_case_collision():
    entry = ZipInfo("link.py")
    entry.create_system = 3
    entry.external_attr = 0o120777 << 16
    with pytest.raises(InputError, match="unsupported_archive_entry"):
        read_archive(archive([(entry, "target.py")]), InputLimits())
    with pytest.raises(InputError, match="duplicate_path"):
        read_archive(archive([("a.py", "pass"), ("A.py", "pass")]), InputLimits())


def test_zip_bomb_and_file_directory_collision():
    with pytest.raises(InputError, match="archive_expansion_limit"):
        read_archive(archive([("large.py", "a" * 100_000)]), InputLimits())
    with pytest.raises(InputError, match="path_collision"):
        read_archive(archive([("a.py", "pass"), ("a.py/b.py", "pass")]), InputLimits())


def test_code_archive_is_not_extracted_or_imported(tmp_path):
    sentinel = tmp_path / "executed"
    content = f"open({str(sentinel)!r}, 'w').write('unsafe')\n"
    result = read_code_archive(archive([("sample.py", content)]))
    assert result[0].content == content
    assert not sentinel.exists()
    with pytest.raises(InputError, match="unsupported_source"):
        read_code_archive(archive([("script.sh", "exit 1")]))


def test_archive_count_size_and_encoding_limits():
    data = archive([("a.py", "x=1"), ("b.py", "x=2")])
    with pytest.raises(InputError, match="archive_too_large"):
        read_archive(data, replace(InputLimits(), archive_bytes=1))
    with pytest.raises(InputError, match="too_many_archive_entries"):
        read_archive(data, replace(InputLimits(), archive_entries=1))
    with pytest.raises(InputError, match="source_file_limit"):
        read_code_archive(data, replace(InputLimits(), source_files=1))
    with pytest.raises(InputError, match="source_size_limit"):
        read_code_archive(data, replace(InputLimits(), total_source_bytes=4))
    with pytest.raises(InputError, match="invalid_text_encoding"):
        read_code_archive(archive([("bad.py", b"\xff")]))


def test_docx_missing_parts_macros_and_text_limit():
    with pytest.raises(InputError, match="invalid_docx"):
        extract_document("spec.docx", archive([("word/document.xml", "<document/>")]))
    entries = list(read_archive(docx(), InputLimits()).items())
    with pytest.raises(InputError, match="unsupported_docx_content"):
        extract_document("spec.docx", archive([*entries, ("word/vbaProject.bin", b"macro")]))
    with pytest.raises(InputError, match="document_text_limit"):
        extract_document("spec.docx", docx(), replace(InputLimits(), text_characters=3))
    with pytest.raises(InputError, match="document_text_limit"):
        extract_document("spec.pdf", pdf(), replace(InputLimits(), text_characters=3))


@pytest.mark.parametrize("suffix", [".pdf", ".docx"])
def test_worker_contract_matches_isolated_output(suffix):
    from app.services.parsing.document_worker import process_document

    data = pdf() if suffix == ".pdf" else docx()
    code, text, pages = process_document(suffix, data, InputLimits())
    isolated = extract_document("spec" + suffix, data)
    assert code is None
    assert (text, pages) == (isolated.text, isolated.page_count)


def daemon_extract(queue, data):
    queue.put(extract_document("spec.pdf", data).text)


def test_extraction_works_in_daemonic_task_worker():
    import multiprocessing

    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    process = context.Process(target=daemon_extract, args=(queue, pdf()), daemon=True)
    process.start()
    try:
        assert queue.get(timeout=10) == "Double every integer."
        process.join(timeout=5)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.kill()
            process.join()
        process.close()
        queue.close()
        queue.join_thread()
