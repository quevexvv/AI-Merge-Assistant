import unicodedata

from app.services.llm.bundles import relative_parts

from .errors import InputError


def safe_path(path: str) -> tuple[str, ...]:
    try:
        parts = relative_parts(path)
    except (ValueError, TypeError, AttributeError):
        raise InputError("invalid_path") from None
    if (
        len(path) > 1024
        or unicodedata.normalize("NFC", path) != path
        or any(unicodedata.category(char).startswith("C") for char in path)
        or any(len(part.encode("utf-8")) > 255 for part in parts)
        or any(char in path for char in '<>"|?*')
        or any(part.casefold() == ".git" for part in parts)
    ):
        raise InputError("invalid_path")
    return parts


def unique_paths(paths: list[str]) -> None:
    seen: set[tuple[str, ...]] = set()
    for path in paths:
        key = tuple(part.casefold() for part in safe_path(path))
        if key in seen:
            raise InputError("duplicate_path")
        seen.add(key)
    for key in seen:
        if any(key[:length] in seen for length in range(1, len(key))):
            raise InputError("path_collision")
