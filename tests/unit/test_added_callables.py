"""An added callable's obligation lists what its own code returns and raises, and on which branch.

Its evidence held only its signature, so its obligation asked to call it "with a representative
input" and assert "its intended contract". Replaying the Action's field test on meal-manager, every
such obligation left the test to be invented: `last_change` returned `None` for each malformed
record and the record otherwise, `_is_iso_date` returned `False` when `date.fromisoformat` raised,
and `_blend` returned `round(1 - match_weight, 4)`, none of which the obligation named.
"""

from __future__ import annotations

import ast

import pytest

from semantic_diff_weaver.ast_diff import analyze_ast, limits
from semantic_diff_weaver.ast_diff.extract import extract_symbols, function_exits
from semantic_diff_weaver.config import WeaverConfig
from semantic_diff_weaver.evidence_scenarios import changes_by_evidence, contexts_of
from semantic_diff_weaver.models import (
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

WIDE_HUNK = SourceHunk(id="hunk-001", old_start=1, old_count=400, new_start=1, new_count=400)


def _exits(source: str) -> tuple[tuple[tuple[str, str, str, str], ...], int]:
    function = ast.parse(source).body[0]
    assert isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
    return function_exits(function)


def test_each_return_and_raise_is_recorded_with_the_branch_leading_there() -> None:
    source = (
        "def pick(x):\n"
        "    if x < 0:\n"
        "        raise ValueError(x)\n"
        "    elif x == 0:\n"
        "        return None\n"
        "    else:\n"
        "        log(x)\n"
        "    for item in range(x):\n"
        "        with lock:\n"
        "            if item > 3:\n"
        "                return item\n"
        "    return -1\n"
    )
    assert _exits(source) == (
        (
            ("raise", "ValueError(x)", "if", "x < 0"),
            ("return", "None", "if", "x == 0"),
            ("return", "item", "if", "item > 3"),
            ("return", "-1", "", ""),
        ),
        4,
    )


def test_a_handler_a_bare_raise_and_an_else_are_branches_of_their_own() -> None:
    source = (
        "def parse(value):\n"
        "    try:\n"
        "        return int(value)\n"
        "    except ValueError:\n"
        "        return None\n"
        "    except:\n"
        "        raise\n"
        "    finally:\n"
        "        close()\n"
    )
    assert _exits(source)[0] == (
        ("return", "int(value)", "", ""),
        ("return", "None", "except", "ValueError"),
        ("raise", "", "except", ""),
    )
    assert _exits("def f(x):\n    if x:\n        return 1\n    else:\n        return 2\n")[0] == (
        ("return", "1", "if", "x"),
        ("return", "2", "else", "x"),
    )


def test_a_match_case_is_a_branch_and_a_case_matching_anything_is_none() -> None:
    source = (
        "def name(x):\n"
        "    match x:\n"
        "        case 0:\n"
        "            return 'zero'\n"
        "        case [a, b] if a > b:\n"
        "            return 'pair'\n"
        "        case _:\n"
        "            return 'other'\n"
    )
    assert _exits(source) == (
        (
            ("return", "'zero'", "case", "0"),
            ("return", "'pair'", "case", "[a, b] if a > b"),
            ("return", "'other'", "", ""),
        ),
        3,
    )


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("    if x:\n        return 1\n    return 2\n", id="a-return"),
        pytest.param("    if x:\n        return 1\n    else:\n        raise E\n", id="both-blocks"),
        pytest.param("    with lock:\n        return 1\n", id="a-with-block"),
        pytest.param(
            "    try:\n        return 1\n    except E:\n        return 2\n", id="body-and-handlers"
        ),
        pytest.param("    try:\n        x()\n    finally:\n        return 2\n", id="a-finally"),
        pytest.param("    while True:\n        if x():\n            return 1\n", id="endless"),
        pytest.param(
            "    while True:\n        for item in x:\n            break\n        return 1\n",
            id="endless-with-an-inner-break",
        ),
        pytest.param(
            "    match x:\n        case 1:\n            return 1\n        case _:\n"
            "            return 2\n",
            id="a-match-with-a-catch-all",
        ),
    ],
)
def test_a_body_whose_every_path_exits_returns_no_none_of_its_own(body: str) -> None:
    exits, _ = _exits(f"def f(x):\n{body}")
    assert ("return", "None", "", "") not in exits


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("    if x:\n        return 1\n", id="an-if-alone"),
        pytest.param("    try:\n        return 1\n    except E:\n        log()\n", id="a-handler"),
        pytest.param(
            "    while True:\n        if x():\n            break\n        return 1\n",
            id="a-loop-that-breaks",
        ),
        pytest.param(
            "    match x:\n        case 1:\n            return 1\n", id="a-match-without-catch-all"
        ),
    ],
)
def test_a_body_that_can_run_past_its_end_returns_none_there(body: str) -> None:
    exits, _ = _exits(f"def f(x):\n{body}")
    assert exits[-1] == ("return", "None", "", "")


def test_a_generator_and_nested_code_give_no_exits_of_their_own() -> None:
    assert _exits("def f(x):\n    yield x\n    return 1\n") == ((), 0)
    nested = "def f(x):\n    def g():\n        return 2\n    return g\n"
    assert _exits(nested)[0] == (("return", "g", "", ""),)
    # A nested generator does not make the function one.
    assert _exits("def f(x):\n    def g():\n        yield 1\n    return g\n")[1] == 1


def test_at_most_a_bounded_number_of_exits_is_kept_and_all_are_counted() -> None:
    branches = "".join(f"    if x == {n}:\n        return {n}\n" for n in range(20))
    exits, total = _exits(f"def f(x):\n{branches}")
    assert len(exits) == limits.MAX_FUNCTION_EXITS
    assert total == 21


def test_a_function_s_snapshot_keeps_its_exits_and_a_class_has_none() -> None:
    symbols = {
        item.qualified_name: item
        for item in extract_symbols("class C:\n    def m(self):\n        return 1\n")
    }
    assert symbols["C.m"].exits == (("return", "1", "", ""),)
    assert symbols["C"].exits == ()


def test_a_snapshot_s_exits_reuse_the_texts_its_features_hold() -> None:
    source = "def load(path):\n    try:\n        return read(path)\n    except:\n        raise\n"
    function = {item.qualified_name: item for item in extract_symbols(source)}["load"]
    assert function.exits == (("return", "read(path)", "", ""), ("raise", "", "except", ""))
    assert function.features["returns"][0][0] == "read(path)"


def _obligations(old: str, new: str) -> list[tuple[ObligationType, str, str, str, str]]:
    """The obligations of a change to `m.py`, each as its type, title, given, when, and then."""
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
            presentation=Presentation.FINDING,
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
    obligations, _ = generate_obligations(
        behaviors,
        {},
        False,
        WeaverConfig().rules,
        changes=changes,
        contexts=contexts_of(behaviors, changes, None),
    )
    return [(item.type, item.title, item.given, item.when, item.then) for item in obligations]


# The module the function is added to: its own top-level code, a docstring, runs nothing.
BASE = '"""Tools."""\n'

LAST_CHANGE = (
    "def last_change(state) -> dict | None:\n"
    "    record = state.get('last_change')\n"
    "    if not isinstance(record, dict):\n"
    "        return None\n"
    "    if not isinstance(record.get('on'), str):\n"
    "        return None\n"
    "    if record.get('dish') is None:\n"
    "        return None\n"
    "    return {'on': record['on'], 'dish': record['dish']}\n"
)


def test_an_added_function_s_obligation_lists_each_outcome_on_its_branch() -> None:
    ((kind, title, given, when, then),) = _obligations(BASE, BASE + LAST_CHANGE)
    assert kind is ObligationType.POSITIVE
    assert title == "Assert each outcome of the new `last_change`"
    assert given == (
        "`last_change` is new: `(state) -> dict | None`; it returns `None` when "
        "`not isinstance(record, dict)`, when `not isinstance(record.get('on'), str)`, and in 1 "
        "more case, or returns `{'on': record['on'], 'dish': record['dish']}` otherwise"
    )
    assert when == "`last_change` is called once for each outcome"
    assert then == "Each outcome is asserted for the input leading to it"


@pytest.mark.parametrize(
    ("added", "title", "given", "when"),
    [
        pytest.param(
            "def blend(weight):\n    return {'recency': round(1 - weight, 4)}\n",
            "Assert what the new `blend` returns",
            "`blend` is new: `(weight)`; it returns `{'recency': round(1 - weight, 4)}`",
            "`blend` is called with a representative input",
            id="one-return",
        ),
        pytest.param(
            "def refuse(user):\n    raise PermissionError(user)\n",
            "Assert what the new `refuse` raises",
            "`refuse` is new: `(user)`; it raises `PermissionError(user)`",
            "`refuse` is called with a representative input",
            id="one-raise",
        ),
        pytest.param(
            "def check(x):\n    if x < 0:\n        raise ValueError(x)\n",
            "Assert each outcome of the new `check`",
            "`check` is new: `(x)`; it raises `ValueError(x)` when `x < 0`, or returns `None` "
            "otherwise",
            "`check` is called once for each outcome",
            id="a-raise-or-none",
        ),
        pytest.param(
            "def parse(value):\n    try:\n        return int(value)\n    except ValueError:\n"
            "        return 0\n",
            "Assert each outcome of the new `parse`",
            "`parse` is new: `(value)`; it returns `0` when `ValueError` is raised, or returns "
            "`int(value)` otherwise",
            "`parse` is called once for each outcome",
            id="a-handler",
        ),
    ],
)
def test_an_added_function_s_obligation_says_what_it_returns_or_raises(
    added: str, title: str, given: str, when: str
) -> None:
    ((_, found_title, found_given, found_when, _),) = _obligations(BASE, BASE + added)
    assert (found_title, found_given, found_when) == (title, given, when)


@pytest.mark.parametrize(
    "added",
    [
        pytest.param("def save(x):\n    store.write(x)\n", id="a-procedure"),
        pytest.param("def run(self):\n    raise NotImplementedError\n", id="a-stub"),
        pytest.param("def stop(x):\n    if x:\n        return\n    halt()\n", id="only-none"),
        pytest.param("def items(x):\n    yield x\n", id="a-generator"),
    ],
)
def test_an_added_function_with_nothing_to_assert_keeps_the_general_scenario(added: str) -> None:
    ((_, title, _, _, _),) = _obligations(BASE, BASE + added)
    assert title.startswith("Characterize the new ")


def test_an_added_function_with_more_outcomes_than_are_named_says_how_many_exits_it_has() -> None:
    branches = "".join(f"    if x == {n}:\n        return {n}\n" for n in range(4))
    ((_, _, given, _, _),) = _obligations(BASE, f"{BASE}def f2(x):\n{branches}    return -1\n")
    assert given == (
        "`f2` is new: `(x)`; among its 5 exits, it returns `0` when `x == 0`, returns `1` when "
        "`x == 1`, or returns `-1` otherwise"
    )


@pytest.mark.parametrize(
    ("added", "given", "when"),
    [
        pytest.param(
            "def sign(x):\n    if x:\n        return 1\n    else:\n        return -1\n",
            "`sign` is new: `(x)`; it returns `1` when `x`, or returns `-1` unless `x`",
            "`sign` is called once for each outcome",
            id="an-else",
        ),
        pytest.param(
            "def load(path):\n    try:\n        return read(path)\n    except:\n"
            "        return b''\n",
            "`load` is new: `(path)`; it returns `b''` when anything is raised, or returns "
            "`read(path)` otherwise",
            "`load` is called once for each outcome",
            id="a-bare-handler",
        ),
        pytest.param(
            "def wait(x):\n    while True:\n        if x():\n            return 1\n",
            "`wait` is new: `(x)`; it returns `1` when `x()`",
            "`wait` is called with input that reaches it",
            id="one-outcome-on-a-branch",
        ),
        pytest.param(
            "def name(x):\n    match x:\n        case 0:\n            return 'zero'\n"
            "        case _:\n            return 'other'\n",
            "`name` is new: `(x)`; it returns `'zero'` in the case `0`, or returns `'other'` "
            "otherwise",
            "`name` is called once for each outcome",
            id="a-match-case",
        ),
    ],
)
def test_each_branch_reads_as_what_leads_to_it(added: str, given: str, when: str) -> None:
    ((_, _, found_given, found_when, _),) = _obligations(BASE, BASE + added)
    assert (found_given, found_when) == (given, when)
