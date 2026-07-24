"""Safe, bounded collection of committed Git diff data."""

from __future__ import annotations

from .collect import collect_diff
from .limits import (
    COMMIT_RE,
    GIT_TIMEOUT_SECONDS,
    HARD_MAX_GIT_OUTPUT_BYTES,
    HUNK_RE,
    MAX_ANALYZED_FILES,
    MAX_GIT_INPUT_BYTES,
    MAX_GIT_OUTPUT_BYTES,
    MAX_SOURCE_BLOB_BYTES,
    MAX_SOURCE_FILE_BYTES,
    MAX_TREE_PATHS_PER_COMMAND,
    RENAME_DETECTION_ARGUMENTS,
)
from .parse import _parse_name_status, _parse_numstat, parse_hunks, parse_name_status, parse_numstat
from .process import (
    OutputLimitExceeded,
    _OutputLimitExceeded,
    _run_bounded_process,
    run_bounded_process,
)
from .repository import GitRepository
from .types import BlobBatch, ChangedFile, DiffCollection, GitTreeEntry, Hunk

# Keep private blob-batch alias discoverable for any remaining callers.
_BlobBatch = BlobBatch

__all__ = [
    "COMMIT_RE",
    "GIT_TIMEOUT_SECONDS",
    "HARD_MAX_GIT_OUTPUT_BYTES",
    "HUNK_RE",
    "MAX_ANALYZED_FILES",
    "MAX_GIT_INPUT_BYTES",
    "MAX_GIT_OUTPUT_BYTES",
    "MAX_SOURCE_BLOB_BYTES",
    "MAX_SOURCE_FILE_BYTES",
    "MAX_TREE_PATHS_PER_COMMAND",
    "RENAME_DETECTION_ARGUMENTS",
    "BlobBatch",
    "ChangedFile",
    "DiffCollection",
    "GitRepository",
    "GitTreeEntry",
    "Hunk",
    "OutputLimitExceeded",
    "_BlobBatch",
    "_OutputLimitExceeded",
    "_parse_name_status",
    "_parse_numstat",
    "_run_bounded_process",
    "collect_diff",
    "parse_hunks",
    "parse_name_status",
    "parse_numstat",
    "run_bounded_process",
]
