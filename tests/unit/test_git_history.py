"""The log of the commits up to the base is read as each commit's files, whatever they are named."""

from __future__ import annotations

from semantic_diff_weaver.git_diff.history import ChangeHistory, parse_history, read_history
from semantic_diff_weaver.git_diff.limits import MAX_HISTORY_COMMIT_FILES

NUL = b"\0"
FIRST = b"a" * 40
SECOND = b"b" * 40
THIRD = b"c" * 64


def record(commit: bytes, *paths: bytes) -> bytes:
    """One commit as `git log -z --name-only --format=%x00%x00%H` writes it."""
    if not paths:
        return NUL + NUL + commit + NUL
    return NUL + NUL + commit + NUL + b"\n" + NUL.join(paths) + NUL


def test_each_commit_is_read_as_its_files() -> None:
    raw = record(FIRST, b"src/a.py", b"tests/test_a.py") + record(SECOND, b"src/b.py")
    assert parse_history(raw) == (
        [frozenset({"src/a.py", "tests/test_a.py"}), frozenset({"src/b.py"})],
        False,
    )


def test_a_commit_without_files_and_a_sha256_hash_are_read() -> None:
    raw = record(FIRST) + record(THIRD, b"src/a.py")
    assert parse_history(raw) == ([frozenset(), frozenset({"src/a.py"})], False)


def test_a_path_named_like_a_hash_or_opening_with_a_line_break_is_a_path() -> None:
    hash_like = b"d" * 40
    raw = record(FIRST, b"\nnamed.py", hash_like) + record(SECOND, b"src/b.py")
    # The line break that ends the hash's line is dropped once; the path's own one stays.
    assert parse_history(raw) == (
        [frozenset({"\nnamed.py", "d" * 40}), frozenset({"src/b.py"})],
        False,
    )


def test_a_sweep_says_nothing_and_is_left_out() -> None:
    sweep = [f"src/file{index}.py".encode() for index in range(MAX_HISTORY_COMMIT_FILES + 1)]
    raw = record(FIRST, *sweep) + record(SECOND, b"src/a.py", b"tests/test_a.py")
    assert parse_history(raw) == ([frozenset({"src/a.py", "tests/test_a.py"})], False)


def test_a_path_no_analysis_could_name_is_skipped() -> None:
    raw = record(FIRST, b"src/\xff.py", b"../outside.py", b".git/config", b"src/a.py")
    assert parse_history(raw) == ([frozenset({"src/a.py"})], False)


def test_a_record_that_does_not_parse_makes_the_history_incomplete() -> None:
    assert parse_history(b"src/a.py" + record(FIRST, b"src/b.py")) == (
        [frozenset({"src/b.py"})],
        True,
    )
    assert parse_history(NUL + NUL + b"not-a-hash" + NUL + b"\nsrc/a.py" + NUL) == ([], True)
    assert parse_history(b"") == ([], False)


def test_no_depth_or_no_commit_reads_no_log() -> None:
    class Refusing:
        def run(self, *args: object, **kwargs: object) -> bytes:
            raise AssertionError("no log is read")

    assert read_history(Refusing(), "a" * 40, 0) == ChangeHistory()  # type: ignore[arg-type]
    assert read_history(Refusing(), "HEAD", 10) == ChangeHistory()  # type: ignore[arg-type]
