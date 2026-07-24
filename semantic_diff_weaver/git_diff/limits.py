"""Immutable Git collection budgets."""

from __future__ import annotations

import re

GIT_TIMEOUT_SECONDS = 15
MAX_GIT_OUTPUT_BYTES = 16 * 1024 * 1024
HARD_MAX_GIT_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_GIT_INPUT_BYTES = 2 * 1024 * 1024
MAX_SOURCE_FILE_BYTES = 8 * 1024 * 1024
MAX_SOURCE_BLOB_BYTES = 64 * 1024 * 1024
MAX_ANALYZED_FILES = 1000
MAX_TREE_PATHS_PER_COMMAND = 256
# Git's 50% default misses small CRLF renames; AST matching still rejects ambiguous symbols.
RENAME_DETECTION_ARGUMENTS = ("-M40%", "-C40%")
COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
