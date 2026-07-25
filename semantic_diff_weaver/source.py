"""The source-revision contract shared by diff collection and AST analysis.

AST comparison needs two revisions of a file plus the line ranges that changed. It does not
need a Git status letter, rename score, or blob mode. Stating that contract here keeps
``ast_diff`` independent of ``git_diff``: the analysis stage can be driven by anything that can
produce a pair of texts, and neither package imports the other.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceHunk:
    """One changed line range, in old-revision and new-revision coordinates."""

    id: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int


@dataclass(frozen=True)
class SourceRevisionPair:
    """Two revisions of one source file and the hunks that differ between them.

    ``has_old_side`` is false when the new file has no comparable predecessor — a copy, where
    treating the source file as the "old" revision would invent a change that never happened.
    """

    path: str
    old_path: str | None
    new_path: str | None
    old_text: str | None
    new_text: str | None
    hunks: tuple[SourceHunk, ...] = ()
    has_old_side: bool = True
