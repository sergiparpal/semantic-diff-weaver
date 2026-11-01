"""The files each commit up to the base changed, to tell which tests change with which code.

A test file that keeps changing in the same commits as a module is about that module, whatever
its name or imports say. The log is read up to the base, so the change under review is not
evidence about itself, and without merges, whose files are their parents' changes again. A
commit that changes many files — a reformat, a rename, a release — says nothing about which test
goes with which module, so it is left out.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import WeaverError
from ..path_policy import normalize_repo_path
from . import limits
from .repository import GitRepository


@dataclass(frozen=True)
class ChangeHistory:
    # The files of each commit read, newest first, sweeps left out.
    commits: tuple[frozenset[str], ...] = ()
    # Whether the log could not be read whole: too large, cut short, or malformed.
    incomplete: bool = False


def parse_history(raw: bytes) -> tuple[list[frozenset[str]], bool]:
    """The files of each commit of `git log -z --name-only --format=%x00%x00%H`, sweeps left
    out, and whether a record did not parse.

    Each commit opens with two NULs and its hash, then a line break before its first file; files
    are NUL-separated. A path is never empty and holds no NUL, so an empty field is a delimiter
    and the field after it a hash, whatever the paths are named.
    """
    commits: list[frozenset[str]] = []
    files: list[str] | None = None
    malformed = False
    after_delimiter = False

    def close() -> None:
        if files is not None and len(files) <= limits.MAX_HISTORY_COMMIT_FILES:
            commits.append(frozenset(files))

    for field in raw.split(b"\0"):
        if not field:
            after_delimiter = True
            continue
        if after_delimiter:
            after_delimiter = False
            close()
            files = []
            if not limits.COMMIT_RE.fullmatch(field.decode("ascii", errors="replace")):
                malformed = True
                files = None
            continue
        if files is None:
            malformed = True
            continue
        # Only the first file of a commit follows the line break that ends its hash's line.
        name = field[1:] if not files and field.startswith(b"\n") else field
        try:
            files.append(normalize_repo_path(name.decode("utf-8", errors="strict")))
        except (UnicodeDecodeError, WeaverError):
            # A path no analysis could name, which no candidate test is either.
            continue
    close()
    return commits, malformed


def read_history(repo: GitRepository, base_commit: str, depth: int) -> ChangeHistory:
    """The files each of the last `depth` commits up to the base changed, bounded; a log that
    cannot be read within bounds is an incomplete history, never an error."""
    if depth <= 0 or not limits.COMMIT_RE.fullmatch(base_commit):
        return ChangeHistory()
    try:
        raw = repo.run(
            [
                "log",
                "--no-merges",
                "--no-renames",
                "--no-ext-diff",
                "--no-textconv",
                f"--max-count={depth}",
                "-z",
                "--name-only",
                "--format=%x00%x00%H",
                base_commit,
                "--",
            ],
            max_bytes=limits.MAX_HISTORY_BYTES,
            binary=True,
        )
    except WeaverError:
        return ChangeHistory(incomplete=True)
    commits, malformed = parse_history(raw)
    return ChangeHistory(commits=tuple(commits), incomplete=malformed)
