"""Parsers for Git name-status, numstat, and unified-diff hunk headers."""

from __future__ import annotations

from ..path_policy import normalize_repo_path
from .limits import HUNK_RE
from .types import ChangedFile, Hunk


def parse_name_status(raw: bytes) -> list[ChangedFile]:
    fields = raw.split(b"\x00")
    files: list[ChangedFile] = []
    index = 0
    while index < len(fields) and fields[index]:
        status = fields[index].decode("ascii", errors="strict")
        index += 1
        code = status[:1]
        old_path: str | None
        new_path: str | None
        if code in {"R", "C"}:
            old_path = normalize_repo_path(fields[index].decode("utf-8", errors="strict"))
            new_path = normalize_repo_path(fields[index + 1].decode("utf-8", errors="strict"))
            index += 2
        else:
            path = normalize_repo_path(fields[index].decode("utf-8", errors="strict"))
            index += 1
            old_path = None if code == "A" else path
            new_path = None if code == "D" else path
        files.append(ChangedFile(status=status, old_path=old_path, new_path=new_path))
    return files


def parse_numstat(raw: bytes) -> tuple[dict[str, tuple[int, int, bool]], int]:
    stats: dict[str, tuple[int, int, bool]] = {}
    total = 0
    fields = raw.split(b"\x00")
    index = 0
    while index < len(fields) and fields[index]:
        record = fields[index]
        index += 1
        pieces = record.split(b"\t", 2)
        if len(pieces) != 3:
            continue
        add_raw, delete_raw, path_raw = pieces
        binary = add_raw == b"-" or delete_raw == b"-"
        additions = 0 if binary else int(add_raw)
        deletions = 0 if binary else int(delete_raw)
        if path_raw:
            path = normalize_repo_path(path_raw.decode("utf-8", errors="strict"))
        else:
            if index + 1 >= len(fields):
                break
            index += 1
            path = normalize_repo_path(fields[index].decode("utf-8", errors="strict"))
            index += 1
        stats[path] = (additions, deletions, binary)
        total += additions + deletions
    return stats, total


def parse_hunks(diff_output: str) -> list[Hunk]:
    result: list[Hunk] = []
    for line in diff_output.splitlines():
        match = HUNK_RE.match(line)
        if match:
            result.append(
                Hunk(
                    id=f"hunk-{len(result) + 1:03d}",
                    old_start=int(match.group(1)),
                    old_count=int(match.group(2) or 1),
                    new_start=int(match.group(3)),
                    new_count=int(match.group(4) or 1),
                )
            )
    return result


# Backward-compatible private aliases.
_parse_name_status = parse_name_status
_parse_numstat = parse_numstat
