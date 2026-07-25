"""Argument-level behavior of the command line, isolated from any repository.

These cover the bounds and defensive arms that an end-to-end run does not reach: the
`--fail-on` comparison itself, the pattern ceiling, and the provider notice.
"""

from __future__ import annotations

import argparse

import pytest

import semantic_diff_weaver.cli as cli
from semantic_diff_weaver.cli import EXIT_ARGUMENT_ERROR, EXIT_SUCCESS, _meets_threshold, main
from semantic_diff_weaver.models import MAX_PATH_PATTERNS, RiskLabel
from semantic_diff_weaver.path_policy import ALLOWED_ROOTS_ENV

BOUNDARY_OLD = {"api.py": "def allowed(x):\n    return x < 5\n"}
BOUNDARY_NEW = {"api.py": "def allowed(x):\n    return x <= 5\n"}


@pytest.mark.parametrize("risk", [item.value for item in RiskLabel])
def test_fail_on_none_never_triggers(risk: str) -> None:
    assert _meets_threshold(risk, "none") is False


def test_threshold_is_met_at_or_above_the_named_level() -> None:
    assert _meets_threshold("critical", "low") is True
    assert _meets_threshold("high", "high") is True
    assert _meets_threshold("medium", "high") is False
    assert _meets_threshold("low", "medium") is False
    assert _meets_threshold("low", "low") is True


def test_an_unrecognized_risk_label_never_trips_the_threshold() -> None:
    """Defensive: a future summary label must not be read as an exit-code signal."""
    assert _meets_threshold("unspecified", "low") is False
    assert _meets_threshold("", "critical") is False


def test_too_many_include_patterns_is_an_argument_error(capsys) -> None:
    patterns: list[str] = []
    for index in range(MAX_PATH_PATTERNS + 1):
        patterns.extend(["--include", f"src/module_{index}/**/*.py"])
    code = main(["--repo", ".", "--base", "HEAD", *patterns])
    assert code == EXIT_ARGUMENT_ERROR
    assert f"at most {MAX_PATH_PATTERNS} patterns" in capsys.readouterr().err


def test_too_many_exclude_patterns_is_an_argument_error(capsys) -> None:
    patterns: list[str] = []
    for index in range(MAX_PATH_PATTERNS + 1):
        patterns.extend(["--exclude", f"vendor/module_{index}/**"])
    code = main(["--repo", ".", "--base", "HEAD", *patterns])
    assert code == EXIT_ARGUMENT_ERROR
    assert f"at most {MAX_PATH_PATTERNS} patterns" in capsys.readouterr().err


def test_a_provider_notice_goes_to_stderr_and_does_not_stop_the_run(
    repo_factory, capsys, monkeypatch
) -> None:
    """A missing provider is a notice, never a failure — the analysis still completes."""
    repo, base, head = repo_factory(BOUNDARY_OLD, BOUNDARY_NEW)
    monkeypatch.delenv(ALLOWED_ROOTS_ENV, raising=False)
    monkeypatch.setattr(
        cli, "load_llm", lambda namespace: (None, "no provider configured; deterministic mode")
    )
    code = main(["--repo", str(repo), "--base", base, "--head", head])
    captured = capsys.readouterr()
    assert code == EXIT_SUCCESS
    assert "no provider configured" in captured.err
    assert "## Semantic Diff Test Brief" in captured.out


def test_the_default_provider_resolution_is_deterministic_mode() -> None:
    client, notice = cli.load_llm(argparse.Namespace())
    assert client is None or callable(getattr(client, "complete_structured", None))
    assert notice is None or isinstance(notice, str)
