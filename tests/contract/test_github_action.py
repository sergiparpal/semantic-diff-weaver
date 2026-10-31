"""The GitHub Action's base revision, resolved by its own step script in temporary repositories.

The analyzer compares two trees as they are. The action used to compare from the event's
`pull_request.base.sha`, the base branch as it stood when the pull request opened, so commits the
branch gained later read as reverted by the pull request, or as its own once merged into it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from tests.conftest import git

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="the action's steps run under bash on Linux and macOS runners",
)


@dataclass(frozen=True)
class History:
    """`main` gains a commit before the pull request opens and another after it."""

    repo: Path
    branch_point: str
    feature: str
    opened: str
    later: str
    # The feature branch once it merged `main` at `later`.
    merged: str


def _step_script() -> str:
    action = yaml.safe_load((REPOSITORY_ROOT / "action.yml").read_text(encoding="utf-8"))
    (step,) = [item for item in action["runs"]["steps"] if item.get("id") == "revisions"]
    return str(step["run"])


def _resolve(repo: Path, tmp_path: Path, **env: str) -> tuple[int, str, str]:
    """Run the step as the action does; return its status, the base it outputs, and stderr."""
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    script = tmp_path / "resolve.sh"
    script.write_text(_step_script(), encoding="utf-8")
    variables = {"BASE_REF": "", "HEAD_REF": "", "PR_BASE_SHA": "", "PR_BASE_BRANCH": "", **env}
    completed = subprocess.run(
        [str(shutil.which("bash")), str(script)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **variables, "GITHUB_OUTPUT": str(output)},
    )
    outputs = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    return completed.returncode, outputs.get("base", ""), completed.stderr


def _commit(repo: Path, text: str) -> str:
    (repo / "app.py").write_text(text, encoding="utf-8")
    git(repo, "add", "app.py")
    git(repo, "commit", "-q", "-m", text.strip())
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def history(tmp_path: Path) -> History:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    branch_point = _commit(repo, "LIMIT = 1\n")
    git(repo, "checkout", "-q", "-b", "feature")
    feature = _commit(repo, "LIMIT = 1\nRETRIES = 3\n")
    git(repo, "checkout", "-q", "main")
    opened = _commit(repo, "LIMIT = 2\n")
    later = _commit(repo, "LIMIT = 3\n")
    git(repo, "checkout", "-q", "feature")
    git(repo, "merge", "-q", "--no-edit", "-X", "theirs", "main")
    return History(
        repo=repo,
        branch_point=branch_point,
        feature=feature,
        opened=opened,
        later=later,
        merged=git(repo, "rev-parse", "HEAD"),
    )


def test_a_pull_request_is_reviewed_from_where_it_branched_off(
    history: History, tmp_path: Path
) -> None:
    """The base moved on before the pull request opened; the event names where it stood."""
    git(history.repo, "update-ref", "refs/remotes/origin/main", history.opened)
    status, base, _ = _resolve(
        history.repo,
        tmp_path,
        HEAD_REF=history.feature,
        PR_BASE_SHA=history.opened,
        PR_BASE_BRANCH="main",
    )
    assert status == 0
    assert base == history.branch_point


def test_the_base_branch_as_checked_out_outranks_the_events_stale_commit(
    history: History, tmp_path: Path
) -> None:
    """Once the head merged `main`, its commits since the event's base are not the head's own."""
    git(history.repo, "update-ref", "refs/remotes/origin/main", history.later)
    status, base, _ = _resolve(
        history.repo,
        tmp_path,
        HEAD_REF=history.merged,
        PR_BASE_SHA=history.opened,
        PR_BASE_BRANCH="main",
    )
    assert status == 0
    assert base == history.later


def test_without_the_base_branch_the_events_commit_still_gives_the_merge_base(
    history: History, tmp_path: Path
) -> None:
    status, base, _ = _resolve(
        history.repo,
        tmp_path,
        HEAD_REF=history.feature,
        PR_BASE_SHA=history.opened,
        PR_BASE_BRANCH="main",
    )
    assert status == 0
    assert base == history.branch_point


def test_an_explicit_base_ref_is_compared_as_given(history: History, tmp_path: Path) -> None:
    status, base, _ = _resolve(
        history.repo,
        tmp_path,
        BASE_REF=history.opened,
        HEAD_REF=history.feature,
        PR_BASE_SHA=history.opened,
        PR_BASE_BRANCH="main",
    )
    assert status == 0
    assert base == ""


def test_without_a_pull_request_or_a_base_ref_the_step_says_what_to_set(
    history: History, tmp_path: Path
) -> None:
    status, _, stderr = _resolve(history.repo, tmp_path)
    assert status == 1
    assert "base-ref is empty" in stderr


def test_a_revision_missing_from_the_checkout_names_fetch_depth(
    history: History, tmp_path: Path
) -> None:
    status, _, stderr = _resolve(
        history.repo, tmp_path, BASE_REF="0" * 40, HEAD_REF=history.feature
    )
    assert status == 1
    assert "fetch-depth: 0" in stderr


def test_the_outputs_carry_the_overall_risk_and_the_verdict(tmp_path: Path) -> None:
    """The analyze step reads both from the envelope's summary into the step's outputs."""
    action = yaml.safe_load((REPOSITORY_ROOT / "action.yml").read_text(encoding="utf-8"))
    (step,) = [item for item in action["runs"]["steps"] if item.get("id") == "analyze"]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python").symlink_to(sys.executable)
    envelope = tmp_path / "canned.json"
    envelope.write_text(
        '{"success": true, "analysis": {"summary": '
        '{"overall_risk": "high", "verdict": "review_required"}}, "markdown": "brief"}',
        encoding="utf-8",
    )
    stub = bin_dir / "semantic-diff-weaver"
    stub.write_text(f"#!/usr/bin/env bash\ncat '{envelope}'\n", encoding="utf-8")
    stub.chmod(0o755)
    script = tmp_path / "analyze.sh"
    script.write_text(str(step["run"]), encoding="utf-8")
    output = tmp_path / "github_output"
    variables = {
        "BASE_REF": "base",
        "HEAD_REF": "",
        "INCLUDE_GLOBS": "",
        "EXCLUDE_GLOBS": "",
        "COVERAGE_REPORT": "",
        "RISK_PROFILE": "",
        "FAIL_ON": "none",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(output),
        "GITHUB_ENV": str(tmp_path / "github_env"),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
    }
    completed = subprocess.run(
        [str(shutil.which("bash")), str(script)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **variables},
    )
    assert completed.returncode == 0, completed.stderr
    outputs = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    assert outputs["overall-risk"] == "high"
    assert outputs["verdict"] == "review_required"
