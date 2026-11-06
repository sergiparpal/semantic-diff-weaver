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
