"""Offline checks of `scripts/verify_hermes_install.py`'s judgments.

CI's `hermes-current` job runs the script against a real Hermes, which only ever proves that the
current plugin installs. These prove the other half without Hermes: each result the script exists
to refuse is refused, with a message that names it.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.verify_hermes_install import (
    VerificationError,
    dependency_python,
    load_problems,
    unacceptable_findings,
)

HOSTILE_FIXTURE = "tests/security/test_hidden_changes.py"


def _finding(severity: str, file: str) -> SimpleNamespace:
    return SimpleNamespace(
        pattern_id="echo_pipe_exec", severity=severity, category="obfuscation", file=file, line=112
    )


def test_a_caution_verdict_whose_high_findings_lie_in_the_tests_is_accepted() -> None:
    findings = [_finding("high", HOSTILE_FIXTURE), _finding("medium", "README.md")]

    assert unacceptable_findings("caution", findings) == []


@pytest.mark.parametrize("severity", ["high", "critical"])
def test_a_high_or_critical_finding_outside_the_tests_is_refused(severity: str) -> None:
    problems = unacceptable_findings("caution", [_finding(severity, "semantic_diff_weaver/x.py")])

    assert problems == [
        f"a {severity} obfuscation finding outside tests/: semantic_diff_weaver/x.py:112 "
        "(echo_pipe_exec)"
    ]


def test_a_directory_named_like_the_test_tree_is_not_the_test_tree() -> None:
    assert unacceptable_findings("caution", [_finding("high", "tests_data/fixture.py")])


def test_a_dangerous_verdict_is_refused_whatever_its_findings() -> None:
    assert unacceptable_findings("dangerous", [_finding("high", HOSTILE_FIXTURE)]) == [
        "the verdict is dangerous, which no Hermes install can accept"
    ]


def _environment_python(home: Path, name: str) -> Path:
    python = home / "installs" / "f880aab2" / "environments" / name / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    return python


def test_the_plugin_runs_with_the_dependency_environment_hermes_made(tmp_path: Path) -> None:
    python = _environment_python(tmp_path, "bd70299c")

    assert dependency_python(tmp_path) == python


def test_without_a_dependency_environment_the_plugin_runs_in_hermes_own(tmp_path: Path) -> None:
    assert dependency_python(tmp_path) == Path(sys.executable)


def test_two_dependency_environments_in_a_fresh_home_are_refused(tmp_path: Path) -> None:
    _environment_python(tmp_path, "first")
    _environment_python(tmp_path, "second")

    with pytest.raises(VerificationError, match="expected one dependency environment"):
        dependency_python(tmp_path)


LOADED: dict[str, Any] = {
    "entries": [{"enabled": True, "error": None, "tools": 2}],
    "analysis": {"error": None, "behavior_changes": 1},
    "versions": {"pydantic": "2.14.1", "PyYAML": "6.0.3"},
}


def test_a_plugin_loaded_with_both_tools_that_analyzes_a_change_passes() -> None:
    assert load_problems(LOADED) == []


@pytest.mark.parametrize(
    ("fault", "named"),
    [
        pytest.param({"entries": []}, "found []", id="not-listed"),
        pytest.param(
            {"entries": [{"enabled": False, "error": None, "tools": 2}]},
            "did not load cleanly",
            id="disabled",
        ),
        pytest.param(
            {"entries": [{"enabled": True, "error": "No module named 'yaml'", "tools": 0}]},
            "did not load cleanly",
            id="load-error",
        ),
        pytest.param(
            {"entries": [{"enabled": True, "error": None, "tools": 1}]},
            "did not register exactly",
            id="one-tool",
        ),
        pytest.param(
            {"analysis": {"error": {"code": "path_outside_repository"}}},
            "the analysis failed",
            id="analysis-error",
        ),
        pytest.param(
            {"analysis": {"error": None, "behavior_changes": 0}},
            "no behavior change",
            id="nothing-found",
        ),
    ],
)
def test_each_fault_of_the_installed_plugin_is_named(fault: dict[str, Any], named: str) -> None:
    problems = load_problems({**LOADED, **fault})

    assert len(problems) == 1
    assert named in problems[0]
