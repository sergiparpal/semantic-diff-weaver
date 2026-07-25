"""End-to-end coverage of the standalone command line.

The suite's autouse `authorized_test_roots` fixture sets `ALLOWED_ROOTS_ENV`, which is the
operator-set case. Tests that need the CLI's own authorization path delete it first.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from semantic_diff_weaver.cli import (
    EXIT_ANALYSIS_ERROR,
    EXIT_ARGUMENT_ERROR,
    EXIT_RISK_THRESHOLD,
    EXIT_SUCCESS,
    main,
)
from semantic_diff_weaver.path_policy import ALLOWED_ROOTS_ENV
from semantic_diff_weaver.service import analyze

BOUNDARY_OLD = {"api.py": "def allowed(x):\n    return x < 5\n"}
BOUNDARY_NEW = {"api.py": "def allowed(x):\n    return x <= 5\n"}


def test_markdown_is_the_default_and_prints_the_brief(repo_factory, capsys) -> None:
    repo, base, head = repo_factory(BOUNDARY_OLD, BOUNDARY_NEW)
    code = main(["--repo", str(repo), "--base", base, "--head", head])
    captured = capsys.readouterr()
    assert code == EXIT_SUCCESS
    assert "## Semantic Diff Test Brief" in captured.out
    assert "do not verify runtime coverage" in captured.out


def test_json_prints_the_canonical_analysis(repo_factory, capsys) -> None:
    repo, base, head = repo_factory(BOUNDARY_OLD, BOUNDARY_NEW)
    code = main(["--repo", str(repo), "--base", base, "--head", head, "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_SUCCESS
    assert payload["success"] is True
    assert "behavior_changes" in payload
    assert "markdown" not in payload


def test_json_output_matches_the_service_json_transport(repo_factory, capsys) -> None:
    """`--format json` must be the same object the tool's own `json` mode returns."""
    repo, base, head = repo_factory(BOUNDARY_OLD, BOUNDARY_NEW)
    main(["--repo", str(repo), "--base", base, "--head", head, "--format", "json"])
    from_cli = json.loads(capsys.readouterr().out)
    direct = analyze(
        {
            "repo_path": str(repo),
            "base_ref": base,
            "head_ref": head,
            "output_format": "json",
        }
    )
    for payload in (from_cli, direct):
        payload["analysis_id"] = "<normalized>"
    assert from_cli == direct


def test_both_prints_the_full_envelope(repo_factory, capsys) -> None:
    repo, base, head = repo_factory(BOUNDARY_OLD, BOUNDARY_NEW)
    code = main(["--repo", str(repo), "--base", base, "--head", head, "--format", "both"])
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_SUCCESS
    assert payload["analysis"]["success"] is True
    assert "## Semantic Diff Test Brief" in payload["markdown"]


def test_include_and_exclude_narrow_the_scope(repo_factory, capsys) -> None:
    repo, base, head = repo_factory(
        {**BOUNDARY_OLD, "other.py": "def other(x):\n    return x < 1\n"},
        {**BOUNDARY_NEW, "other.py": "def other(x):\n    return x <= 1\n"},
    )
    main(
        [
            "--repo",
            str(repo),
            "--base",
            base,
            "--head",
            head,
            "--format",
            "json",
            "--include",
            "api.py",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    paths = {
        item["path"] for behavior in payload["behavior_changes"] for item in behavior["evidence"]
    }
    assert paths == {"api.py"}


def test_no_python_change_succeeds(repo_factory, capsys) -> None:
    repo, base, head = repo_factory({"README.md": "old\n"}, {"README.md": "new\n"})
    code = main(["--repo", str(repo), "--base", base, "--head", head, "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_SUCCESS
    assert payload["success"] is True
    assert payload["behavior_changes"] == []


def test_invalid_ref_is_an_analysis_error(repo_factory, capsys) -> None:
    repo, base, _ = repo_factory(BOUNDARY_OLD, BOUNDARY_NEW)
    code = main(["--repo", str(repo), "--base", base, "--head", "no-such-ref", "--no-llm"])
    captured = capsys.readouterr()
    assert code == EXIT_ANALYSIS_ERROR
    assert captured.out == ""
    assert "invalid_ref" in captured.err
    # Exactly the message and its remediation — never a traceback.
    assert "Traceback" not in captured.err
    assert len(captured.err.strip().splitlines()) == 2


def test_no_llm_skips_provider_resolution_entirely(repo_factory, capsys, monkeypatch) -> None:
    """`--no-llm` must emit no provider notice, even with a key present."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    repo, base, head = repo_factory(BOUNDARY_OLD, BOUNDARY_NEW)
    code = main(
        ["--repo", str(repo), "--base", base, "--head", head, "--format", "json", "--no-llm"]
    )
    captured = capsys.readouterr()
    assert code == EXIT_SUCCESS
    assert captured.err == ""
    assert json.loads(captured.out)["deterministic_mode"] is True


def test_missing_required_base_is_an_argument_error(capsys) -> None:
    code = main(["--repo", "."])
    assert code == EXIT_ARGUMENT_ERROR
    assert "--base" in capsys.readouterr().err


def test_unknown_format_is_an_argument_error(capsys) -> None:
    code = main(["--repo", ".", "--base", "HEAD", "--format", "sarif"])
    assert code == EXIT_ARGUMENT_ERROR
    assert "--format" in capsys.readouterr().err


def test_help_exits_successfully(capsys) -> None:
    assert main(["--help"]) == EXIT_SUCCESS
    assert "--fail-on" in capsys.readouterr().out


def test_fail_on_returns_the_threshold_exit_code(repo_factory, capsys) -> None:
    repo, base, head = repo_factory(BOUNDARY_OLD, BOUNDARY_NEW)
    arguments = ["--repo", str(repo), "--base", base, "--head", head, "--format", "json"]
    main([*arguments])
    risk = json.loads(capsys.readouterr().out)["summary"]["overall_risk"]
    assert risk == "medium"

    assert main([*arguments, "--fail-on", "none"]) == EXIT_SUCCESS
    capsys.readouterr()
    assert main([*arguments, "--fail-on", "low"]) == EXIT_RISK_THRESHOLD
    capsys.readouterr()
    assert main([*arguments, "--fail-on", "medium"]) == EXIT_RISK_THRESHOLD
    threshold_err = capsys.readouterr().err
    assert "reaches --fail-on medium" in threshold_err
    assert main([*arguments, "--fail-on", "high"]) == EXIT_SUCCESS
    capsys.readouterr()
    assert main([*arguments, "--fail-on", "critical"]) == EXIT_SUCCESS


def test_fail_on_still_prints_the_report(repo_factory, capsys) -> None:
    """A threshold breach is a signal, not a reason to withhold the analysis."""
    repo, base, head = repo_factory(BOUNDARY_OLD, BOUNDARY_NEW)
    code = main(["--repo", str(repo), "--base", base, "--head", head, "--fail-on", "low"])
    assert code == EXIT_RISK_THRESHOLD
    assert "## Semantic Diff Test Brief" in capsys.readouterr().out


def test_risk_profile_is_applied(repo_factory, capsys, monkeypatch) -> None:
    repo, base, head = repo_factory(BOUNDARY_OLD, BOUNDARY_NEW)
    profile = Path(repo) / "profile.yaml"
    profile.write_text(
        "version: 1\ncritical_paths:\n  - pattern: 'api.py'\n    weight: 100\n",
        encoding="utf-8",
    )
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
            "--risk-profile",
            str(profile),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_SUCCESS
    assert payload["summary"]["overall_risk"] in {"high", "critical"}


def test_python_dash_m_entry_point_is_wired(repo_factory) -> None:
    import subprocess
    import sys

    repo, base, head = repo_factory(BOUNDARY_OLD, BOUNDARY_NEW)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "semantic_diff_weaver",
            "--repo",
            str(repo),
            "--base",
            base,
            "--head",
            head,
        ],
        capture_output=True,
        check=False,
        cwd=Path(__file__).parents[2],
        encoding="utf-8",
        shell=False,
    )
    assert completed.returncode == EXIT_SUCCESS, completed.stderr
    assert "## Semantic Diff Test Brief" in completed.stdout


@pytest.mark.parametrize("output_format", ["json", "markdown", "both"])
def test_every_output_format_is_reachable(repo_factory, capsys, output_format: str) -> None:
    repo, base, head = repo_factory(BOUNDARY_OLD, BOUNDARY_NEW)
    code = main(["--repo", str(repo), "--base", base, "--head", head, "--format", output_format])
    assert code == EXIT_SUCCESS
    assert capsys.readouterr().out.strip()
