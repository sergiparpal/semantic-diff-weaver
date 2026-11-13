"""A limit is no retry, and a function changes as the functions nested in it do.

On the labeled benchmark's 150 development cases, apscheduler#1146's MongoDB cursors changed their
`.limit(n)` calls, labeled dependency interactions, and the word `limit` made each a retry change.
And a function whose body changed only through a nested function, such as black#5643's
`_handle_regular_fmt_block` and stamina#123's `retry`, was an unknown change. `docs/benchmark.md`
records the measurements.
"""

from __future__ import annotations

import pytest

from semantic_diff_weaver.ast_diff import analyze_ast
from semantic_diff_weaver.models import BehaviorCategory
from semantic_diff_weaver.semantic_candidates import build_candidates
from semantic_diff_weaver.source import SourceHunk, SourceRevisionPair

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
