"""A coverage report with per-test contexts says which tests ran a change, end to end."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from coverage import CoverageData

import semantic_diff_weaver.coverage_map as coverage_map
from semantic_diff_weaver.cli import EXIT_SUCCESS, main
from semantic_diff_weaver.errors import ErrorCode, WeaverError
from semantic_diff_weaver.path_policy import ALLOWED_ROOTS_ENV
from semantic_diff_weaver.service import analyze

MONEY = "def parse_amount(text):\n    if {}:\n        return 0\n    return int(text)\n"
# The test loads the module by a name it builds, so nothing static ties it to the change.
MISC = (
    "import importlib\n\n\n"
    "def test_loaded_by_name():\n"
    "    module = importlib.import_module('shop.' + 'money')\n"
    "    assert getattr(module, 'parse_' + 'amount')('') == 0\n\n\n"
    "def test_unrelated():\n"
    "    assert 1 + 1 == 2\n"
)
EXECUTED = "executed the changed line in the coverage report"


def _repository(repo_factory) -> tuple[Path, str, str]:
    files = {"src/shop/__init__.py": "", "tests/test_misc.py": MISC}
    return repo_factory(
        {**files, "src/shop/money.py": MONEY.format("not text")},
        {**files, "src/shop/money.py": MONEY.format("not text or text == '0'")},
    )


def _report(repo: Path, contexts: dict[str, list[str]] | None) -> Path:
    entry: dict[str, Any] = {"executed_lines": [1, 2, 3, 4], "missing_lines": []}
    if contexts is not None:
        entry["contexts"] = contexts
    path = repo.parent / "coverage.json"
    path.write_text(json.dumps({"files": {"src/shop/money.py": entry}}), encoding="utf-8")
    return path


def _analysis(repo: Path, base: str, head: str, report: Path | None) -> dict[str, Any]:
    request: dict[str, Any] = {
        "repo_path": str(repo),
        "base_ref": base,
        "head_ref": head,
        "output_format": "both",
    }
    if report is not None:
        request["coverage_report"] = str(report)
    envelope = analyze(request)
    assert envelope["success"] is True
    return envelope


def _candidates(envelope: dict[str, Any]) -> dict[str, list[str]]:
    return {
        test["symbol"]: test["match_reasons"]
        for obligation in envelope["analysis"]["test_obligations"]
        for test in obligation["candidate_existing_tests"]
    }


def test_a_test_the_report_saw_run_the_change_is_its_candidate(repo_factory) -> None:
    repo, base, head = _repository(repo_factory)
    assert _candidates(_analysis(repo, base, head, None)) == {}
    report = _report(repo, {"2": ["tests/test_misc.py::test_loaded_by_name|run"], "1": [""]})
    envelope = _analysis(repo, base, head, report)
    assert _candidates(envelope) == {
        "test_loaded_by_name": [EXECUTED, "direct module or symbol import"]
    }
    assert envelope["analysis"]["warnings"] == []
    (line,) = (item for item in envelope["markdown"].splitlines() if EXECUTED in item)
    assert line.startswith("  - Candidate existing tests (unverified): ")


def test_a_report_run_from_another_directory_matches_by_suffix(repo_factory) -> None:
    repo, base, head = _repository(repo_factory)
    report = _report(repo, {"2": ["test_misc.test_loaded_by_name", "checkout/tests/x.py::t|run"]})
    assert _candidates(_analysis(repo, base, head, report)) == {
        "test_loaded_by_name": [EXECUTED, "direct module or symbol import"]
    }


def test_a_report_without_contexts_names_no_test(repo_factory) -> None:
    repo, base, head = _repository(repo_factory)
    envelope = _analysis(repo, base, head, _report(repo, None))
    assert _candidates(envelope) == {}
    assert envelope["analysis"]["warnings"] == []


def test_contexts_that_name_no_test_of_the_repository_are_a_warning(repo_factory) -> None:
    repo, base, head = _repository(repo_factory)
    report = _report(repo, {"2": ["elsewhere/test_other.py::test_other|run"]})
    envelope = _analysis(repo, base, head, report)
    assert _candidates(envelope) == {}
    assert (
        "The coverage test contexts name tests that executed the changed lines, but none of "
        "them is a test the analysis found; check that their test IDs share a path suffix with "
        "the repository's."
    ) in envelope["analysis"]["warnings"]


def test_contexts_past_their_bound_are_a_warning(repo_factory, monkeypatch) -> None:
    monkeypatch.setattr(coverage_map, "MAX_CONTEXT_PAIRS", 1)
    repo, base, head = _repository(repo_factory)
    report = _report(
        repo,
        {
            "2": ["tests/test_misc.py::test_loaded_by_name|run"],
            "3": ["tests/test_misc.py::test_loaded_by_name|run"],
        },
    )
    envelope = _analysis(repo, base, head, report)
    assert _candidates(envelope) == {
        "test_loaded_by_name": [EXECUTED, "direct module or symbol import"]
    }
    assert (
        "The coverage test contexts exceeded their bounds and were read in part; tests that "
        "executed the changed lines may be missing."
    ) in envelope["analysis"]["warnings"]


def test_a_configuration_key_beside_the_code_is_left_to_its_names(repo_factory) -> None:
    """Coverage measures Python, so a changed key keeps the candidates its names find."""
    named = "version: 1\npaths:\n  config_files: ['config/*.yaml']\n"
    files = {
        "src/shop/__init__.py": "",
        "tests/test_misc.py": MISC,
        ".semantic-diff-weaver.yaml": named,
    }
    repo, base, head = repo_factory(
        {
            **files,
            "src/shop/money.py": MONEY.format("not text"),
            "config/app.yaml": "retry_limit: 3\n",
        },
        {
            **files,
            "src/shop/money.py": MONEY.format("not text or text == '0'"),
            "config/app.yaml": "retry_limit: 5\n",
        },
    )
    report = _report(repo, {"2": ["tests/test_misc.py::test_loaded_by_name|run"]})
    envelope = _analysis(repo, base, head, report)
    paths = {item["evidence"][0]["path"] for item in envelope["analysis"]["behavior_changes"]}
    assert paths == {"src/shop/money.py", "config/app.yaml"}
    assert _candidates(envelope) == {
        "test_loaded_by_name": [EXECUTED, "direct module or symbol import"]
    }


def _data_file(repo: Path, contexts: dict[str, list[int]]) -> Path:
    """The `.coverage` data file of a run, beside the report, as coverage.py writes it."""
    path = repo.parent / ".coverage"
    data = CoverageData(basename=str(path))
    for context, lines in contexts.items():
        data.set_context(context)
        data.add_lines({str(repo / "src" / "shop" / "money.py"): lines})
    data.write()
    return path


def _with_data(repo: Path, base: str, head: str, report: Path, data: Path) -> dict[str, Any]:
    envelope = analyze(
        {
            "repo_path": str(repo),
            "base_ref": base,
            "head_ref": head,
            "output_format": "both",
            "coverage_report": str(report),
            "coverage_data": str(data),
        }
    )
    assert envelope["success"] is True
    return envelope


def test_the_data_file_of_the_run_says_who_ran_the_change(repo_factory) -> None:
    repo, base, head = _repository(repo_factory)
    # The report's own contexts are left for the data file's.
    report = _report(repo, {"2": ["tests/test_misc.py::test_unrelated|run"]})
    data = _data_file(
        repo,
        {"": [1], "tests/test_misc.py::test_loaded_by_name|run": [2, 3], "unit": [2]},
    )
    envelope = _with_data(repo, base, head, report, data)
    assert _candidates(envelope) == {
        "test_loaded_by_name": [EXECUTED, "direct module or symbol import"]
    }
    assert envelope["analysis"]["warnings"] == []


def test_a_data_file_recorded_without_contexts_is_a_warning(repo_factory) -> None:
    repo, base, head = _repository(repo_factory)
    envelope = _with_data(repo, base, head, _report(repo, None), _data_file(repo, {"": [1, 2]}))
    assert _candidates(envelope) == {}
    assert (
        "The coverage data file records no per-test contexts; record them with "
        "pytest --cov-context=test."
    ) in envelope["analysis"]["warnings"]


def test_a_data_file_without_its_report_is_refused(repo_factory) -> None:
    repo, base, head = _repository(repo_factory)
    data = _data_file(repo, {"tests/test_misc.py::test_loaded_by_name|run": [2]})
    with pytest.raises(WeaverError, match="without the coverage report of the same run") as raised:
        analyze(
            {"repo_path": str(repo), "base_ref": base, "head_ref": head, "coverage_data": str(data)}
        )
    assert raised.value.code is ErrorCode.CONFIGURATION_ERROR


def test_an_unreadable_data_file_is_an_error(repo_factory) -> None:
    repo, base, head = _repository(repo_factory)
    data = repo.parent / ".coverage"
    data.write_text("not a database", encoding="utf-8")
    with pytest.raises(WeaverError, match="not a SQLite database") as raised:
        analyze(
            {
                "repo_path": str(repo),
                "base_ref": base,
                "head_ref": head,
                "coverage_report": str(_report(repo, None)),
                "coverage_data": str(data),
            }
        )
    assert raised.value.code is ErrorCode.COVERAGE_UNREADABLE


def test_the_cli_takes_the_data_file_beside_the_report(repo_factory, capsys, monkeypatch) -> None:
    repo, base, head = _repository(repo_factory)
    report = _report(repo, None)
    data = _data_file(repo, {"tests/test_misc.py::test_loaded_by_name|run": [2]})
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
            "--no-llm",
            "--coverage",
            str(report),
            "--coverage-data",
            str(data),
            "--allow-root",
            str(repo.parent),
        ]
    )
    assert code == EXIT_SUCCESS
    analysis = json.loads(capsys.readouterr().out)
    reasons = [
        test["match_reasons"]
        for obligation in analysis["test_obligations"]
        for test in obligation["candidate_existing_tests"]
    ]
    assert [EXECUTED, "direct module or symbol import"] in reasons


def test_a_line_the_data_file_saw_run_counts_when_the_report_misses_its_file(
    repo_factory,
) -> None:
    """A report cut to other files measures none of the changed lines, which a test still ran."""
    repo, base, head = _repository(repo_factory)
    report = repo.parent / "coverage.json"
    report.write_text(
        json.dumps({"files": {"src/shop/other.py": {"executed_lines": [1]}}}), encoding="utf-8"
    )
    data = _data_file(repo, {"tests/test_misc.py::test_loaded_by_name|run": [2]})
    assert _candidates(_with_data(repo, base, head, report, data)) == {
        "test_loaded_by_name": [EXECUTED, "direct module or symbol import"]
    }
