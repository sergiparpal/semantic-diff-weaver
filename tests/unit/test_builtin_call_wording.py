"""A changed call to a builtin of Python's is not the project's own code.

A call change that stays in the process, to the module's own code, its package's, or a pure
builtin, asks what the symbol makes of the call, and its obligation said each such call was "its
own project's code": `naturaldelta now also calls round(delta.days / 365), its own project's
code`, and `float('inf')` the same, which the seventh blind review of obligations read as a
misattribution. The finding's summary said the same of the call.
"""

from __future__ import annotations

import pytest

from semantic_diff_weaver.ast_diff.analyze import analyze_ast
from semantic_diff_weaver.ast_diff.compare import is_builtin_call
from semantic_diff_weaver.ast_diff.types import StructuralDelta
from semantic_diff_weaver.evidence_scenarios import (
    Change,
    changes_by_evidence,
    describe_change,
    evidence_scenarios,
)
from semantic_diff_weaver.models import (
    BehaviorCategory,
    BehaviorChange,
    Evidence,
    Origin,
    RiskLabel,
    RulesConfig,
    ScoreExplanation,
)
from semantic_diff_weaver.semantic_candidates import build_candidates, classify_delta
from semantic_diff_weaver.source import SourceHunk, SourceRevisionPair

WIDE_HUNK = SourceHunk(id="hunk-001", old_start=1, old_count=400, new_start=1, new_count=400)
OUTCOME = "Assert the outcome of `f` that depends on its changed call"


def _deltas(old: str, new: str) -> list[StructuralDelta]:
    pair = SourceRevisionPair(
        path="m.py",
        old_path="m.py",
        new_path="m.py",
        hunks=(WIDE_HUNK,),
        old_text=old,
        new_text=new,
    )
    return list(analyze_ast([pair]).deltas)


def _call_delta(old: str, new: str) -> StructuralDelta:
    (delta,) = [item for item in _deltas(old, new) if item.kind == "call_change"]
    return delta


def _behavior(category: BehaviorCategory, evidence: tuple[Evidence, ...]) -> BehaviorChange:
    return BehaviorChange(
        id="bc-001",
        category=category,
        summary="Changed.",
        observable_impact="Something differs.",
        risk=RiskLabel.MEDIUM,
        risk_score=50,
        confidence=0.8,
        evidence=list(evidence),
        assumptions=[],
        origin=Origin.DETERMINISTIC,
        score_explanation=ScoreExplanation(
            behavioral_impact=50,
            critical_path_weight=0,
            test_gap_weight=90,
            change_surface_weight=10,
        ),
    )


@pytest.mark.parametrize(
    ("name", "own", "first_party", "builtin"),
    [
        pytest.param("round", frozenset(), frozenset(), True, id="pure-builtin"),
        pytest.param("float", frozenset(), frozenset(), True, id="builtin-type"),
        pytest.param("round", frozenset({"round"}), frozenset(), False, id="defined-by-the-module"),
        pytest.param(
            "round", frozenset(), frozenset({"round"}), False, id="imported-from-its-package"
        ),
        pytest.param("open", frozenset(), frozenset(), False, id="builtin-that-reaches-out"),
        pytest.param("helper", frozenset({"helper"}), frozenset(), False, id="module-function"),
    ],
)
def test_a_builtin_call_is_one_the_module_does_not_define_or_import(
    name: str, own: frozenset[str], first_party: frozenset[str], builtin: bool
) -> None:
    assert is_builtin_call(name, own, first_party) is builtin


@pytest.mark.parametrize(
    ("old", "new", "builtins"),
    [
        pytest.param(
            "def f(days):\n    years = int(days / 365)\n    return years\n",
            "def f(days):\n    years = round(days / 365)\n    return years\n",
            "all",
            id="builtins-only",
        ),
        pytest.param(
            "def helper(x):\n    return x\n\n\ndef f(x):\n    y = helper(x)\n    return y\n",
            "def helper(x):\n    return x\n\n\ndef f(x):\n    y = len(x)\n    return y\n",
            "some",
            id="own-and-builtin",
        ),
        pytest.param(
            "def helper(x):\n    return x\n\n\ndef other(x):\n    return x\n\n\n"
            "def f(x):\n    y = helper(x)\n    return y\n",
            "def helper(x):\n    return x\n\n\ndef other(x):\n    return x\n\n\n"
            "def f(x):\n    y = other(x)\n    return y\n",
            None,
            id="own-code-only",
        ),
        pytest.param(
            "def round(x):\n    return x\n\n\ndef f(x):\n    y = int(x)\n    return y\n",
            "def round(x):\n    return x\n\n\ndef f(x):\n    y = round(x)\n    return y\n",
            "some",
            id="a-module-s-own-round",
        ),
    ],
)
def test_a_call_change_records_whether_its_internal_calls_are_builtins(
    old: str, new: str, builtins: str | None
) -> None:
    delta = _call_delta(old, new)
    assert delta.metadata["internal_calls"] is True
    assert delta.metadata.get("internal_builtins") == builtins


def test_a_dependency_call_records_no_builtins() -> None:
    """`internal_builtins` qualifies an internal call change, and a dependency's is none."""
    delta = _call_delta(
        "import requests\n\n\ndef f(url):\n    return len(requests.get(url).text)\n",
        "import requests\n\n\ndef f(url):\n    return len(requests.post(url).text)\n",
    )
    assert delta.metadata["internal_calls"] is False
    assert "internal_builtins" not in delta.metadata


@pytest.mark.parametrize(
    ("builtins", "suffix"),
    [
        pytest.param(None, ", its own project's code", id="own-code"),
        pytest.param("some", ", its own project's code or built into Python", id="both"),
        pytest.param("all", ", built into Python", id="builtins"),
    ],
)
def test_the_obligation_says_whose_code_the_changed_call_is(
    builtins: str | None, suffix: str
) -> None:
    change = Change(
        kind="call_change",
        removed=("int(x)",),
        added=("round(x)",),
        removed_total=1,
        added_total=1,
        internal=True,
        builtins=builtins,
    )
    evidence = Evidence(id="ev-001", path="m.py", symbol="f", kind="call_change", old="a", new="b")
    (scenario,) = evidence_scenarios(
        _behavior(BehaviorCategory.UNKNOWN, (evidence,)), {"ev-001": change}
    )
    assert scenario.title == OUTCOME
    assert scenario.given == f"`f` now calls `round(x)` where it called `int(x)`{suffix}"


def test_a_changed_builtin_call_feeding_the_output_is_built_into_python() -> None:
    """The shape of humanize's `naturaldelta`, which rounds the years it used to truncate."""
    old = "def f(days):\n    years = int(days / 365)\n    return years\n"
    new = "def f(days):\n    years = round(days / 365)\n    return years\n"
    deltas = _deltas(old, new)
    changes = changes_by_evidence(deltas)
    givens = [
        scenario.given
        for candidate in build_candidates(deltas)
        for scenario in evidence_scenarios(
            _behavior(candidate.category, candidate.evidence), changes
        )
        if scenario.title == OUTCOME
    ]
    assert givens == [
        "`f` now calls `round(days / 365)` where it called `int(days / 365)`, built into Python"
    ]


def test_metadata_other_than_all_or_some_names_no_builtins() -> None:
    delta = _call_delta(
        "def f(days):\n    years = int(days / 365)\n    return years\n",
        "def f(days):\n    years = round(days / 365)\n    return years\n",
    )
    delta.metadata["internal_builtins"] = "every"
    change = describe_change(delta)
    assert change is not None and change.internal and change.builtins is None


@pytest.mark.parametrize(
    ("old_call", "new_call", "summary"),
    [
        pytest.param(
            "sorted(x)",
            "reversed(x)",
            "A call to a Python builtin changed without enough local contract context.",
            id="builtins",
        ),
        pytest.param(
            "helper(x)",
            "reversed(x)",
            "A call to the project's own code changed without enough local contract context.",
            id="own-and-builtin",
        ),
    ],
)
def test_the_summary_of_an_internal_call_names_whose_code_it_is(
    old_call: str, new_call: str, summary: str
) -> None:
    helper = "def helper(x):\n    return x\n\n\n"
    delta = _call_delta(
        f"{helper}def f(x):\n    {old_call}\n    return x\n",
        f"{helper}def f(x):\n    {new_call}\n    return x\n",
    )
    category, text, _, _, _, rule = classify_delta(delta, RulesConfig())
    assert category is BehaviorCategory.UNKNOWN
    assert rule == "SDW-RULE-UNKNOWN-INTERNAL-CALL"
    assert text == summary
