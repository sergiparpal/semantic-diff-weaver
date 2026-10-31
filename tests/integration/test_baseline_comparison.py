"""An analysis compared with an earlier one of the same change, as a pull request moves on."""

from __future__ import annotations

import json
from pathlib import Path

from semantic_diff_weaver.service import analyze
from tests.conftest import git

BASE = {
    "app.py": (
        "def edited(x):\n    return x < 5\n\n\n"
        "def kept():\n    return 'x'\n\n\n"
        "def reverted():\n    return 1\n"
    )
}
FIRST_PUSH = {
    "app.py": (
        "def edited(x):\n    return x <= 5\n\n\n"
        "def kept():\n    return 'y'\n\n\n"
        "def reverted():\n    return 2\n"
    )
}
SECOND_PUSH = (
    "def edited(x):\n    return x <= 6\n\n\n"
    "def kept():\n    return 'y'\n\n\n"
    "def reverted():\n    return 1\n\n\n"
    "def added(y):\n    return y + 1\n"
)


def test_a_second_push_is_read_against_the_first(repo_factory, tmp_path: Path) -> None:
    repo, base, first = repo_factory(BASE, FIRST_PUSH)
    earlier = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": first, "output_format": "both"}
    )
    baseline = tmp_path / "earlier.json"
    baseline.write_text(json.dumps(earlier), encoding="utf-8")
    (repo / "app.py").write_text(SECOND_PUSH, encoding="utf-8")
    git(repo, "commit", "-q", "-am", "second push")
    second = git(repo, "rev-parse", "HEAD")

    result = analyze(
        {
            "repo_path": str(repo),
            "base_ref": base,
            "head_ref": second,
            "output_format": "both",
            "baseline_report": str(baseline),
        }
    )

    analysis = result["analysis"]
    by_symbol = {item["evidence"][0]["symbol"]: item["id"] for item in analysis["behavior_changes"]}
    comparison = analysis["baseline"]
    assert comparison["analysis_id"] == earlier["analysis"]["analysis_id"]
    assert comparison["head_commit"] == first
    assert comparison["new"] == [by_symbol["added"]]
    assert comparison["updated"] == [by_symbol["edited"]]
    assert comparison["unchanged"] == [by_symbol["kept"]]
    assert [(item["symbol"], item["category"]) for item in comparison["resolved"]] == [
        ("reverted", "output_contract_change")
    ]
    markdown = result["markdown"]
    assert "**Since the baseline**" in markdown
    assert "1 new, 1 updated, 1 unchanged, 1 resolved." in markdown
    assert f"`{by_symbol['added']}` · new ·" in markdown
    assert f"`{by_symbol['edited']}` · updated ·" in markdown
    assert "output_contract_change in `app\\.py::reverted`" in markdown


def test_without_a_baseline_nothing_is_compared(repo_factory) -> None:
    repo, base, head = repo_factory(BASE, FIRST_PUSH)
    result = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "both"}
    )
    assert result["analysis"]["baseline"] is None
    assert "Since the baseline" not in result["markdown"]
