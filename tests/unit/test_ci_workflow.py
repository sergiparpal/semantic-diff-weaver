"""Invariants of the CI workflow that nothing else would notice drifting."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _jobs() -> dict[str, Any]:
    jobs: dict[str, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    return jobs


def test_ci_complete_needs_every_other_job() -> None:
    """The ruleset requires `ci-complete` alone, so a job left out of its needs gates nothing."""
    jobs = _jobs()

    assert sorted(jobs["ci-complete"]["needs"]) == sorted(set(jobs) - {"ci-complete"})


def test_the_current_hermes_is_pinned_to_a_release_tag_and_its_commit() -> None:
    """A tag can be moved; the job checks the commit it names, as actions are pinned to theirs."""
    environment = _jobs()["hermes-current"]["env"]

    assert re.fullmatch(r"v\d+\.\d+\.\d+", environment["HERMES_TAG"])
    assert re.fullmatch(r"[0-9a-f]{40}", environment["HERMES_COMMIT"])


def test_only_the_macos_leg_leaves_out_the_performance_ceilings() -> None:
    """Every other leg still times them, so a complexity regression still fails CI."""
    test = _jobs()["test"]
    legs = test["strategy"]["matrix"]["include"]
    left_out = [leg for leg in legs if leg.get("pytest-args")]

    assert [leg["os"] for leg in left_out] == ["macos-26-intel"]
    assert left_out[0]["pytest-args"] == '-m "not performance"'
    assert sum(1 for leg in legs if not leg["os"].startswith("macos")) >= 2
    pytest_step = next(step["run"] for step in test["steps"] if "pytest" in str(step.get("run")))
    assert "${{ matrix.pytest-args }}" in pytest_step


def test_a_stall_ends_the_pytest_step_before_the_job_s_timeout() -> None:
    """A job cancelled at its timeout kept no log, twice on the macOS leg (2026-11-08 and -09).

    Each bound sits inside the next: a test's, where faulthandler's outlasts pytest-timeout's
    and every mark's; the session's, with the longest test after it; the step's; the job's.
    """
    test = _jobs()["test"]
    step = next(step for step in test["steps"] if "pytest" in str(step.get("run")))
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    options = pyproject["tool"]["pytest"]["ini_options"]
    marks = [
        int(limit)
        for path in (ROOT / "tests").rglob("*.py")
        for limit in re.findall(r"mark\.timeout\((\d+)\)", path.read_text(encoding="utf-8"))
    ]
    session = re.search(r"--session-timeout=(\d+)", step["run"])

    assert options["faulthandler_exit_on_timeout"] is True
    assert int(options["faulthandler_timeout"]) > max([int(options["timeout"]), *marks])
    assert session is not None
    test_bound = int(session.group(1)) + int(options["faulthandler_timeout"])
    assert test_bound < 60 * step["timeout-minutes"]
    assert step["timeout-minutes"] < test["timeout-minutes"]
