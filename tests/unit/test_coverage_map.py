"""Parsing and resolving an ingested coverage report.

The report is untrusted input data. Everything here is about reading it safely and, above
all, about never converting "the report does not mention this file" into "this file has no
tests" — the failure mode that would make a misconfigured path prefix look like a coverage
gap.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from semantic_diff_weaver.coverage_map import (
    SOURCE_COVERAGE_JSON,
    SOURCE_LCOV,
    CoverageMap,
    FileCoverage,
    load_coverage,
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
        coverage,
        [("src/api.py", LineRange(start=1, end=3)), ("src/absent.py", LineRange(start=1, end=2))],
    )
    assert (changed, covered, uncovered) == (5, 3, 0)


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
