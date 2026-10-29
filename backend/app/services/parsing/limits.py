from dataclasses import dataclass, fields


@dataclass(frozen=True, slots=True)
class InputLimits:
    document_bytes: int = 10_000_000
    text_characters: int = 1_000_000
    pages: int = 500
    archive_bytes: int = 10_000_000
    expanded_bytes: int = 30_000_000
    archive_entries: int = 500
    compression_ratio: int = 100
    source_bytes: int = 500_000
    source_files: int = 200
    total_source_bytes: int = 5_000_000
    ast_nodes: int = 50_000
    timeout_seconds: int = 15
    memory_bytes: int = 536_870_912

    def __post_init__(self) -> None:
        if any(
            type(value := getattr(self, field.name)) is not int or value <= 0
            for field in fields(self)
        ):
            raise ValueError("limits must be positive integers")


DEFAULT_LIMITS = InputLimits()
