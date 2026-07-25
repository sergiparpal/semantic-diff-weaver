"""Safe, bounded collection of committed Git diff data.

Only the pipeline-facing surface is re-exported. Everything else — the process runner, the
wire-format parsers, and the byte budgets — is internal to this package and must be imported
from its defining module, so the boundary stays visible rather than being widened by re-export.
"""

from __future__ import annotations

from .collect import collect_diff
from .repository import GitRepository
from .types import BlobBatch, ChangedFile, DiffCollection, GitTreeEntry

__all__ = [
    "BlobBatch",
    "ChangedFile",
    "DiffCollection",
    "GitRepository",
    "GitTreeEntry",
    "collect_diff",
]
