"""Git diff collection value types."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Hunk:
    id: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int


@dataclass
class ChangedFile:
    status: str
    old_path: str | None
    new_path: str | None
    additions: int = 0
    deletions: int = 0
    binary: bool = False
    hunks: list[Hunk] = field(default_factory=list)
    old_text: str | None = None
    new_text: str | None = None
    parser_warning: str | None = None

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""


@dataclass
class DiffCollection:
    files: list[ChangedFile]
    changed_files_total: int
    changed_lines: int
    excluded_counts: dict[str, int]
    warnings: list[str]
    omitted_counts: dict[str, int] = field(default_factory=dict)
    truncated: bool = False


@dataclass(frozen=True)
class GitTreeEntry:
    mode: str
    object_id: str


@dataclass
class BlobBatch:
    texts: dict[str, str | None]
    failures: dict[str, str]
