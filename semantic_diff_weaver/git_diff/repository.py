"""Safe Git repository boundary and bounded object readers."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Literal, overload

from ..errors import ErrorCode, WeaverError
from ..path_policy import normalize_repo_path
from . import limits
from .process import OutputLimitExceeded, run_bounded_process
from .types import BlobBatch, GitTreeEntry


def _git_error(code: ErrorCode, message: str, remediation: str) -> WeaverError:
    return WeaverError(code, message, remediation)


class GitRepository:
    """A Git repository boundary with a safe command runner."""

    def __init__(self, root: Path, git: str) -> None:
        self.root = root
        self.git = git

    @classmethod
    def open(cls, repo_path: str) -> GitRepository:
        git = shutil.which("git")
        if git is None:
            raise _git_error(
                ErrorCode.NOT_A_GIT_REPOSITORY,
                "Git is not available on PATH.",
                "Install Git and ensure the git executable is available on PATH.",
            )
        try:
            requested = Path(repo_path).resolve(strict=True)
        except OSError as exc:
            raise _git_error(
                ErrorCode.NOT_A_GIT_REPOSITORY,
                "The repository path does not exist or is inaccessible.",
                "Provide a readable local Git repository path.",
            ) from exc
        cwd = requested if requested.is_dir() else requested.parent
        probe = cls(cwd, git)
        try:
            raw_root = probe.run(["rev-parse", "--show-toplevel"], max_bytes=64 * 1024).strip()
            root = Path(raw_root).resolve(strict=True)
            requested.relative_to(root)
        except (OSError, ValueError, WeaverError) as exc:
            raise _git_error(
                ErrorCode.NOT_A_GIT_REPOSITORY,
                "The supplied path is not contained in a readable Git repository.",
                "Provide the repository root or a path inside a local Git repository.",
            ) from exc
        if root == Path(root.anchor):
            raise _git_error(
                ErrorCode.PATH_OUTSIDE_REPOSITORY,
                "A filesystem root may not be used as the repository boundary.",
                "Provide a bounded Git repository directory.",
            )
        return cls(root, git)

    @overload
    def run(
        self,
        arguments: list[str],
        *,
        max_bytes: int = limits.MAX_GIT_OUTPUT_BYTES,
        binary: Literal[False] = False,
        input_data: bytes | None = None,
    ) -> str: ...

    @overload
    def run(
        self,
        arguments: list[str],
        *,
        max_bytes: int = limits.MAX_GIT_OUTPUT_BYTES,
        binary: Literal[True],
        input_data: bytes | None = None,
    ) -> bytes: ...

    def run(
        self,
        arguments: list[str],
        *,
        max_bytes: int = limits.MAX_GIT_OUTPUT_BYTES,
        binary: bool = False,
        input_data: bytes | None = None,
    ) -> str | bytes:
        # Inherited Git variables can redirect the repository, object database, config, namespace,
        # or replacement refs. Preserve the host environment needed to launch Git, but rebuild the
        # Git-specific portion from a strict allowlist.
        env = {
            key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")
        }
        env.update(
            {
                "GIT_ATTR_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_NO_LAZY_FETCH": "1",
                "GIT_NO_REPLACE_OBJECTS": "1",
                "GIT_PAGER": "cat",
                "LC_ALL": "C.UTF-8",
            }
        )
        command = [
            self.git,
            "--no-replace-objects",
            "--no-pager",
            "-c",
            "core.quotepath=false",
            *arguments,
        ]
        if input_data is not None and len(input_data) > limits.MAX_GIT_INPUT_BYTES:
            raise _git_error(
                ErrorCode.DIFF_TOO_LARGE,
                "Git input exceeded the bounded collection limit.",
                "Narrow the include patterns or split the change.",
            )
        try:
            completed = run_bounded_process(
                command,
                cwd=self.root,
                env=env,
                input_data=input_data,
                max_bytes=min(max_bytes, limits.HARD_MAX_GIT_OUTPUT_BYTES),
            )
        except OutputLimitExceeded as exc:
            raise _git_error(
                ErrorCode.DIFF_TOO_LARGE,
                "Git output exceeded the bounded collection limit.",
                "Narrow the include patterns or split the change.",
            ) from exc
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise _git_error(
                ErrorCode.NOT_A_GIT_REPOSITORY,
                "Git did not complete safely within the configured timeout.",
                "Verify the repository and Git installation, then retry with a smaller scope.",
            ) from exc
        if completed.returncode != 0:
            raise _git_error(
                ErrorCode.INVALID_REF,
                "Git could not resolve the requested revision or diff.",
                "Check that both refs exist locally and identify commits in this repository.",
            )
        if binary:
            return completed.stdout
        try:
            return completed.stdout.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise _git_error(
                ErrorCode.PARSE_FAILURE,
                "Git returned metadata that is not valid UTF-8.",
                "Rename the affected path to valid UTF-8 or narrow the analyzed scope.",
            ) from exc

    def resolve_ref(self, raw_ref: str) -> str:
        if (
            not raw_ref
            or raw_ref.startswith("-")
            or "\x00" in raw_ref
            or "\r" in raw_ref
            or "\n" in raw_ref
        ):
            raise _git_error(
                ErrorCode.INVALID_REF,
                "An empty or option-like Git ref was rejected.",
                "Provide a local branch, tag, or commit identifier that does not begin with '-'.",
            )
        try:
            resolved = self.run(
                ["rev-parse", "--verify", "--end-of-options", f"{raw_ref}^{{commit}}"],
                max_bytes=1024,
            ).strip()
        except WeaverError as exc:
            raise _git_error(
                ErrorCode.INVALID_REF,
                "A requested Git ref does not resolve to a commit.",
                "Check that the base and head refs exist locally and name commits.",
            ) from exc
        if not limits.COMMIT_RE.fullmatch(resolved):
            raise _git_error(
                ErrorCode.INVALID_REF,
                "Git returned an invalid commit identifier.",
                "Verify repository integrity and retry.",
            )
        return resolved

    def read_blob(self, commit: str, path: str, max_bytes: int) -> str | None:
        if not limits.COMMIT_RE.fullmatch(commit):
            raise _git_error(ErrorCode.INVALID_REF, "An unresolved commit was rejected.", "Retry.")
        normalized = normalize_repo_path(path)
        try:
            size_text = self.run(["cat-file", "-s", f"{commit}:{normalized}"], max_bytes=1024)
            size = int(size_text.strip())
        except (ValueError, WeaverError):
            return None
        effective_max_bytes = min(max_bytes, limits.MAX_SOURCE_FILE_BYTES)
        if size > effective_max_bytes:
            return None
        try:
            raw = self.run(
                ["show", f"{commit}:{normalized}"],
                max_bytes=effective_max_bytes,
                binary=True,
            )
        except WeaverError:
            return None
        if b"\x00" in raw:
            return None
        try:
            return raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return None

    def entry_mode(self, commit: str, path: str) -> str | None:
        """Return the committed Git mode without following symlinks or Git links."""
        if not limits.COMMIT_RE.fullmatch(commit):
            raise _git_error(ErrorCode.INVALID_REF, "An unresolved commit was rejected.", "Retry.")
        normalized = normalize_repo_path(path)
        try:
            raw = self.run(
                ["ls-tree", "-z", commit, "--", normalized],
                max_bytes=64 * 1024,
                binary=True,
            )
        except WeaverError:
            return None
        if not raw:
            return None
        header = raw.split(b"\t", 1)[0]
        parts = header.split(b" ", 2)
        if len(parts) != 3:
            return None
        try:
            return parts[0].decode("ascii", errors="strict")
        except UnicodeDecodeError:
            return None

    def tree_entries(self, commit: str, paths: list[str]) -> dict[str, GitTreeEntry]:
        """Read modes and object IDs for literal committed paths in one bounded command."""
        if not limits.COMMIT_RE.fullmatch(commit):
            raise _git_error(ErrorCode.INVALID_REF, "An unresolved commit was rejected.", "Retry.")
        normalized_paths = sorted({normalize_repo_path(path) for path in paths})
        if not normalized_paths:
            return {}
        requested = set(normalized_paths)
        entries: dict[str, GitTreeEntry] = {}
        for start in range(0, len(normalized_paths), limits.MAX_TREE_PATHS_PER_COMMAND):
            chunk = normalized_paths[start : start + limits.MAX_TREE_PATHS_PER_COMMAND]
            raw = self.run(
                [
                    "ls-tree",
                    "-z",
                    commit,
                    "--",
                    *(f":(literal){path}" for path in chunk),
                ],
                binary=True,
            )
            for record in raw.split(b"\x00"):
                if not record or b"\t" not in record:
                    continue
                header, path_raw = record.split(b"\t", 1)
                parts = header.split(b" ", 2)
                if len(parts) != 3:
                    continue
                try:
                    mode = parts[0].decode("ascii", errors="strict")
                    object_id = parts[2].decode("ascii", errors="strict")
                    path = normalize_repo_path(path_raw.decode("utf-8", errors="strict"))
                except (UnicodeDecodeError, WeaverError):
                    continue
                if path in requested and limits.COMMIT_RE.fullmatch(object_id):
                    entries[path] = GitTreeEntry(mode=mode, object_id=object_id)
        return entries

    def read_blob_objects(
        self,
        object_ids: set[str],
        max_bytes: int,
        *,
        max_total_bytes: int = limits.MAX_SOURCE_BLOB_BYTES,
    ) -> dict[str, str | None]:
        """Read many bounded blob objects with batch plumbing instead of per-file processes."""
        return self.read_blob_batch(object_ids, max_bytes, max_total_bytes=max_total_bytes).texts

    def read_blob_batch(
        self,
        object_ids: set[str],
        max_bytes: int,
        *,
        max_total_bytes: int = limits.MAX_SOURCE_BLOB_BYTES,
    ) -> BlobBatch:
        """Return bounded blob text plus non-sensitive failure classes for scope reporting."""
        requested = sorted(object_ids)
        if any(not limits.COMMIT_RE.fullmatch(object_id) for object_id in requested):
            raise _git_error(ErrorCode.INVALID_REF, "An invalid object ID was rejected.", "Retry.")
        result: dict[str, str | None] = dict.fromkeys(requested)
        failures = dict.fromkeys(requested, "missing_or_not_blob")
        if not requested:
            return BlobBatch(result, failures)
        batch_input = b"".join(f"{object_id}\n".encode("ascii") for object_id in requested)
        try:
            raw_info = self.run(
                ["cat-file", "--batch-check"],
                max_bytes=max(1024, len(requested) * 128),
                binary=True,
                input_data=batch_input,
            )
        except WeaverError:
            return BlobBatch(result, failures)
        eligible: list[tuple[str, int]] = []
        aggregate_bytes = 0
        effective_max_bytes = min(max_bytes, limits.MAX_SOURCE_FILE_BYTES)
        for line in raw_info.splitlines():
            parts = line.split(b" ")
            if len(parts) != 3 or parts[1] != b"blob":
                continue
            try:
                object_id = parts[0].decode("ascii", errors="strict")
                size = int(parts[2])
            except (UnicodeDecodeError, ValueError):
                continue
            if object_id not in result or size < 0:
                continue
            if size > effective_max_bytes:
                failures[object_id] = "oversized"
                continue
            max_blob_bytes = limits.MAX_SOURCE_BLOB_BYTES
            if aggregate_bytes + size > min(max_total_bytes, max_blob_bytes):
                failures[object_id] = "aggregate_source_limit"
                continue
            eligible.append((object_id, size))
            aggregate_bytes += size

        chunk_budget = limits.MAX_GIT_OUTPUT_BYTES
        chunks: list[list[tuple[str, int]]] = []
        current: list[tuple[str, int]] = []
        current_bytes = 0
        for object_id, size in eligible:
            estimated_bytes = size + len(object_id) + 32
            if current and current_bytes + estimated_bytes > chunk_budget:
                chunks.append(current)
                current = []
                current_bytes = 0
            current.append((object_id, size))
            current_bytes += estimated_bytes
        if current:
            chunks.append(current)

        for chunk in chunks:
            chunk_input = b"".join(f"{object_id}\n".encode("ascii") for object_id, _ in chunk)
            expected_bytes = sum(size + len(object_id) + 32 for object_id, size in chunk)
            try:
                raw_blobs = self.run(
                    ["cat-file", "--batch"],
                    max_bytes=max(1024, expected_bytes),
                    binary=True,
                    input_data=chunk_input,
                )
            except WeaverError:
                continue
            offset = 0
            while offset < len(raw_blobs):
                header_end = raw_blobs.find(b"\n", offset)
                if header_end < 0:
                    break
                header = raw_blobs[offset:header_end].split(b" ")
                if len(header) != 3 or header[1] != b"blob":
                    break
                try:
                    object_id = header[0].decode("ascii", errors="strict")
                    size = int(header[2])
                except (UnicodeDecodeError, ValueError):
                    break
                content_start = header_end + 1
                content_end = content_start + size
                if (
                    content_end >= len(raw_blobs)
                    or raw_blobs[content_end : content_end + 1] != b"\n"
                ):
                    break
                content = raw_blobs[content_start:content_end]
                offset = content_end + 1
                if object_id not in result:
                    continue
                if b"\x00" in content:
                    failures[object_id] = "binary"
                    continue
                try:
                    result[object_id] = content.decode("utf-8", errors="strict")
                except UnicodeDecodeError:
                    failures[object_id] = "non_utf8"
                    continue
                failures.pop(object_id, None)
        return BlobBatch(result, failures)

    def list_files(self, commit: str, max_bytes: int = limits.MAX_GIT_OUTPUT_BYTES) -> list[str]:
        raw = self.run(
            ["ls-tree", "-r", "-z", "--name-only", commit, "--"],
            max_bytes=max_bytes,
            binary=True,
        )
        result: list[str] = []
        for item in raw.split(b"\x00"):
            if not item:
                continue
            try:
                result.append(normalize_repo_path(item.decode("utf-8", errors="strict")))
            except (UnicodeDecodeError, WeaverError):
                continue
        return result
