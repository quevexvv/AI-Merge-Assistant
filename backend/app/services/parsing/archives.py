import stat
from io import BytesIO
from zipfile import BadZipFile, ZipFile

from .errors import InputError
from .limits import InputLimits
from .paths import safe_path, unique_paths


def read_archive(data: bytes, limits: InputLimits) -> dict[str, bytes]:
    if len(data) > limits.archive_bytes:
        raise InputError("archive_too_large")
    try:
        with ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > limits.archive_entries:
                raise InputError("too_many_archive_entries")
            paths: list[str] = []
            total = 0
            for entry in entries:
                path = entry.filename.removesuffix("/") if entry.is_dir() else entry.filename
                safe_path(path)
                paths.append(path)
                mode = entry.external_attr >> 16
                if (
                    entry.flag_bits & 1
                    or stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}
                    or (stat.S_ISDIR(mode) and not entry.is_dir())
                    or entry.orig_filename != entry.filename
                ):
                    raise InputError("unsupported_archive_entry")
                total += entry.file_size
                if (
                    total > limits.expanded_bytes
                    or entry.file_size > max(entry.compress_size, 1) * limits.compression_ratio
                ):
                    raise InputError("archive_expansion_limit")
            folded = [path.casefold() for path in paths]
            if len(folded) != len(set(folded)):
                raise InputError("duplicate_path")
            unique_paths([entry.filename for entry in entries if not entry.is_dir()])
            file_paths = {entry.filename.casefold() for entry in entries if not entry.is_dir()}
            for path in folded:
                parts = path.split("/")
                if any("/".join(parts[:index]) in file_paths for index in range(1, len(parts))):
                    raise InputError("path_collision")
            result = {}
            for entry in entries:
                if not entry.is_dir():
                    with archive.open(entry) as stream:
                        content = stream.read(entry.file_size + 1)
                    if len(content) != entry.file_size:
                        raise InputError("invalid_archive")
                    result[entry.filename] = content
            return result
    except (
        BadZipFile,
        EOFError,
        OSError,
        RuntimeError,
        NotImplementedError,
        ValueError,
    ) as error:
        if isinstance(error, InputError):
            raise
        raise InputError("invalid_archive") from None
