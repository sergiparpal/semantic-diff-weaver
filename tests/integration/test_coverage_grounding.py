"""Coverage grounding end to end, over a real temporary repository.

What this pins is the product claim: with a report supplied, an uncovered changed function is
reported as uncovered and ranks above a covered one — and without a report, nothing about the
output changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from semantic_diff_weaver.cli import EXIT_SUCCESS, main
from semantic_diff_weaver.models import CoverageStatus
from semantic_diff_weaver.path_policy import ALLOWED_ROOTS_ENV
from semantic_diff_weaver.service import analyze

# Two distinct behavior categories, so their obligations do not merge by semantics and each
# keeps its own grounded verdict. The merge rule itself is pinned separately below.
OLD = {
    "api.py": "def allowed(x):\n    return x < 5\n\n\ndef audited(y):\n    return {'old': y}\n",
}
NEW = {
    "api.py": "def allowed(x):\n    return x <= 5\n\n\ndef audited(y):\n    return {'new': y}\n",
}

# `allowed` (lines 1-2) is executed by the suite; `audited` (lines 5-6) is not.
REPORT = {
    "meta": {"version": "7.6.1"},
    "files": {
        "/ci/workspace/api.py": {"executed_lines": [1, 2], "missing_lines": [5, 6]},
    },
    "totals": {},
}


def _analyze(repo: Path, base: str, head: str, report: Path | None) -> dict[str, Any]:
    request: dict[str, Any] = {
        "repo_path": str(repo),
        "base_ref": base,
        "head_ref": head,
        "output_format": "both",
    }
    if report is not None:
        request["coverage_report"] = str(report)
    return analyze(request)


def _write_report(repo: Path, payload: dict[str, Any] | str | None = None) -> Path:
    path = repo / "coverage.json"
    text = payload if isinstance(payload, str) else json.dumps(payload or REPORT)
    path.write_text(text, encoding="utf-8")
    return path


def _by_symbol(analysis: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for behavior in analysis["behavior_changes"]:
        for item in behavior["evidence"]:
            if item["symbol"]:
                result[item["symbol"]] = behavior
    return result


def test_an_uncovered_changed_function_is_reported_uncovered(repo_factory) -> None:
    repo, base, head = repo_factory(OLD, NEW)
    analysis = _analyze(Path(repo), base, head, _write_report(Path(repo)))["analysis"]
    behaviors = _by_symbol(analysis)
    linked = {
        behavior_id: obligation
        for obligation in analysis["test_obligations"]
        for behavior_id in obligation["behavior_change_ids"]
    }
    assert linked[behaviors["audited"]["id"]]["coverage_status"] == (CoverageStatus.UNCOVERED.value)
    assert linked[behaviors["allowed"]["id"]]["coverage_status"] == CoverageStatus.COVERED.value


def test_an_uncovered_change_outranks_a_covered_one(repo_factory) -> None:
    """The scoring hook: the same change ranks higher when its lines are unexecuted."""
    repo, base, head = repo_factory(OLD, NEW)
    analysis = _analyze(Path(repo), base, head, _write_report(Path(repo)))["analysis"]
    behaviors = _by_symbol(analysis)
    uncovered = behaviors["audited"]["score_explanation"]["test_gap_weight"]
    covered = behaviors["allowed"]["score_explanation"]["test_gap_weight"]
    assert uncovered > covered
    assert behaviors["audited"]["risk_score"] > behaviors["allowed"]["risk_score"]


def test_the_gap_is_raised_relative_to_the_ungrounded_run(repo_factory) -> None:
    repo, base, head = repo_factory(OLD, NEW)
    without = _by_symbol(_analyze(Path(repo), base, head, None)["analysis"])
    with_report = _by_symbol(
        _analyze(Path(repo), base, head, _write_report(Path(repo)))["analysis"]
    )
    assert (
        with_report["audited"]["score_explanation"]["test_gap_weight"]
        > without["audited"]["score_explanation"]["test_gap_weight"]
    )
    assert (
        with_report["allowed"]["score_explanation"]["test_gap_weight"]
        < without["allowed"]["score_explanation"]["test_gap_weight"]
    )


def test_no_report_changes_nothing(repo_factory) -> None:
    """Coverage is strictly additive: without a report the analysis is what it always was."""
    repo, base, head = repo_factory(OLD, NEW)
    analysis = _analyze(Path(repo), base, head, None)["analysis"]
    assert analysis["coverage"] is None
    assert all(
        obligation["coverage_status"]
        not in {CoverageStatus.COVERED.value, CoverageStatus.UNCOVERED.value}
        for obligation in analysis["test_obligations"]
    )


def test_the_summary_reports_what_the_report_matched(repo_factory) -> None:
    repo, base, head = repo_factory(OLD, NEW)
    analysis = _analyze(Path(repo), base, head, _write_report(Path(repo)))["analysis"]
    coverage = analysis["coverage"]
    assert coverage["source"] == "coverage.py-json"
    assert coverage["matched_files"] == 1
    assert coverage["unmatched_files"] == 0
    # The diff changes line 2 (executed) and line 6 (missed) — not the functions' other lines,
    # which a span-based count used to include.
    assert coverage["changed_lines"] == 2
    assert coverage["covered_lines"] == 1
    assert coverage["uncovered_lines"] == 1


def test_the_brief_names_the_ingested_report(repo_factory) -> None:
    repo, base, head = repo_factory(OLD, NEW)
    envelope = _analyze(Path(repo), base, head, _write_report(Path(repo)))
    markdown = envelope["markdown"]
    assert "**Coverage (ingested coverage.py-json report, not produced by this tool):**" in markdown
    assert "1 covered and 1 uncovered of 2 changed line(s)" in markdown


def test_the_brief_omits_coverage_without_a_report(repo_factory) -> None:
    repo, base, head = repo_factory(OLD, NEW)
    assert "**Coverage" not in _analyze(Path(repo), base, head, None)["markdown"]


def test_candidates_stay_unverified_even_when_covered(repo_factory) -> None:
    """Coverage proves a line ran, never that a candidate test asserts the changed behavior."""
    repo, base, head = repo_factory(
        {
            **OLD,
            "tests/test_api.py": "from api import allowed\n\n\ndef test_allowed():\n    assert allowed(4)\n",
        },
        {
            **NEW,
            "tests/test_api.py": "from api import allowed\n\n\ndef test_allowed():\n    assert allowed(5)\n",
        },
    )
    analysis = _analyze(Path(repo), base, head, _write_report(Path(repo)))["analysis"]
    assert any(
        obligation["candidate_existing_tests"] for obligation in analysis["test_obligations"]
    )
    assert all(
        candidate["verified"] is False
        for obligation in analysis["test_obligations"]
        for candidate in obligation["candidate_existing_tests"]
    )
    assert (
        "do not verify runtime coverage"
        in _analyze(Path(repo), base, head, _write_report(Path(repo)))["markdown"]
    )


def test_an_unmatched_report_warns_instead_of_claiming_a_gap(repo_factory) -> None:
    """A path-prefix mismatch must be visible, not reported as missing tests."""
    repo, base, head = repo_factory(OLD, NEW)
    report = _write_report(Path(repo), {"files": {"unrelated/module.py": {"executed_lines": [1]}}})
    envelope = _analyze(Path(repo), base, head, report)
    analysis = envelope["analysis"]
    assert analysis["coverage"]["matched_files"] == 0
    assert analysis["coverage"]["unmatched_files"] >= 1
    assert any("did not match" in warning for warning in analysis["warnings"])
    assert all(
        obligation["coverage_status"] != CoverageStatus.UNCOVERED.value
        for obligation in analysis["test_obligations"]
    )


def test_lcov_grounds_the_same_way(repo_factory) -> None:
    repo, base, head = repo_factory(OLD, NEW)
    report = _write_report(
        Path(repo),
        "SF:/ci/workspace/api.py\nDA:1,3\nDA:2,3\nDA:5,0\nDA:6,0\nend_of_record\n",
    )
    analysis = _analyze(Path(repo), base, head, report)["analysis"]
    assert analysis["coverage"]["source"] == "lcov"
    behaviors = _by_symbol(analysis)
    assert (
        behaviors["audited"]["score_explanation"]["test_gap_weight"]
        > behaviors["allowed"]["score_explanation"]["test_gap_weight"]
    )


def test_the_cli_flag_grounds_coverage(repo_factory, capsys, monkeypatch) -> None:
    repo, base, head = repo_factory(OLD, NEW)
    report = _write_report(Path(repo))
    monkeypatch.delenv(ALLOWED_ROOTS_ENV, raising=False)
    code = main(
        [
            "--repo",
            str(repo),
            "--base",
            base,
            "--head",
            head,
            "--format",
            "json",
            "--coverage",
            str(report),
            "--no-llm",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_SUCCESS
    assert payload["coverage"]["source"] == "coverage.py-json"
    assert any(
        obligation["coverage_status"] == CoverageStatus.UNCOVERED.value
        for obligation in payload["test_obligations"]
    )


def test_an_unreadable_report_is_a_cli_analysis_error(repo_factory, capsys, monkeypatch) -> None:
    from semantic_diff_weaver.cli import EXIT_ANALYSIS_ERROR

    repo, base, head = repo_factory(OLD, NEW)
    report = _write_report(Path(repo), "not a coverage report")
    monkeypatch.delenv(ALLOWED_ROOTS_ENV, raising=False)
    code = main(
        [
            "--repo",
            str(repo),
            "--base",
            base,
            "--head",
            head,
            "--coverage",
            str(report),
            "--no-llm",
        ]
    )
    captured = capsys.readouterr()
    assert code == EXIT_ANALYSIS_ERROR
    assert "coverage_unreadable" in captured.err


def test_changes_with_split_verdicts_never_share_an_obligation(repo_factory) -> None:
    """Two identical changes, one covered and one not, each keep their own verdict.

    They used to share obligations, which could claim neither verdict and fell back to static
    matching: claiming either for both would report something the report does not support.
    Obligations now merge only between behaviors whose coverage status agrees.
    """
    repo, base, head = repo_factory(
        {"api.py": "def a(x):\n    return x < 5\n\n\ndef b(y):\n    return y < 5\n"},
        {"api.py": "def a(x):\n    return x <= 5\n\n\ndef b(y):\n    return y <= 5\n"},
    )
    analysis = _analyze(Path(repo), base, head, _write_report(Path(repo)))["analysis"]
    assert all(len(item["behavior_change_ids"]) == 1 for item in analysis["test_obligations"])
    assert _verdicts(analysis) == {
        "a": CoverageStatus.COVERED.value,
        "b": CoverageStatus.UNCOVERED.value,
    }
    # The per-finding score reflects coverage too.
    behaviors = _by_symbol(analysis)
    assert (
        behaviors["b"]["score_explanation"]["test_gap_weight"]
        > behaviors["a"]["score_explanation"]["test_gap_weight"]
    )


def _verdicts(analysis: dict[str, Any]) -> dict[str, str]:
    """Each changed symbol's grounded coverage verdict, read from its linked obligation."""
    by_behavior = {
        behavior_id: obligation["coverage_status"]
        for obligation in analysis["test_obligations"]
        for behavior_id in obligation["behavior_change_ids"]
    }
    return {symbol: by_behavior[item["id"]] for symbol, item in _by_symbol(analysis).items()}


def test_an_uncovered_unchanged_line_does_not_hide_a_covered_change(repo_factory) -> None:
    """Only the lines the diff changed are judged, not the rest of the symbol.

    Line 3 is missed by the suite but untouched by the change; judging the whole function
    made the verdict `unknown` although the one changed line, line 2, is executed.
    """
    old = {"calc.py": "def scale(x):\n    y = x + 1\n    z = y * 2\n    return z\n"}
    new = {"calc.py": "def scale(x):\n    y = x + 2\n    z = y * 2\n    return z\n"}
    report = {
        "files": {"calc.py": {"executed_lines": [1, 2, 4], "missing_lines": [3]}},
    }
    repo, base, head = repo_factory(old, new)
    analysis = _analyze(Path(repo), base, head, _write_report(Path(repo), report))["analysis"]
    assert _verdicts(analysis)["scale"] == CoverageStatus.COVERED.value
    assert analysis["coverage"]["changed_lines"] == 1


def test_each_changed_line_counts_once_however_many_findings_rest_on_it(repo_factory) -> None:
    """A module-level change and a function change in one file overlap: `<module>` spans it all.

    Summing spans counted the whole file plus the function again — more changed lines than the
    file had. The diff here changes exactly lines 1 and 5.
    """
    old = {"limits.py": "LIMIT = 3\n\n\ndef allowed(x):\n    return x < LIMIT\n"}
    new = {"limits.py": "LIMIT = 4\n\n\ndef allowed(x):\n    return x <= LIMIT\n"}
    report = {"files": {"limits.py": {"executed_lines": [1, 4, 5], "missing_lines": []}}}
    repo, base, head = repo_factory(old, new)
    analysis = _analyze(Path(repo), base, head, _write_report(Path(repo), report))["analysis"]
    symbols = {item["symbol"] for b in analysis["behavior_changes"] for item in b["evidence"]}
    assert {"<module>", "allowed"} <= symbols
    coverage = analysis["coverage"]
    assert (coverage["changed_lines"], coverage["covered_lines"]) == (2, 2)
    assert coverage["uncovered_lines"] == 0


def test_a_removed_function_gets_no_coverage_claim(repo_factory) -> None:
    """A removed symbol has no head-side lines, so coverage can say nothing about it.

    Its evidence has no `new_lines`; the finding stays `unknown` — the static semantics — and
    adds nothing to the summary, while the covered change beside it is still claimed.
    """
    old = {"api.py": "def allowed(x):\n    return x < 5\n\n\ndef legacy(y):\n    return y\n"}
    new = {"api.py": "def allowed(x):\n    return x <= 5\n"}
    report = {"files": {"api.py": {"executed_lines": [1, 2], "missing_lines": []}}}
    repo, base, head = repo_factory(old, new)
    analysis = _analyze(Path(repo), base, head, _write_report(Path(repo), report))["analysis"]
    removed = [
        item
        for behavior in analysis["behavior_changes"]
        for item in behavior["evidence"]
        if item["symbol"] == "legacy"
    ]
    assert [item["new_lines"] for item in removed] == [None]
    verdicts = _verdicts(analysis)
    assert verdicts["allowed"] == CoverageStatus.COVERED.value
    assert verdicts["legacy"] not in {CoverageStatus.COVERED.value, CoverageStatus.UNCOVERED.value}
    coverage = analysis["coverage"]
    assert (coverage["changed_lines"], coverage["covered_lines"], coverage["uncovered_lines"]) == (
        1,
        1,
        0,
    )
