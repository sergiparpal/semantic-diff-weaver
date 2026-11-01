"""Parsing and resolving an ingested coverage report.

The report is untrusted input data. Everything here is about reading it safely and, above
all, about never converting "the report does not mention this file" into "this file has no
tests" — the failure mode that would make a misconfigured path prefix look like a coverage
gap.
"""

from __future__ import annotations

import json
import sqlite3
from itertools import pairwise
from pathlib import Path

import pytest
from coverage import CoverageData
from coverage.numbits import nums_to_numbits

import semantic_diff_weaver.coverage_map as coverage_map
from semantic_diff_weaver.coverage_map import (
    SOURCE_COVERAGE_DATA,
    SOURCE_COVERAGE_JSON,
    SOURCE_LCOV,
    ContextTests,
    CoverageMap,
    FileCoverage,
    load_coverage,
    load_coverage_data,
    summarize,
)
from semantic_diff_weaver.errors import ErrorCode, WeaverError
from semantic_diff_weaver.models import LineRange, WeaverConfig


def _config(**rules: object) -> WeaverConfig:
    return WeaverConfig.model_validate({"rules": rules}) if rules else WeaverConfig()


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


COVERAGE_JSON = json.dumps(
    {
        "meta": {"version": "7.6.1"},
        "files": {
            "src/api.py": {"executed_lines": [1, 2, 3], "missing_lines": [10, 11]},
            "src/other.py": {"executed_lines": [1], "missing_lines": []},
        },
        "totals": {},
    }
)

LCOV = "\n".join(
    [
        "TN:",
        "SF:src/api.py",
        "DA:1,4",
        "DA:2,1",
        "DA:3,7",
        "DA:10,0",
        "DA:11,0",
        "LF:5",
        "LH:3",
        "end_of_record",
        "SF:src/other.py",
        "DA:1,2",
        "end_of_record",
        "",
    ]
)


def test_coverage_json_is_parsed(tmp_path: Path) -> None:
    coverage = load_coverage(_write(tmp_path, "coverage.json", COVERAGE_JSON), _config())
    assert coverage.source == SOURCE_COVERAGE_JSON
    assert coverage.status_for("src/api.py", LineRange(start=1, end=3)) == "covered"
    assert coverage.status_for("src/api.py", LineRange(start=10, end=11)) == "uncovered"


def test_lcov_is_parsed(tmp_path: Path) -> None:
    coverage = load_coverage(_write(tmp_path, "lcov.info", LCOV), _config())
    assert coverage.source == SOURCE_LCOV
    assert coverage.status_for("src/api.py", LineRange(start=1, end=3)) == "covered"
    assert coverage.status_for("src/api.py", LineRange(start=10, end=11)) == "uncovered"


def test_both_formats_agree(tmp_path: Path) -> None:
    """The two parsers must produce the same verdicts from equivalent reports."""
    from_json = load_coverage(_write(tmp_path, "coverage.json", COVERAGE_JSON), _config())
    from_lcov = load_coverage(_write(tmp_path, "lcov.info", LCOV), _config())
    for span in (LineRange(start=1, end=3), LineRange(start=10, end=11)):
        assert from_json.status_for("src/api.py", span) == from_lcov.status_for("src/api.py", span)


def test_the_format_is_sniffed_by_content_not_extension(tmp_path: Path) -> None:
    assert load_coverage(_write(tmp_path, "report.info", COVERAGE_JSON), _config()).source == (
        SOURCE_COVERAGE_JSON
    )
    assert load_coverage(_write(tmp_path, "report.json", LCOV), _config()).source == SOURCE_LCOV


def test_lcov_without_a_trailing_end_of_record_is_still_read(tmp_path: Path) -> None:
    text = "SF:src/api.py\nDA:1,1\nDA:2,0\n"
    coverage = load_coverage(_write(tmp_path, "lcov.info", text), _config())
    assert coverage.status_for("src/api.py", LineRange(start=1, end=1)) == "covered"
    assert coverage.status_for("src/api.py", LineRange(start=2, end=2)) == "uncovered"


def test_a_mixed_range_is_unknown_rather_than_guessed(tmp_path: Path) -> None:
    coverage = load_coverage(_write(tmp_path, "coverage.json", COVERAGE_JSON), _config())
    assert coverage.status_for("src/api.py", LineRange(start=1, end=11)) == "unknown"


def test_a_range_with_no_known_lines_is_unknown(tmp_path: Path) -> None:
    coverage = load_coverage(_write(tmp_path, "coverage.json", COVERAGE_JSON), _config())
    assert coverage.status_for("src/api.py", LineRange(start=500, end=520)) == "unknown"


def test_ci_absolute_paths_match_by_suffix(tmp_path: Path) -> None:
    """Coverage reports store CI working-directory paths, not repository-relative ones."""
    text = json.dumps(
        {"files": {"/home/runner/work/project/project/src/api.py": {"executed_lines": [1]}}}
    )
    coverage = load_coverage(_write(tmp_path, "coverage.json", text), _config())
    assert coverage.status_for("src/api.py", LineRange(start=1, end=1)) == "covered"


def test_windows_separators_and_drive_letters_normalize(tmp_path: Path) -> None:
    text = json.dumps({"files": {"C:\\\\build\\\\src\\\\api.py": {"executed_lines": [1]}}})
    coverage = load_coverage(_write(tmp_path, "coverage.json", text), _config())
    assert coverage.status_for("src/api.py", LineRange(start=1, end=1)) == "covered"


def test_the_longest_suffix_match_wins() -> None:
    coverage = CoverageMap(
        source="test",
        files={
            "api.py": FileCoverage(covered=frozenset(), uncovered=frozenset({1})),
            "build/src/api.py": FileCoverage(covered=frozenset({1}), uncovered=frozenset()),
        },
    )
    # "src/api.py" shares two trailing components with the second entry, one with the first.
    assert coverage.status_for("src/api.py", LineRange(start=1, end=1)) == "covered"


def test_an_exact_match_beats_a_longer_suffix() -> None:
    coverage = CoverageMap(
        source="test",
        files={
            "src/api.py": FileCoverage(covered=frozenset({1}), uncovered=frozenset()),
            "vendor/src/api.py": FileCoverage(covered=frozenset(), uncovered=frozenset({1})),
        },
    )
    assert coverage.status_for("src/api.py", LineRange(start=1, end=1)) == "covered"


def test_an_ambiguous_match_is_unknown_rather_than_a_guess() -> None:
    """Two unrelated files matching equally well must not produce a coverage claim."""
    coverage = CoverageMap(
        source="test",
        files={
            "a/src/api.py": FileCoverage(covered=frozenset({1}), uncovered=frozenset()),
            "b/src/api.py": FileCoverage(covered=frozenset(), uncovered=frozenset({1})),
        },
    )
    assert coverage.status_for("src/api.py", LineRange(start=1, end=1)) == "unknown"


def test_a_partial_component_is_not_a_match() -> None:
    coverage = CoverageMap(
        source="test",
        files={"src/legacy_api.py": FileCoverage(covered=frozenset({1}), uncovered=frozenset())},
    )
    assert coverage.status_for("src/api.py", LineRange(start=1, end=1)) == "unknown"


def test_an_unmatched_file_is_unknown_and_counted(tmp_path: Path) -> None:
    """The central rule: absent from the report is never reported as uncovered."""
    coverage = load_coverage(_write(tmp_path, "coverage.json", COVERAGE_JSON), _config())
    assert coverage.status_for("src/absent.py", LineRange(start=1, end=4)) == "unknown"
    assert coverage.unmatched_files == 1
    assert coverage.unmatched_paths == ("src/absent.py",)
    coverage.status_for("src/api.py", LineRange(start=1, end=1))
    assert coverage.matched_files == 1


def test_duplicate_entries_union_and_execution_wins(tmp_path: Path) -> None:
    text = "SF:src/api.py\nDA:1,0\nend_of_record\nSF:src/api.py\nDA:1,3\nend_of_record\n"
    coverage = load_coverage(_write(tmp_path, "lcov.info", text), _config())
    assert coverage.status_for("src/api.py", LineRange(start=1, end=1)) == "covered"


def test_summarize_counts_only_known_lines(tmp_path: Path) -> None:
    coverage = load_coverage(_write(tmp_path, "coverage.json", COVERAGE_JSON), _config())
    changed, covered, uncovered = summarize(
        coverage, {"src/api.py": {1, 2, 3}, "src/absent.py": {1, 2}}
    )
    assert (changed, covered, uncovered) == (5, 3, 0)


def test_line_sets_are_judged_like_ranges(tmp_path: Path) -> None:
    coverage = load_coverage(_write(tmp_path, "coverage.json", COVERAGE_JSON), _config())
    assert coverage.status_for_lines("src/api.py", {1, 3}) == "covered"
    assert coverage.status_for_lines("src/api.py", {10, 11}) == "uncovered"
    assert coverage.status_for_lines("src/api.py", {1, 10}) == "unknown"
    assert coverage.status_for_lines("src/api.py", set()) == "unknown"
    assert coverage.counts_for_lines("src/api.py", {1, 2, 10}) == (2, 1)
    assert coverage.counts_for_lines("src/absent.py", {1}) == (0, 0)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "{not json",
        "[1, 2, 3]",
        '{"meta": {}}',
        '{"files": []}',
        '{"files": {}}',
        "not a coverage report at all",
        "SF:\nend_of_record\n",
    ],
)
def test_malformed_input_is_coverage_unreadable(tmp_path: Path, text: str) -> None:
    with pytest.raises(WeaverError) as raised:
        load_coverage(_write(tmp_path, "report.json", text), _config())
    assert raised.value.code is ErrorCode.COVERAGE_UNREADABLE
    assert raised.value.remediation


def test_a_missing_report_is_coverage_unreadable(tmp_path: Path) -> None:
    with pytest.raises(WeaverError) as raised:
        load_coverage(tmp_path / "absent.json", _config())
    assert raised.value.code is ErrorCode.COVERAGE_UNREADABLE


def test_a_directory_is_coverage_unreadable(tmp_path: Path) -> None:
    with pytest.raises(WeaverError) as raised:
        load_coverage(tmp_path, _config())
    assert raised.value.code is ErrorCode.COVERAGE_UNREADABLE


def test_non_utf8_input_is_coverage_unreadable(tmp_path: Path) -> None:
    path = tmp_path / "coverage.json"
    path.write_bytes(b'{"files": {"\xff\xfe": {}}}')
    with pytest.raises(WeaverError) as raised:
        load_coverage(path, _config())
    assert raised.value.code is ErrorCode.COVERAGE_UNREADABLE


def test_an_oversized_report_is_rejected_before_parsing(tmp_path: Path) -> None:
    path = _write(tmp_path, "coverage.json", json.dumps({"files": {"a.py": {}}}) + " " * 5000)
    with pytest.raises(WeaverError) as raised:
        load_coverage(path, _config(max_coverage_bytes=2048))
    assert raised.value.code is ErrorCode.COVERAGE_UNREADABLE
    assert "limit" in raised.value.safe_message


def test_the_size_limit_is_configurable_and_bounded() -> None:
    assert _config().rules.max_coverage_bytes == 20_000_000
    with pytest.raises(ValueError):
        _config(max_coverage_bytes=0)
    with pytest.raises(ValueError):
        _config(max_coverage_bytes=10**12)


def test_absurd_line_numbers_are_discarded(tmp_path: Path) -> None:
    text = json.dumps(
        {"files": {"src/api.py": {"executed_lines": [0, -5, 10**9, 2], "missing_lines": [3]}}}
    )
    coverage = load_coverage(_write(tmp_path, "coverage.json", text), _config())
    assert coverage.status_for("src/api.py", LineRange(start=2, end=2)) == "covered"
    assert coverage.status_for("src/api.py", LineRange(start=1, end=1)) == "unknown"


def test_non_integer_and_boolean_line_numbers_are_discarded(tmp_path: Path) -> None:
    text = json.dumps(
        {"files": {"src/api.py": {"executed_lines": [True, "3", None, 4], "missing_lines": "no"}}}
    )
    coverage = load_coverage(_write(tmp_path, "coverage.json", text), _config())
    assert coverage.status_for("src/api.py", LineRange(start=4, end=4)) == "covered"
    assert coverage.status_for("src/api.py", LineRange(start=1, end=3)) == "unknown"


def test_malformed_entries_are_skipped_not_fatal(tmp_path: Path) -> None:
    text = json.dumps({"files": {"src/api.py": {"executed_lines": [1]}, "": {}, "x": "not a dict"}})
    coverage = load_coverage(_write(tmp_path, "coverage.json", text), _config())
    assert coverage.status_for("src/api.py", LineRange(start=1, end=1)) == "covered"


def test_malformed_lcov_data_lines_are_skipped(tmp_path: Path) -> None:
    text = "SF:src/api.py\nDA:notanumber,1\nDA:5\nDA:,\nDA:2,1\nend_of_record\n"
    coverage = load_coverage(_write(tmp_path, "lcov.info", text), _config())
    assert coverage.status_for("src/api.py", LineRange(start=2, end=2)) == "covered"


def test_lcov_data_before_any_source_file_is_ignored(tmp_path: Path) -> None:
    text = "DA:1,1\nSF:src/api.py\nDA:2,1\nend_of_record\n"
    coverage = load_coverage(_write(tmp_path, "lcov.info", text), _config())
    assert coverage.status_for("src/api.py", LineRange(start=2, end=2)) == "covered"


def test_an_lcov_record_flood_is_rejected(tmp_path: Path, monkeypatch) -> None:
    import semantic_diff_weaver.coverage_map as module

    monkeypatch.setattr(module, "MAX_LCOV_RECORDS", 3)
    text = "SF:src/api.py\n" + "".join(f"DA:{n},1\n" for n in range(1, 20)) + "end_of_record\n"
    with pytest.raises(WeaverError) as raised:
        load_coverage(_write(tmp_path, "lcov.info", text), _config())
    assert raised.value.code is ErrorCode.COVERAGE_UNREADABLE


def test_file_coverage_reports_its_known_lines() -> None:
    entry = FileCoverage(covered=frozenset({1, 2}), uncovered=frozenset({3}))
    assert entry.known == frozenset({1, 2, 3})


def test_counts_for_an_unmatched_file_are_zero() -> None:
    coverage = CoverageMap(source="test", files={})
    assert coverage.counts_for("src/api.py", LineRange(start=1, end=5)) == (0, 0)


def test_an_empty_query_path_never_matches() -> None:
    coverage = CoverageMap(
        source="test", files={"src/api.py": FileCoverage(frozenset({1}), frozenset())}
    )
    assert coverage.resolve("") is None
    assert coverage.resolve("   ") is None
    assert coverage.resolve("../..") is None


def test_a_shared_tail_that_spans_neither_path_fully_is_not_a_match() -> None:
    """`a/x/api.py` and `b/x/api.py` share a tail but neither contains the other."""
    coverage = CoverageMap(
        source="test", files={"a/x/api.py": FileCoverage(frozenset({1}), frozenset())}
    )
    assert coverage.status_for("b/x/api.py", LineRange(start=1, end=1)) == "unknown"


def test_a_json_document_that_is_not_an_object_is_rejected() -> None:
    """Defensive: `load_coverage` sniffs on `{`, so only a direct call can reach this."""
    from semantic_diff_weaver.coverage_map import _parse_coverage_json

    with pytest.raises(WeaverError) as raised:
        _parse_coverage_json("[1, 2, 3]")
    assert raised.value.code is ErrorCode.COVERAGE_UNREADABLE


def test_a_report_that_grows_past_the_limit_during_the_read_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    """`stat` and `read` are separate syscalls, so the read is bounded independently."""
    path = _write(tmp_path, "coverage.json", json.dumps({"files": {"a.py": {}}}) + " " * 4000)
    real_stat = Path.stat

    class Understated:
        """A stat result that under-reports size and delegates everything else."""

        def __init__(self, real: object) -> None:
            self._real = real
            self.st_size = 64

        def __getattr__(self, name: str) -> object:
            return getattr(self._real, name)

    def understate(self: Path, *args: object, **kwargs: object) -> object:
        result = real_stat(self, *args, **kwargs)
        return Understated(result) if self.name == "coverage.json" else result

    monkeypatch.setattr(Path, "stat", understate)
    with pytest.raises(WeaverError) as raised:
        load_coverage(path, _config(max_coverage_bytes=1024))
    assert raised.value.code is ErrorCode.COVERAGE_UNREADABLE
    assert "while being read" in raised.value.safe_message


def test_a_weaker_suffix_candidate_does_not_displace_the_best() -> None:
    coverage = CoverageMap(
        source="test",
        files={
            "api.py": FileCoverage(covered=frozenset(), uncovered=frozenset({1})),
            "build/src/api.py": FileCoverage(covered=frozenset({1}), uncovered=frozenset()),
            "unrelated/module.py": FileCoverage(covered=frozenset(), uncovered=frozenset({1})),
        },
    )
    assert coverage.status_for("src/api.py", LineRange(start=1, end=1)) == "covered"


def test_an_lcov_line_number_outside_the_bound_is_discarded(tmp_path: Path) -> None:
    text = "SF:src/api.py\nDA:0,1\nDA:99999999,1\nDA:4,1\nend_of_record\n"
    coverage = load_coverage(_write(tmp_path, "lcov.info", text), _config())
    assert coverage.status_for("src/api.py", LineRange(start=4, end=4)) == "covered"
    assert coverage.status_for("src/api.py", LineRange(start=1, end=3)) == "unknown"


def test_two_equal_entries_are_not_ambiguous() -> None:
    """Duplicate report entries that agree are a match, not a conflict."""
    same = FileCoverage(covered=frozenset({1}), uncovered=frozenset())
    coverage = CoverageMap(source="test", files={"a/src/api.py": same, "b/src/api.py": same})
    assert coverage.status_for("src/api.py", LineRange(start=1, end=1)) == "covered"


def test_suffix_resolution_is_memoized_per_path() -> None:
    """Resolution used to rescan and re-split every report path on every single lookup.

    `status_for` and `counts_for` each resolve independently, so a report with thousands of
    files made every finding pay two full O(files) scans. The answer is pure, so it is cached.
    """
    entry = FileCoverage(covered=frozenset({1, 2, 3}), uncovered=frozenset())
    files = {f"/ci/pkg/mod{index}/api.py": entry for index in range(50)}
    coverage = CoverageMap(source="test", files=files)

    first = coverage.resolve("pkg/mod7/api.py")
    second = coverage.resolve("pkg/mod7/api.py")

    assert first is second is entry
    assert coverage._resolved["pkg/mod7/api.py"] is entry
    # The split index is built once for the whole report, not once per lookup.
    assert len(coverage._candidate_parts) == len(files)


def test_a_memoized_miss_stays_a_miss_without_rescanning() -> None:
    coverage = CoverageMap(
        source="test",
        files={"/ci/pkg/api.py": FileCoverage(covered=frozenset({1}), uncovered=frozenset())},
    )

    assert coverage.resolve("unrelated/other.py") is None
    assert coverage.resolve("unrelated/other.py") is None
    assert coverage._resolved["unrelated/other.py"] is None


def _contexts_report(tmp_path: Path, files: dict[str, object]) -> CoverageMap:
    return load_coverage(_write(tmp_path, "coverage.json", json.dumps({"files": files})), _config())


def _executed(coverage: CoverageMap, path: str, line: int) -> set[str]:
    entry = coverage.resolve(path)
    assert entry is not None
    return {coverage.tests[test] for test in entry.contexts.get(line, ())}


def test_contexts_name_each_test_once_without_its_phase_or_parameters(tmp_path: Path) -> None:
    coverage = _contexts_report(
        tmp_path,
        {
            "src/money.py": {
                "executed_lines": [1, 2, 8],
                "missing_lines": [3],
                "contexts": {
                    "1": [""],
                    "2": [
                        "tests/test_money.py::test_parse|setup",
                        "tests/test_money.py::test_parse|run",
                        "unit",
                    ],
                    "8": [
                        "tests/test_money.py::TestRound::test_total[2.5]|run",
                        "tests/test_money.py::TestRound::test_total[a::b|c]|run",
                        "test_money.TestRound.test_total",
                    ],
                },
            }
        },
    )
    assert coverage.tests == (
        "tests/test_money.py::test_parse",
        "tests/test_money.py::TestRound::test_total",
        "test_money.TestRound.test_total",
    )
    assert _executed(coverage, "src/money.py", 1) == set()
    assert _executed(coverage, "src/money.py", 2) == {"tests/test_money.py::test_parse"}
    assert _executed(coverage, "src/money.py", 8) == {
        "tests/test_money.py::TestRound::test_total",
        "test_money.TestRound.test_total",
    }
    assert coverage.contexts_incomplete is False


@pytest.mark.parametrize(
    "contexts",
    [
        "not an object",
        {"2": "tests/test_money.py::test_parse|run"},
        {"2": [7, None, ["tests/test_money.py::test_parse"]]},
        {"x": ["tests/test_money.py::test_parse|run"]},
        {"0": ["tests/test_money.py::test_parse|run"]},
        {"99999999": ["tests/test_money.py::test_parse|run"]},
        # Not an executed line: a context can only say who ran what the report says ran.
        {"3": ["tests/test_money.py::test_parse|run"]},
        {"2": ["tests/test_money.py::test-parse|run", "::test_parse", "money.py::"]},
        {"2": ["no_dots", "dotted.not-a-name", "x" * 2000]},
    ],
    ids=[
        "not-object",
        "not-list",
        "not-text",
        "line-not-number",
        "line-zero",
        "line-out-of-range",
        "line-not-executed",
        "names-not-identifiers",
        "not-a-test",
    ],
)
def test_contexts_that_name_no_executed_line_or_no_test_are_skipped(
    tmp_path: Path, contexts: object
) -> None:
    coverage = _contexts_report(
        tmp_path,
        {"src/money.py": {"executed_lines": [1, 2], "missing_lines": [3], "contexts": contexts}},
    )
    assert coverage.tests == ()
    assert coverage.status_for("src/money.py", LineRange(start=1, end=2)) == "covered"


def test_the_contexts_of_duplicate_entries_are_merged(tmp_path: Path) -> None:
    coverage = _contexts_report(
        tmp_path,
        {
            "src/money.py": {
                "executed_lines": [2],
                "contexts": {"2": ["tests/test_a.py::test_a|run"]},
            },
            "./src/money.py": {
                "executed_lines": [2],
                "contexts": {"2": ["tests/test_b.py::test_b|run"]},
            },
        },
    )
    assert _executed(coverage, "src/money.py", 2) == {
        "tests/test_a.py::test_a",
        "tests/test_b.py::test_b",
    }


def test_a_report_counts_the_measured_lines_and_says_who_ran_which(tmp_path: Path) -> None:
    coverage = _contexts_report(
        tmp_path,
        {
            "build/src/money.py": {
                "executed_lines": [2, 4],
                "missing_lines": [3],
                "contexts": {
                    "2": ["tests/test_a.py::test_a|run", "tests/test_b.py::test_b|run"],
                    "4": ["tests/test_a.py::test_a|run"],
                },
            }
        },
    )
    lines = {"src/money.py": {2, 3, 4, 5}, "src/elsewhere.py": {1}}
    # Line 5 is not measured, a comment perhaps, and no report entry is `src/elsewhere.py`.
    assert coverage.measured(lines) == 3
    assert coverage.executed(lines) == {
        "tests/test_a.py::test_a": frozenset({("src/money.py", 2), ("src/money.py", 4)}),
        "tests/test_b.py::test_b": frozenset({("src/money.py", 2)}),
    }


def test_an_lcov_report_names_no_test(tmp_path: Path) -> None:
    coverage = load_coverage(_write(tmp_path, "coverage.info", LCOV), _config())
    assert coverage.tests == ()
    assert coverage.executed({"src/api.py": {1, 2}}) == {}


@pytest.mark.parametrize("bound", ["MAX_CONTEXT_TESTS", "MAX_CONTEXT_PAIRS"])
def test_contexts_past_a_bound_are_read_in_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bound: str
) -> None:
    monkeypatch.setattr(coverage_map, bound, 2)
    coverage = _contexts_report(
        tmp_path,
        {
            "src/money.py": {
                "executed_lines": [1, 2, 3],
                "contexts": {
                    "1": ["tests/test_a.py::test_a|run"],
                    "2": ["tests/test_b.py::test_b|run"],
                    "3": ["tests/test_c.py::test_c|run"],
                },
            },
            "src/other.py": {
                "executed_lines": [1],
                "contexts": {"1": ["tests/test_a.py::test_a|run"]},
            },
        },
    )
    assert coverage.contexts_incomplete is True
    assert _executed(coverage, "src/money.py", 1) == {"tests/test_a.py::test_a"}
    assert _executed(coverage, "src/money.py", 3) == set()
    assert _executed(coverage, "src/other.py", 1) == set()
    # The lines themselves are all still read.
    assert coverage.status_for("src/other.py", LineRange(start=1, end=1)) == "covered"


TESTS = [
    ("tests/test_money.py", "test_parse"),
    ("tests/test_money.py", "TestRound.test_total"),
    ("tests/unit/test_cart.py", "test_total"),
    ("tests/integration/test_cart.py", "test_total"),
    ("backend/tests/test_api.py", "test_get"),
]


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("tests/test_money.py::test_parse", {0}),
        ("tests/test_money.py::TestRound::test_total", {1}),
        # pytest named the file from a directory below the repository's root, or above it.
        ("tests/test_api.py::test_get", {4}),
        ("monorepo/backend/tests/test_api.py::test_get", {4}),
        ("tests/unit/test_cart.py::test_total", {2}),
        # Two files share the shorter suffix equally well.
        ("test_cart.py::test_total", set()),
        ("tests/test_money.py::test_missing", set()),
        ("tests/test_money.py::test_total", set()),
        ("test_money.test_parse", {0}),
        ("tests.test_money.TestRound.test_total", {1}),
        ("unit.test_cart.test_total", {2}),
        ("test_cart.test_total", set()),
        ("test_money.test_missing", set()),
    ],
)
def test_a_context_label_names_the_tests_it_matches_without_a_guess(
    label: str, expected: set[int]
) -> None:
    assert ContextTests(TESTS).positions(label) == expected


def test_an_exact_file_beats_a_longer_suffix() -> None:
    tests = ContextTests([("tests/test_api.py", "test_get"), ("x/tests/test_api.py", "test_get")])
    assert tests.positions("tests/test_api.py::test_get") == {0}
    assert tests.positions("tests.test_api.test_get") == {0}
    # Asked again, the answer is the one remembered.
    assert tests.positions("tests/test_api.py::test_get") == {0}


def _data_file(
    tmp_path: Path, lines: dict[str, dict[str, list[int]]], *, arcs: bool = False
) -> Path:
    """A coverage.py data file, as coverage.py itself writes one, with the lines each context
    ran: arcs between consecutive lines when branches are measured."""
    path = tmp_path / ".coverage"
    data = CoverageData(basename=str(path))
    for context, files in lines.items():
        data.set_context(context)
        if arcs:
            data.add_arcs(
                {
                    file: [(-1, numbers[0]), *pairwise(numbers), (numbers[-1], -1)]
                    for file, numbers in files.items()
                }
            )
        else:
            data.add_lines(files)
    data.write()
    return path


@pytest.mark.parametrize("arcs", [False, True], ids=["lines", "arcs"])
def test_a_data_file_says_which_test_ran_each_line(tmp_path: Path, arcs: bool) -> None:
    path = _data_file(
        tmp_path,
        {
            "": {"/ci/work/src/money.py": [1]},
            "tests/test_money.py::test_parse|run": {"/ci/work/src/money.py": [2, 4]},
            "tests/test_money.py::TestRound::test_total[2.5]|run": {
                "/ci/work/src/money.py": [4],
                "/ci/work/src/cart.py": [9],
            },
        },
        arcs=arcs,
    )
    coverage = load_coverage_data(path, _config())
    assert coverage.source == SOURCE_COVERAGE_DATA
    assert coverage.tests == (
        "tests/test_money.py::test_parse",
        "tests/test_money.py::TestRound::test_total",
    )
    assert coverage.executed({"src/money.py": {1, 2, 3, 4}}) == {
        "tests/test_money.py::test_parse": frozenset({("src/money.py", 2), ("src/money.py", 4)}),
        "tests/test_money.py::TestRound::test_total": frozenset({("src/money.py", 4)}),
    }
    assert coverage.contexts_incomplete is False


def test_numbits_are_read_as_coverage_py_writes_them() -> None:
    assert list(coverage_map._numbits(nums_to_numbits([1, 7, 8, 9, 64]))) == [1, 7, 8, 9, 64]
    # Bit zero of the first byte would be line 0, which is no line.
    assert list(coverage_map._numbits(b"\x01")) == []
    assert list(coverage_map._numbits("text")) == []
    assert list(coverage_map._numbits(b"\xff" * (coverage_map.MAX_NUMBITS_BYTES + 1))) == []


@pytest.mark.parametrize("arcs", [False, True], ids=["lines", "arcs"])
def test_a_data_file_past_its_pair_bound_is_read_in_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arcs: bool
) -> None:
    monkeypatch.setattr(coverage_map, "MAX_CONTEXT_PAIRS", 2)
    path = _data_file(tmp_path, {"tests/test_a.py::test_a|run": {"src/a.py": [1, 2, 3]}}, arcs=arcs)
    coverage = load_coverage_data(path, _config())
    assert coverage.contexts_incomplete is True


def test_a_data_file_past_its_test_bound_is_read_in_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(coverage_map, "MAX_CONTEXT_TESTS", 1)
    path = _data_file(
        tmp_path,
        {
            "tests/test_a.py::test_a|run": {"src/a.py": [1]},
            "tests/test_b.py::test_b|run": {"src/a.py": [2]},
        },
        arcs=True,
    )
    coverage = load_coverage_data(path, _config())
    assert coverage.contexts_incomplete is True
    assert coverage.tests == ("tests/test_a.py::test_a",)


INSERTS = {
    "file": "INSERT INTO file VALUES (?, ?)",
    "context": "INSERT INTO context VALUES (?, ?)",
    "line_bits": "INSERT INTO line_bits VALUES (?, ?, ?)",
    "arc": "INSERT INTO arc VALUES (?, ?, ?, ?)",
}


def _database(
    path: Path, script: str, rows: dict[str, list[tuple[object, ...]]] | None = None
) -> Path:
    connection = sqlite3.connect(path)
    connection.executescript(script)
    for table, values in (rows or {}).items():
        connection.executemany(INSERTS[table], values)
    connection.commit()
    connection.close()
    return path


TABLES = (
    "CREATE TABLE file (id integer primary key, path text);"
    "CREATE TABLE context (id integer primary key, context text);"
)


@pytest.mark.parametrize(
    ("script", "message"),
    [
        ("CREATE TABLE other (id integer);", "holds no files and contexts"),
        (
            "CREATE TABLE context (id integer primary key, context text);"
            "CREATE VIEW file AS SELECT 1 AS id, 'src/a.py' AS path;",
            "holds no files and contexts",
        ),
        ("CREATE TABLE file (id integer); CREATE TABLE context (id integer);", "not a readable"),
    ],
    ids=["no-tables", "a-view", "missing-columns"],
)
def test_a_database_that_is_no_coverage_data_is_refused(
    tmp_path: Path, script: str, message: str
) -> None:
    path = _database(tmp_path / ".coverage", script)
    with pytest.raises(WeaverError, match=message) as raised:
        load_coverage_data(path, _config())
    assert raised.value.code is ErrorCode.COVERAGE_UNREADABLE


def test_rows_of_no_file_or_test_are_skipped(tmp_path: Path) -> None:
    # Columns of no type, which hold whatever a hostile file put in them.
    path = _database(
        tmp_path / ".coverage",
        "CREATE TABLE file (id, path); CREATE TABLE context (id, context);"
        "CREATE TABLE line_bits (file_id, context_id, numbits);",
        {
            "file": [(1, "src/a.py"), (2, ""), ("x", "src/b.py")],
            "context": [(1, "tests/test_a.py::test_a|run"), (2, ""), ("y", "x.t")],
            "line_bits": [
                (1, 1, nums_to_numbits([3])),
                (2, 1, nums_to_numbits([4])),
                (1, 2, nums_to_numbits([5])),
                (9, 1, b"\xff"),
                (1, 1, "not bits"),
            ],
        },
    )
    coverage = load_coverage_data(path, _config())
    assert {path: dict(entry.contexts) for path, entry in coverage.files.items()} == {
        "src/a.py": {3: frozenset({0})}
    }


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not a database at all", "not a SQLite database"),
        (b"SQLite format 3\x00" + b"\x00" * 200, "not a readable coverage.py data file"),
    ],
    ids=["not-sqlite", "corrupt"],
)
def test_a_data_file_that_is_no_database_is_unreadable(
    tmp_path: Path, content: bytes, message: str
) -> None:
    path = tmp_path / ".coverage"
    path.write_bytes(content)
    with pytest.raises(WeaverError, match=message) as raised:
        load_coverage_data(path, _config())
    assert raised.value.code is ErrorCode.COVERAGE_UNREADABLE


def test_a_data_file_past_its_size_bound_is_refused_before_it_is_read(tmp_path: Path) -> None:
    path = _data_file(tmp_path, {"tests/test_a.py::test_a|run": {"src/a.py": [1]}})
    with pytest.raises(WeaverError, match="above the 2048-byte limit"):
        load_coverage_data(path, _config(max_coverage_data_bytes=2048))
    assert _config().rules.max_coverage_data_bytes == 100_000_000


def test_a_data_file_read_past_its_deadline_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _database(
        tmp_path / ".coverage",
        TABLES
        + "CREATE TABLE arc (file_id integer, context_id integer, fromno integer, tono integer);",
        {
            "file": [(1, "src/a.py")],
            "context": [(1, "tests/test_a.py::test_a|run")],
            "arc": [(1, 1, line, line + 1) for line in range(1, 5000)],
        },
    )
    monkeypatch.setattr(coverage_map, "COVERAGE_DATA_SECONDS", -1.0)
    with pytest.raises(WeaverError, match=r"not a readable coverage\.py data file"):
        load_coverage_data(path, _config())


class _Connection:
    """A connection without what an older or a smaller SQLite lacks, recording what is set."""

    def __init__(self) -> None:
        self.limits: list[int] = []
        self.statements: list[str] = []

    def setlimit(self, category: int, limit: int) -> None:
        self.limits.append(category)

    def set_progress_handler(self, handler: object, steps: int) -> None:
        self.handler = handler

    def execute(self, statement: str) -> None:
        self.statements.append(statement)

    def close(self) -> None:
        pass


def test_a_connection_without_dbconfig_is_still_bounded_and_kept_from_its_schema() -> None:
    connection = _Connection()
    coverage_map._harden(connection, 0.0)  # type: ignore[arg-type]
    assert connection.limits == [sqlite3.SQLITE_LIMIT_ATTACHED, sqlite3.SQLITE_LIMIT_LENGTH]
    assert "PRAGMA trusted_schema = OFF" in connection.statements
    assert "PRAGMA query_only = ON" in connection.statements


def test_a_python_whose_sqlite_reads_no_database_from_memory_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _data_file(tmp_path, {"tests/test_a.py::test_a|run": {"src/a.py": [1]}})
    monkeypatch.setattr(coverage_map.sqlite3, "connect", lambda *_: _Connection())
    with pytest.raises(WeaverError, match="cannot read a database from memory"):
        load_coverage_data(path, _config())


def test_a_data_file_that_grows_past_its_bound_while_read_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`stat` and `read` are separate syscalls, so the read is bounded independently."""
    path = _data_file(tmp_path, {"tests/test_a.py::test_a|run": {"src/a.py": [1]}})
    real_stat = Path.stat

    class Understated:
        def __init__(self, real: object) -> None:
            self._real = real
            self.st_size = 64

        def __getattr__(self, name: str) -> object:
            return getattr(self._real, name)

    def understate(self: Path, *args: object, **kwargs: object) -> object:
        result = real_stat(self, *args, **kwargs)
        return Understated(result) if self.name == ".coverage" else result

    monkeypatch.setattr(Path, "stat", understate)
    with pytest.raises(WeaverError, match="while being read"):
        load_coverage_data(path, _config(max_coverage_data_bytes=1024))


def test_a_data_file_that_cannot_be_opened_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _data_file(tmp_path, {"tests/test_a.py::test_a|run": {"src/a.py": [1]}})

    def refuse(self: Path, *args: object, **kwargs: object) -> object:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "open", refuse)
    with pytest.raises(WeaverError, match="is not readable") as raised:
        load_coverage_data(path, _config())
    assert raised.value.code is ErrorCode.COVERAGE_UNREADABLE
