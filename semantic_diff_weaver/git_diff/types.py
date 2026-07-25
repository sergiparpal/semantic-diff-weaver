"""Git diff collection value types."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..source import SourceHunk, SourceRevisionPair


@dataclass
class ChangedFile:
    """One changed path, with the Git-specific metadata the collection stage needs."""

    status: str
    old_path: str | None
    new_path: str | None
    additions: int = 0
    deletions: int = 0
    binary: bool = False
    hunks: list[SourceHunk] = field(default_factory=list)
    old_text: str | None = None
    new_text: str | None = None
    parser_warning: str | None = None

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""

    def as_revision_pair(self) -> SourceRevisionPair:
        """Narrow this Git result to the revision contract AST analysis consumes."""
        return SourceRevisionPair(
            path=self.path,
            old_path=self.old_path,
            new_path=self.new_path,
            old_text=self.old_text,
            new_text=self.new_text,
            hunks=tuple(self.hunks),
            # A copy's source file is not a predecessor, so it has no comparable old side.
            has_old_side=not self.status.startswith("C"),
        )


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
