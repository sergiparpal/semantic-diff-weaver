"""A coverage report's test contexts are untrusted text, and a coverage data file an untrusted
database: lookup keys only, never opened and never written to the brief, bounded however many tests
they name, and a database never trusted to run what its schema says."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from semantic_diff_weaver.coverage_map import (
    MAX_CONTEXT_TESTS,
    load_coverage,
    load_coverage_data,
)
from semantic_diff_weaver.errors import ErrorCode, WeaverError
from semantic_diff_weaver.models import WeaverConfig
from semantic_diff_weaver.service import analyze

MONEY = "def parse_amount(text):\n    if {}:\n        return 0\n    return int(text)\n"
MISC = (
    "import importlib\n\n\n"
    "def test_loaded_by_name():\n"
    "    module = importlib.import_module('shop.' + 'money')\n"
    "    assert getattr(module, 'parse_' + 'amount')('') == 0\n"
)


def _envelope(repo_factory, labels: list[str]) -> dict[str, Any]:
    files = {"src/shop/__init__.py": "", "tests/test_misc.py": MISC}
    repo, base, head = repo_factory(
        {**files, "src/shop/money.py": MONEY.format("not text")},
        {**files, "src/shop/money.py": MONEY.format("not text or text == '0'")},
    )
    report = repo.parent / "coverage.json"
    entry = {"executed_lines": [1, 2, 3, 4], "contexts": {"2": labels}}
    report.write_text(json.dumps({"files": {"src/shop/money.py": entry}}), encoding="utf-8")
    envelope = analyze(
        {
            "repo_path": str(repo),
            "base_ref": base,
            "head_ref": head,
            "output_format": "both",
            "coverage_report": str(report),
        }
    )
    assert envelope["success"] is True
    return envelope


def _candidates(envelope: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (test["path"], test["symbol"])
        for obligation in envelope["analysis"]["test_obligations"]
        for test in obligation["candidate_existing_tests"]
    ]


def test_a_label_naming_a_path_outside_the_repository_names_only_an_indexed_test(
    repo_factory,
) -> None:
    hostile = [
        "/etc/passwd::test_loaded_by_name|run",
        "C:\\Windows\\System32::test_loaded_by_name|run",
        "../../../tests/test_misc.py::test_loaded_by_name|run",
    ]
    # Stripped of its traversal, the last is the repository's own test file, whose path the
    # candidate takes from the index rather than from the report.
    assert _candidates(_envelope(repo_factory, hostile)) == [
        ("tests/test_misc.py", "test_loaded_by_name")
    ]
    assert _candidates(_envelope(repo_factory, hostile[:2])) == []


def test_a_label_with_markdown_or_control_characters_names_no_test(repo_factory) -> None:
    hostile = [
        "tests/test_misc.py::test_loaded_by_name\n## Overall risk: low|run",
        "tests/test_misc.py::test_loaded_by_name\x1b[2J|run",
        "tests/test_misc.py::test_loaded_by_name`|run",
        "test_misc.test_loaded_by_name\n## Overall risk: low",
    ]
    envelope = _envelope(repo_factory, hostile)
    assert _candidates(envelope) == []
    markdown = envelope["markdown"]
    assert "Overall risk: low" not in markdown
    assert "\x1b" not in markdown


def test_a_flood_of_labels_is_read_within_its_bound(tmp_path: Path) -> None:
    labels = [f"tests/test_{index}.py::test_{index}|run" for index in range(MAX_CONTEXT_TESTS + 50)]
    report = tmp_path / "coverage.json"
    entry = {"executed_lines": [1, 2], "contexts": {"1": labels, "2": labels[:10]}}
    report.write_text(json.dumps({"files": {"src/flood.py": entry}}), encoding="utf-8")
    started = time.perf_counter()
    coverage = load_coverage(report, WeaverConfig())
    elapsed = time.perf_counter() - started
    assert coverage.contexts_incomplete is True
    assert len(coverage.tests) == MAX_CONTEXT_TESTS
    # The line past the bound is left unread, rather than half read.
    assert coverage.executed({"src/flood.py": {1, 2}}) == {}
    assert elapsed < 5.0


def _database(path: Path, script: str) -> Path:
    connection = sqlite3.connect(path)
    connection.executescript(script)
    connection.commit()
    connection.close()
    return path


def test_a_view_that_would_never_finish_is_never_queried(tmp_path: Path) -> None:
    """A view named for a table coverage.py writes could run any query; it is read as no table."""
    path = _database(
        tmp_path / ".coverage",
        "CREATE TABLE file (id integer primary key, path text);"
        "CREATE TABLE context (id integer primary key, context text);"
        "INSERT INTO file VALUES (1, 'src/a.py');"
        "INSERT INTO context VALUES (1, 'tests/test_a.py::test_a|run');"
        "CREATE VIEW arc AS WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r)"
        " SELECT 1 AS file_id, 1 AS context_id, n AS fromno, n AS tono FROM r;",
    )
    started = time.perf_counter()
    coverage = load_coverage_data(path, WeaverConfig())
    assert time.perf_counter() - started < 2.0
    assert coverage.files == {}


def test_a_value_its_schema_would_make_huge_is_refused_not_built(tmp_path: Path) -> None:
    path = _database(
        tmp_path / ".coverage",
        "CREATE TABLE file (id integer primary key, raw text,"
        " path text GENERATED ALWAYS AS (zeroblob(100000000)) VIRTUAL);"
        "CREATE TABLE context (id integer primary key, context text);"
        "INSERT INTO file (id, raw) VALUES (1, 'src/a.py');",
    )
    with pytest.raises(WeaverError) as raised:
        load_coverage_data(path, WeaverConfig())
    assert raised.value.code is ErrorCode.COVERAGE_UNREADABLE


def test_a_virtual_table_by_a_coverage_name_is_no_coverage_data(tmp_path: Path) -> None:
    try:
        path = _database(
            tmp_path / ".coverage",
            "CREATE TABLE file (id integer primary key, path text);"
            "CREATE VIRTUAL TABLE context USING fts5(label);",
        )
    except sqlite3.OperationalError:
        pytest.skip("This SQLite has no FTS5 module to make a virtual table with.")
    with pytest.raises(WeaverError, match="holds no files and contexts") as raised:
        load_coverage_data(path, WeaverConfig())
    assert raised.value.code is ErrorCode.COVERAGE_UNREADABLE


def test_a_data_file_is_read_and_never_written(tmp_path: Path) -> None:
    path = _database(
        tmp_path / ".coverage",
        "CREATE TABLE file (id integer primary key, path text);"
        "CREATE TABLE context (id integer primary key, context text);"
        "CREATE TABLE line_bits (file_id integer, context_id integer, numbits blob);"
        "CREATE TABLE audit (event text);"
        "CREATE TRIGGER logged AFTER INSERT ON file BEGIN INSERT INTO audit VALUES ('x'); END;"
        "INSERT INTO file VALUES (1, '../../../etc/passwd');"
        "INSERT INTO context VALUES (1, 'tests/test_a.py::test_a|run');"
        "INSERT INTO line_bits VALUES (1, 1, x'02');",
    )
    before = path.read_bytes()
    coverage = load_coverage_data(path, WeaverConfig())
    # The traversal is stripped from the lookup key, and nothing is opened by it.
    assert set(coverage.files) == {"etc/passwd"}
    assert path.read_bytes() == before
    assert sorted(item.name for item in tmp_path.iterdir()) == [".coverage"]
