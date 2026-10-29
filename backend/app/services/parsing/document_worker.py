import json
import logging
import sys

from .documents import _clean_text, _docx, _pdf
from .errors import InputError
from .limits import InputLimits


def process_document(suffix: str, data: bytes, limits: InputLimits) -> tuple:
    try:
        if suffix not in {".pdf", ".docx"}:
            raise InputError("unsupported_document")
        if len(data) > limits.document_bytes:
            raise InputError("document_size_limit")
        text, pages = _pdf(data, limits) if suffix == ".pdf" else _docx(data, limits)
        return None, _clean_text(text, limits), pages
    except InputError as error:
        return error.code, None, None
    except Exception:
        return "invalid_document", None, None


def main() -> None:
    logging.disable(logging.CRITICAL)
    limits = InputLimits(**json.loads(sys.argv[2]))
    if sys.platform == "linux":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (limits.timeout_seconds,) * 2)
    data = sys.stdin.buffer.read(limits.document_bytes + 1)
    result = process_document(sys.argv[1], data, limits)
    sys.stdout.write(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
