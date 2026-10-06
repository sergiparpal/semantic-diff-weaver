"""The brief says why scope was left out, not only how much of it.

It used to report "2 omitted item(s), 25 excluded file(s)" and nothing more, so a reader — or a
model reading the brief — could not tell a policy choice from a budget from a path filter.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from semantic_diff_weaver import path_policy
from semantic_diff_weaver.renderer import EXCLUSION_PHRASES, OMISSION_PHRASES, _reason_items
from semantic_diff_weaver.service import analyze

PACKAGE = Path(__file__).resolve().parents[2] / "semantic_diff_weaver"


def _source(relative: str) -> str:
    return (PACKAGE / relative).read_text(encoding="utf-8")


def test_every_omission_reason_the_pipeline_emits_has_a_phrase() -> None:
    emitted = set(re.findall(r'record_omitted\(\s*"([a-z_]+)"', _source("service.py")))
    collect = _source("git_diff/collect.py")
    emitted |= set(re.findall(r'omitted_counts\["([a-z_]+)"\]', collect))
    emitted |= set(re.findall(r'omitted_counts = \{"([a-z_]+)"', collect))
    assert len(emitted) >= 10
    assert emitted <= OMISSION_PHRASES.keys()


def test_every_exclusion_reason_the_pipeline_emits_has_a_phrase() -> None:
    emitted = set(re.findall(r'excluded\["([a-z_]+)"\]', _source("git_diff/collect.py")))
    emitted |= set(
        re.findall(r'return "([a-z_]+)"', inspect.getsource(path_policy.exclusion_reason))
    )
    assert len(emitted) >= 8
    assert emitted <= EXCLUSION_PHRASES.keys()


def test_reason_items_read_count_first_and_skip_zero_counts() -> None:
    items = _reason_items(
        [("low_risk_refactor_policy", 2), ("changed_symbol_limit", 0), ("minimum_confidence", 1)],
        OMISSION_PHRASES,
    )
    assert items == (
        "2 low-risk refactor(s) hidden by `emit_low_risk_refactors: false` · "
        "1 finding(s) below `minimum_report_confidence`"
    )


def test_an_unknown_reason_is_shown_as_its_escaped_code() -> None:
    assert _reason_items([("new_reason_<x>", 3)], OMISSION_PHRASES) == r"3 new\_reason\_\<x\>"


def _brief(repo_factory, old: dict[str, str], new: dict[str, str]) -> list[str]:
    repo, base, head = repo_factory(old, new)
    envelope = analyze({"repo_path": str(repo), "base_ref": base, "head_ref": head})
    return envelope["markdown"].splitlines()


def test_the_brief_names_each_omission_and_exclusion(repo_factory) -> None:
    # A rename with an unchanged body is a low-risk refactor, which the default policy omits.
    # Vendored Python is outside the default paths; the notes are not Python at all, which is
    # their reason even though the default `**/*.py` include would reject them too.
    old = {"app.py": "def total(items):\n    return sum(items)\n"}
    new = {
        "app.py": "def compute_total(items):\n    return sum(items)\n",
        "NOTES.md": "Renamed total.\n",
        "vendor/client.py": "def call():\n    return 1\n",
    }
    lines = _brief(repo_factory, old, new)
    assert (
        "**Omitted:** 1 low-risk refactor(s) hidden by `emit_low_risk_refactors: false`." in lines
    )
    assert (
        "**Excluded files:** 1 outside the configured include and exclude paths · 1 not Python."
        in lines
    )


def test_a_brief_with_nothing_left_out_adds_no_reason_lines(repo_factory) -> None:
    lines = _brief(
        repo_factory,
        {"api.py": "def allowed(x):\n    return x < 5\n"},
        {"api.py": "def allowed(x):\n    return x <= 5\n"},
    )
    assert not any(line.startswith(("**Omitted:**", "**Excluded files:**")) for line in lines)
