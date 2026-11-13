"""A limit is no retry, and a function changes as the functions nested in it do.

On the labeled benchmark's 150 development cases, apscheduler#1146's MongoDB cursors changed their
`.limit(n)` calls, labeled dependency interactions, and the word `limit` made each a retry change.
And a function whose body changed only through a nested function, such as black#5643's
`_handle_regular_fmt_block` and stamina#123's `retry`, was an unknown change. `docs/benchmark.md`
records the measurements. Such a function then took its categories' general obligations, which
named neither it nor the nested function; it now shares the nested function's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from semantic_diff_weaver.ast_diff import analyze_ast
from semantic_diff_weaver.config import WeaverConfig
from semantic_diff_weaver.evidence_scenarios import Context, changes_by_evidence, contexts_of
from semantic_diff_weaver.models import (
    BehaviorCategory,
    BehaviorChange,
    ObligationType,
    Origin,
    Presentation,
    RiskLabel,
    ScoreExplanation,
)
from semantic_diff_weaver.obligations import generate_obligations
from semantic_diff_weaver.semantic_candidates import build_candidates
from semantic_diff_weaver.source import SourceHunk, SourceRevisionPair
from semantic_diff_weaver.taxonomy import CATEGORY_PROFILES

if TYPE_CHECKING:
    # Imported for its annotation alone: pytest would collect a `Test` class in the module.
    from semantic_diff_weaver.models import TestObligation

WIDE_HUNK = SourceHunk(id="hunk-001", old_start=1, old_count=400, new_start=1, new_count=400)


def _findings(old: str, new: str) -> dict[str, list[tuple[BehaviorCategory, tuple[str, ...]]]]:
    deltas = analyze_ast(
        [
            SourceRevisionPair(
                path="m.py",
                old_path="m.py",
                new_path="m.py",
                hunks=(WIDE_HUNK,),
                old_text=old,
                new_text=new,
            )
        ]
    ).deltas
    found: dict[str, list[tuple[BehaviorCategory, tuple[str, ...]]]] = {}
    for candidate in build_candidates(deltas):
        found.setdefault(candidate.symbol, []).append((candidate.category, candidate.rule_ids))
    return found


@pytest.mark.parametrize(
    ("old", "new", "category"),
    [
        pytest.param(
            "def fetch(db):\n    return db.find().limit(10)\n",
            "def fetch(db):\n    return db.find().limit(20)\n",
            BehaviorCategory.DEPENDENCY_INTERACTION,
            id="a-query-limit",
        ),
        pytest.param(
            "def check(count, limit):\n    if count > limit:\n        raise ValueError\n",
            "def check(count, limit):\n    if count >= limit:\n        raise ValueError\n",
            BehaviorCategory.BOUNDARY,
            id="a-limit-compared",
        ),
    ],
)
def test_a_limit_is_no_retry(old: str, new: str, category: BehaviorCategory) -> None:
    """apscheduler#1146 changed the cursors' `.limit()` calls, and `limit` made them retries."""
    name = "fetch" if "fetch" in old else "check"
    categories = {found for found, _ in _findings(old, new)[name]}
    assert category in categories
    assert BehaviorCategory.RETRY_TIMEOUT not in categories


def test_the_words_of_a_retry_still_name_one() -> None:
    old = "def give_up(attempts, max_retries):\n    if attempts > max_retries:\n        raise E\n"
    new = "def give_up(attempts, max_retries):\n    if attempts >= max_retries:\n        raise E\n"
    assert [category for category, _ in _findings(old, new)["give_up"]] == [
        BehaviorCategory.RETRY_TIMEOUT
    ]


def test_a_function_changes_as_the_function_nested_in_it_does() -> None:
    """black#5643's `_handle_regular_fmt_block` changed only through its nested
    `stringify_standalone_comment`, an output contract change, and was an unknown change."""
    old = "def outer(x):\n    def inner(y):\n        return y + 1\n    return inner(x)\n"
    new = "def outer(x):\n    def inner(y):\n        return y + 2\n    return inner(x)\n"
    found = _findings(old, new)
    assert found["outer.inner"] == [(BehaviorCategory.OUTPUT_CONTRACT, ("SDW-RULE-RETURN",))]
    assert found["outer"] == [(BehaviorCategory.OUTPUT_CONTRACT, ("SDW-RULE-NESTED",))]


def test_a_function_takes_every_category_its_nested_functions_name() -> None:
    old = (
        "def outer(x):\n    def inner(y):\n        if y > 1:\n            return y\n"
        "        return 0\n    return inner(x)\n"
    )
    new = (
        "def outer(x):\n    def inner(y):\n        if y >= 1:\n            return y\n"
        "        raise ValueError(y)\n    return inner(x)\n"
    )
    found = _findings(old, new)
    nested = {category for category, _ in found["outer.inner"]}
    assert {category for category, _ in found["outer"]} == nested - {BehaviorCategory.UNKNOWN}
    assert all(rules == ("SDW-RULE-NESTED",) for _, rules in found["outer"])


@pytest.mark.parametrize(
    ("old", "new", "name"),
    [
        pytest.param(
            "class C:\n    def m(self):\n        return 1\n",
            "class C:\n    def m(self):\n        return 2\n",
            "C",
            id="a-class-is-no-function",
        ),
        pytest.param(
            "def outer(x):\n    def inner(y):\n        return y\n    return inner(x)\n",
            "def outer(x):\n    def inner(y):\n        return y  # same\n    return inner(x)\n",
            "outer",
            id="a-nested-function-that-reads-the-same",
        ),
    ],
)
def test_only_a_function_takes_what_its_nested_functions_name(
    old: str, new: str, name: str
) -> None:
    rules = {rule for _, rule_ids in _findings(old, new).get(name, []) for rule in rule_ids}
    assert "SDW-RULE-NESTED" not in rules


def test_a_function_whose_nested_change_is_unknown_stays_unknown() -> None:
    old = "def outer(x):\n    def inner(y):\n        y.a = 1\n    return inner\n"
    new = "def outer(x):\n    def inner(y):\n        y.a = 2\n    return inner\n"
    found = _findings(old, new)
    assert found["outer.inner"][0][0] is BehaviorCategory.UNKNOWN
    assert [category for category, _ in found["outer"]] == [BehaviorCategory.UNKNOWN]


def _analysis(
    old: str,
    new: str,
    changed: list[tuple[int, int]],
    *,
    coverage: dict[str, str] | None = None,
    review: tuple[str, ...] = (),
) -> tuple[list[BehaviorChange], dict[str, Context], list[TestObligation]]:
    """The findings, their contexts, and the obligations of a change to `changed`, each a first
    line and a count of lines that changed in place: a diff's hunks span those lines alone."""
    hunks = tuple(
        SourceHunk(
            id=f"hunk-{index:03d}",
            old_start=start,
            old_count=count,
            new_start=start,
            new_count=count,
        )
        for index, (start, count) in enumerate(changed, start=1)
    )
    deltas = analyze_ast(
        [
            SourceRevisionPair(
                path="m.py",
                old_path="m.py",
                new_path="m.py",
                hunks=hunks,
                old_text=old,
                new_text=new,
            )
        ]
    ).deltas
    behaviors = [
        BehaviorChange(
            id=f"bc-{index:03d}",
            category=candidate.category,
            summary=candidate.summary,
            observable_impact=candidate.observable_impact,
            risk=RiskLabel.MEDIUM,
            risk_score=50,
            confidence=0.8,
            evidence=list(candidate.evidence),
            assumptions=list(candidate.assumptions),
            presentation=Presentation.REVIEW_QUESTION
            if f"bc-{index:03d}" in review
            else Presentation.FINDING,
            origin=Origin.DETERMINISTIC,
            score_explanation=ScoreExplanation(
                behavioral_impact=50,
                critical_path_weight=0,
                test_gap_weight=90,
                change_surface_weight=10,
            ),
        )
        for index, candidate in enumerate(build_candidates(deltas), start=1)
    ]
    changes = changes_by_evidence(deltas)
    contexts = contexts_of(behaviors, changes, None)
    obligations, _ = generate_obligations(
        behaviors,
        {},
        False,
        WeaverConfig().rules,
        coverage_states=coverage,
        changes=changes,
        contexts=contexts,
    )
    return behaviors, contexts, obligations


def _on(behaviors: list[BehaviorChange], symbol: str) -> list[BehaviorChange]:
    return [item for item in behaviors if item.evidence[0].symbol == symbol]


def _general(behaviors: list[BehaviorChange]) -> set[str]:
    """The titles of the general scenarios of the findings' categories."""
    return {
        scenario.title
        for item in behaviors
        for scenario in CATEGORY_PROFILES[item.category].scenarios
    }


INNER_RETURNS_OLD = "def outer(x):\n    def inner(y):\n        return y + 1\n    return inner(x)\n"
INNER_RETURNS_NEW = "def outer(x):\n    def inner(y):\n        return y + 2\n    return inner(x)\n"


def test_a_function_that_changed_as_its_nested_one_did_shares_its_obligation() -> None:
    """tenacity#696's `retry` changed only through its nested `wrap`, and took the retry
    category's three general obligations, which named neither."""
    behaviors, _, obligations = _analysis(INNER_RETURNS_OLD, INNER_RETURNS_NEW, [(3, 1)])
    (outer,), (inner,) = _on(behaviors, "outer"), _on(behaviors, "outer.inner")
    assert [(item.title, item.behavior_change_ids) for item in obligations] == [
        (
            "Assert the consumer-visible contract of what `outer.inner` returns",
            [outer.id, inner.id],
        )
    ]


def test_each_category_of_the_function_shares_the_obligation_of_its_nested_finding() -> None:
    """werkzeug#3329's `run_wsgi` took its nested `execute`'s dependency and ordering changes."""
    old = (
        "def outer(x):\n    def inner(y):\n        if y > 1:\n            return y\n"
        "        return 0\n    return inner(x)\n"
    )
    new = (
        "def outer(x):\n    def inner(y):\n        if y >= 1:\n            return y\n"
        "        raise ValueError(y)\n    return inner(x)\n"
    )
    behaviors, _, obligations = _analysis(old, new, [(3, 1), (5, 1)])
    by_id = {item.id: item for item in behaviors}
    outer = _on(behaviors, "outer")
    assert len(outer) == 3
    for finding in outer:
        (linked,) = [item for item in obligations if finding.id in item.behavior_change_ids]
        assert [
            (by_id[other].evidence[0].symbol, by_id[other].category)
            for other in linked.behavior_change_ids
            if other != finding.id
        ] == [("outer.inner", finding.category)]
    assert not _general(behaviors) & {item.title for item in obligations}


def test_the_context_names_the_nested_functions_whose_findings_share_the_category() -> None:
    old = (
        "def outer(x):\n    def a(y):\n        return y + 1\n    def b(y):\n        return y\n"
        "    return a(b(x))\n"
    )
    new = (
        "def outer(x):\n    def a(y):\n        return y + 3\n    def b(y):\n"
        "        raise ValueError(y)\n    return a(b(x))\n"
    )
    behaviors, contexts, _ = _analysis(old, new, [(3, 1), (5, 1)])
    nested = {item.category: contexts[item.id].nested for item in _on(behaviors, "outer")}
    assert nested == {
        BehaviorCategory.ERROR_HANDLING: ("outer.b",),
        BehaviorCategory.OUTPUT_CONTRACT: ("outer.a", "outer.b"),
    }
    # A nested function's own findings quote their evidence and have no such context.
    assert not any(item.id in contexts for item in [*_on(behaviors, "outer.a")])


@pytest.mark.parametrize("keeps_its_own", ["its coverage differs", "it is a review question"])
def test_a_function_with_an_obligation_of_its_own_names_its_nested_function(
    keeps_its_own: str,
) -> None:
    """Where it cannot share the nested function's obligation, so that the obligation's coverage
    status stays true of each, or a review question keeps its own, its own names the nested one."""
    behaviors, _, obligations = _analysis(
        INNER_RETURNS_OLD,
        INNER_RETURNS_NEW,
        [(3, 1)],
        coverage={"bc-001": "covered"} if keeps_its_own == "its coverage differs" else None,
        review=("bc-001",) if keeps_its_own == "it is a review question" else (),
    )
    assert [item.id for item in _on(behaviors, "outer")] == ["bc-001"]
    own = [
        (item.title, item.given, item.when, item.then)
        for item in obligations
        if item.behavior_change_ids == ["bc-001"] and item.type is not ObligationType.REVIEW
    ]
    assert own == [
        (
            "Assert the outcome of `outer` that depends on its changed nested function",
            "`outer` changed only as its nested function `outer.inner` did",
            "`outer` runs with input that reaches `outer.inner`",
            "What changed in `outer.inner` is asserted in what `outer` returns, raises, or leaves",
        )
    ]
    assert not _general(behaviors) & {item.title for item in obligations}


def test_a_function_with_two_changed_nested_functions_names_both() -> None:
    old = (
        "def outer(x):\n    def a(y):\n        return y + 1\n    def b(y):\n        return y * 2\n"
        "    return a(b(x))\n"
    )
    new = (
        "def outer(x):\n    def a(y):\n        return y + 3\n    def b(y):\n        return y * 4\n"
        "    return a(b(x))\n"
    )
    _, _, obligations = _analysis(old, new, [(3, 1), (5, 1)], coverage={"bc-001": "covered"})
    (own,) = [item for item in obligations if item.behavior_change_ids == ["bc-001"]]
    assert own.given == "`outer` changed only as its nested functions `outer.a` and `outer.b` did"


def test_a_function_whose_own_code_also_changed_quotes_it() -> None:
    """stamina#123's `retry` also changed a line of its own, the `def` it put a comment on."""
    old = "def outer(x):\n    def inner(y):\n        return y + 1\n    del x[0]\n    return inner\n"
    new = "def outer(x):\n    def inner(y):\n        return y + 2\n    del x[1]\n    return inner\n"
    _, contexts, obligations = _analysis(old, new, [(3, 2)])
    assert "bc-001" not in contexts
    (own,) = [item for item in obligations if item.behavior_change_ids == ["bc-001"]]
    assert own.title == "Clarify and characterize what the changed lines of `outer` do"
    assert own.given.endswith("it now reads `del x[1]` where it read `del x[0]`")
