"""Parsers for Git name-status, numstat, and unified-diff hunk headers."""

from __future__ import annotations

from ..errors import WeaverError
from ..path_policy import normalize_repo_path
from ..source import SourceHunk
from .limits import HUNK_RE
from .types import ChangedFile

_QUOTED_ESCAPES = {
    "a": 0x07,
    "b": 0x08,
    "f": 0x0C,
    "n": 0x0A,
    "r": 0x0D,
    "t": 0x09,
    "v": 0x0B,
    "\\": 0x5C,
    '"': 0x22,
}
_OCTAL_DIGITS = frozenset("01234567")


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
            # A rename or copy record needs both paths; a truncated pair is not a usable record.
            if index + 1 >= len(fields):
                break
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
        try:
            additions = 0 if binary else int(add_raw)
            deletions = 0 if binary else int(delete_raw)
        except ValueError:
            # Git metadata is untrusted input; a malformed count must not escape as a bare
            # ValueError, which the tool boundary can only report as an opaque internal error.
            continue
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


def unquote_git_path(value: str) -> str:
    """Decode Git's C-style quoted path form, which survives ``core.quotepath=false``."""
    if len(value) < 2 or not value.startswith('"') or not value.endswith('"'):
        return value
    body = value[1:-1]
    raw = bytearray()
    index = 0
    while index < len(body):
        character = body[index]
        if character != "\\":
            raw.extend(character.encode("utf-8"))
            index += 1
            continue
        escape = body[index + 1 : index + 2]
        if escape in _QUOTED_ESCAPES:
            raw.append(_QUOTED_ESCAPES[escape])
            index += 2
            continue
        octal = body[index + 1 : index + 4]
        if len(octal) == 3 and all(digit in _OCTAL_DIGITS for digit in octal):
            raw.append(int(octal, 8))
            index += 4
            continue
        raise ValueError("An unsupported escape appeared in a quoted Git path.")
    return raw.decode("utf-8", errors="strict")


def _header_path(value: str) -> str | None:
    """Return the repository path named by a ``---``/``+++`` unified-diff header line."""
    raw = value.split("\t", 1)[0]
    if raw == "/dev/null":
        return None
    try:
        decoded = unquote_git_path(raw)
        if not decoded.startswith(("a/", "b/")):
            return None
        return normalize_repo_path(decoded[2:])
    except (UnicodeDecodeError, ValueError, WeaverError):
        return None


def parse_hunks_by_path(diff_output: str, requested: frozenset[str]) -> dict[str, list[SourceHunk]]:
    """Split one batched unified diff into per-file hunks keyed by repository path.

    Only ``---``/``+++`` lines seen before a file's first hunk are treated as headers, so
    ``--unified=0`` content lines can never be mistaken for one. Paths Git did not report
    exactly as requested are left absent so the caller can fall back to a single-file diff.
    """
    result: dict[str, list[SourceHunk]] = {}
    current: list[SourceHunk] | None = None
    old_path: str | None = None
    in_body = True
    for line in diff_output.splitlines():
        if line.startswith("diff --git "):
            current = None
            old_path = None
            in_body = False
            continue
        if not in_body and line.startswith("--- "):
            old_path = _header_path(line[4:])
            continue
        if not in_body and line.startswith("+++ "):
            path = _header_path(line[4:]) or old_path
            current = result.setdefault(path, []) if path in requested else None
            continue
        match = HUNK_RE.match(line)
        if match:
            in_body = True
            if current is not None:
                current.append(
                    SourceHunk(
                        id=f"hunk-{len(current) + 1:03d}",
                        old_start=int(match.group(1)),
                        old_count=int(match.group(2) or 1),
                        new_start=int(match.group(3)),
                        new_count=int(match.group(4) or 1),
                    )
                )
    return result


def parse_hunks(diff_output: str) -> list[SourceHunk]:
    result: list[SourceHunk] = []
    for line in diff_output.splitlines():
        match = HUNK_RE.match(line)
        if match:
            result.append(
                SourceHunk(
                    id=f"hunk-{len(result) + 1:03d}",
                    old_start=int(match.group(1)),
                    old_count=int(match.group(2) or 1),
                    new_start=int(match.group(3)),
                    new_count=int(match.group(4) or 1),
                )
            )
    return result
