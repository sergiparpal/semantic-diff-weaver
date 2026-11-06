"""Invariants of the CI workflow that nothing else would notice drifting."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml"


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
