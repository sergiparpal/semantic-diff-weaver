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
from semantic_diff_weaver.ast_diff.types import FunctionExit, StructuralDelta
from semantic_diff_weaver.config import WeaverConfig
from semantic_diff_weaver.evidence_scenarios import (
    changes_by_evidence,
    contexts_of,
    describe_change,
)
from semantic_diff_weaver.models import (
    MAX_OBLIGATION_TEXT_CHARS,
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


def _exits(source: str) -> tuple[tuple[FunctionExit, ...], int]:
    function = ast.parse(source).body[0]
    assert isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
    return function_exits(function)


def test_each_return_and_raise_is_recorded_with_every_branch_on_its_path() -> None:
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
            ("raise", "ValueError(x)", (("if", "x < 0"),)),
            # An `elif` is reached only where the `if` before it failed.
            ("return", "None", (("else", "x < 0"), ("if", "x == 0"))),
            ("return", "item", (("if", "item > 3"),)),
            ("return", "-1", ()),
        ),
        4,
    )


def test_an_exit_inside_two_branches_records_both() -> None:
    source = (
        "def disjoint(left, right):\n"
        "    while left and right:\n"
        "        if empty(left, right):\n"
        "            right.pop()\n"
        "        elif empty(right, left):\n"
        "            left.pop()\n"
        "        else:\n"
        "            return False\n"
        "    if left:\n"
        "        if right:\n"
        "            return None\n"
        "    return True\n"
    )
    assert _exits(source)[0] == (
        # The `else` of an `elif`: both tests failed. A branch makes the loop's test unneeded.
        (
            "return",
            "False",
            (("else", "empty(left, right)"), ("else", "empty(right, left)")),
        ),
        ("return", "None", (("if", "left"), ("if", "right"))),
        ("return", "True", ()),
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
        # The body returns where no handler's exception is raised before it.
        ("return", "int(value)", (("try", "ValueError"), ("try", ""))),
        ("return", "None", (("except", "ValueError"),)),
        ("raise", "", (("except", ""),)),
    )
    assert _exits("def f(x):\n    if x:\n        return 1\n    else:\n        return 2\n")[0] == (
        ("return", "1", (("if", "x"),)),
        ("return", "2", (("else", "x"),)),
    )


def test_a_match_case_is_a_branch_and_a_case_matching_anything_follows_the_others() -> None:
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
            ("return", "'zero'", (("case", "0"),)),
            ("return", "'pair'", (("case", "[a, b] if a > b"),)),
            ("return", "'other'", (("other", "0"), ("other", "[a, b] if a > b"))),
        ),
        3,
    )


@pytest.mark.parametrize(
    ("body", "conditions"),
    [
        pytest.param("    for x in xs:\n        return x\n", (("for", "xs"),), id="a-for"),
        pytest.param(
            "    while queue:\n        return queue.pop()\n", (("while", "queue"),), id="a-while"
        ),
        pytest.param("    while True:\n        return 1\n", (), id="an-endless-while"),
        pytest.param(
            "    for x in xs:\n        try:\n            return f(x)\n        except E:\n"
            "            pass\n",
            (("for", "xs"), ("try", "E")),
            id="a-try-in-a-loop",
        ),
        pytest.param(
            "    try:\n        x()\n    except E:\n        pass\n    else:\n        return 1\n",
            (("try", "E"),),
            id="a-try-s-else",
        ),
        pytest.param("    try:\n        x()\n    finally:\n        return 1\n", (), id="a-finally"),
        pytest.param("    with lock:\n        return 1\n", (), id="a-with-block"),
    ],
)
def test_an_exit_on_no_branch_records_what_the_loops_and_try_statements_around_it_need(
    body: str, conditions: tuple[tuple[str, str], ...]
) -> None:
    exits, _ = _exits(f"def f(xs):\n{body}")
    assert exits[0][2] == conditions


def test_a_path_through_more_branches_than_are_recorded_says_how_many() -> None:
    depth = limits.MAX_EXIT_CONDITIONS + 1
    body = "".join(f"{'    ' * (level + 1)}if x > {level}:\n" for level in range(depth))
    exits, _ = _exits(f"def f(x):\n{body}{'    ' * (depth + 1)}return x\n")
    assert exits[0] == ("return", "x", (("deep", str(depth)),))
    loops = "".join(f"{'    ' * (level + 1)}for x{level} in x:\n" for level in range(depth))
    exits, _ = _exits(f"def f(x):\n{loops}{'    ' * (depth + 1)}return x\n")
    # Too many loops to name, and none of them a branch: no condition.
    assert exits[0] == ("return", "x", ())


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
    assert ("return", "None", ()) not in exits


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
    assert exits[-1] == ("return", "None", ())


def test_a_generator_and_nested_code_give_no_exits_of_their_own() -> None:
    assert _exits("def f(x):\n    yield x\n    return 1\n") == ((), 0)
    nested = "def f(x):\n    def g():\n        return 2\n    return g\n"
    assert _exits(nested)[0] == (("return", "g", ()),)
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
    assert symbols["C.m"].exits == (("return", "1", ()),)
    assert symbols["C"].exits == ()


def test_a_snapshot_s_exits_reuse_the_texts_its_features_hold() -> None:
    source = "def load(path):\n    try:\n        return read(path)\n    except:\n        raise\n"
    function = {item.qualified_name: item for item in extract_symbols(source)}["load"]
    assert function.exits == (
        ("return", "read(path)", (("try", ""),)),
        ("raise", "", (("except", ""),)),
    )
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
        "more case, or returns `{'on': record['on'], 'dish': record['dish']}` otherwise; "
        "`record` is `state.get('last_change')`"
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


@pytest.mark.parametrize(
    ("added", "given"),
    [
        pytest.param(
            # packaging#1471's `ranges_are_disjoint`: the first branch returns nothing, so its
            # test was missing from where the second one's `else` returns.
            "def disjoint(left, right):\n    while left and right:\n"
            "        if empty(left, right):\n            right.pop()\n"
            "        elif empty(right, left):\n            left.pop()\n"
            "        else:\n            return False\n    return True\n",
            "`disjoint` is new: `(left, right)`; it returns `False` unless `empty(left, right)` "
            "or `empty(right, left)`, or returns `True` otherwise",
            id="an-elif-s-else",
        ),
        pytest.param(
            "def pick(a, b):\n    if a:\n        if b:\n            return 1\n    return 2\n",
            "`pick` is new: `(a, b)`; it returns `1` when `a` and `b`, or returns `2` otherwise",
            id="an-if-inside-an-if",
        ),
        pytest.param(
            "def pick(a, b):\n    if a:\n        log()\n    elif b:\n        return 1\n"
            "    return 2\n",
            "`pick` is new: `(a, b)`; it returns `1` when `b` unless `a`, or returns `2` otherwise",
            id="an-elif",
        ),
        pytest.param(
            "def first(xs):\n    for x in xs:\n        return x\n",
            "`first` is new: `(xs)`; it returns `x` for an item of `xs`, or returns `None` "
            "otherwise",
            id="the-first-item-of-a-loop",
        ),
        pytest.param(
            "def fetch(f):\n    try:\n        return f()\n    except KeyError:\n        log()\n"
            "    except ValueError:\n        log()\n",
            "`fetch` is new: `(f)`; it returns `f()` unless `KeyError` or `ValueError` is raised, "
            "or returns `None` otherwise",
            id="a-try-whose-handlers-return-nothing",
        ),
        pytest.param(
            "def kind(x):\n    match x:\n        case 0:\n            log()\n        case _:\n"
            "            return 1\n",
            "`kind` is new: `(x)`; it returns `1` in a case other than `0`, or returns `None` "
            "otherwise",
            id="a-case-after-one-that-returns-nothing",
        ),
        pytest.param(
            "def first(xs):\n    for x in xs:\n        if x:\n            return x\n    return 0\n",
            # What the loop's variable is the outcome alone does not say.
            "`first` is new: `(xs)`; it returns `x` when `x`, or returns `0` otherwise; `x` is an "
            "item of `xs`",
            id="a-branch-needs-no-loop",
        ),
        pytest.param(
            "def last(x):\n    try:\n        return 1\n    finally:\n        return 2\n",
            "`last` is new: `(x)`; it returns `1` on one of its paths, or returns `2` otherwise",
            id="two-outcomes-with-nothing-to-tell-them-apart",
        ),
        pytest.param(
            "def raw(flag):\n    if flag.startswith('--'):\n        if flag == '--all':\n"
            "            return True\n    elif flag.startswith('-'):\n        if 'a' in flag:\n"
            "            return True\n    return False\n",
            # Alternatives whose conditions are joined with `and` are told apart with `or`.
            "`raw` is new: `(flag)`; it returns `True` when `flag.startswith('--')` and "
            "`flag == '--all'`, or when `flag.startswith('-')` and `'a' in flag` unless "
            "`flag.startswith('--')`, or returns `False` otherwise",
            id="two-paths-of-several-conditions",
        ),
    ],
)
def test_an_outcome_names_every_condition_on_the_path_to_it(added: str, given: str) -> None:
    ((_, _, found, _, _),) = _obligations(BASE, BASE + added)
    assert found == given
    # Never "otherwise" twice, which would say that two outcomes both happen where nothing else does.
    assert found.count("otherwise") <= 1


def test_a_path_through_more_branches_than_are_named_says_how_many() -> None:
    depth = limits.MAX_EXIT_CONDITIONS + 1
    body = "".join(f"{'    ' * (level + 1)}if x > {level}:\n" for level in range(depth))
    added = f"def deep(x):\n{body}{'    ' * (depth + 1)}return x\n    return 0\n"
    ((_, _, given, _, _),) = _obligations(BASE, BASE + added)
    assert given == (
        f"`deep` is new: `(x)`; it returns `x` on a path through {depth} conditions, or returns "
        "`0` otherwise"
    )


def _long(name: str) -> str:
    return " + ".join(f"{name}{index}" for index in range(40))


def test_an_outcome_list_too_long_for_its_obligation_names_less_of_it() -> None:
    parameters = ", ".join(f"parameter_number_{index}: int = {index}" for index in range(30))
    lines = [f"def wide({parameters}):"]
    for value in "VWZ":
        for test in (f"{value.lower()}a", f"{value.lower()}b"):
            lines += [f"    if ({_long(test)}) > 0:", f"        return {_long(value)}"]
    added = "\n".join([*lines, f"    return {_long('U')}"]) + "\n"
    ((_, _, given, _, _),) = _obligations(BASE, BASE + added)
    # Its whole list would take more than the 1000 characters an obligation's `given` holds,
    # which failed the whole analysis; it names one path of each outcome instead.
    assert len(given) <= MAX_OBLIGATION_TEXT_CHARS
    assert "among its 7 exits, it returns `V0 + V1" in given
    assert "and in 1 more case, returns `W0 + W1" in given
    assert given.endswith("otherwise")


def test_an_exit_with_a_part_out_of_shape_is_dropped_rather_than_read_without_it() -> None:
    def added(exits: object) -> StructuralDelta:
        return StructuralDelta(
            path="m.py",
            symbol="f",
            kind="symbol_added",
            old=None,
            new="(x)",
            old_lines=None,
            new_lines=None,
            hunk_id=None,
            metadata={"exits": exits, "exits_total": 6},
        )

    change = describe_change(
        added(
            [
                ["return", "1", [["if", "x"]]],
                ["return", "2"],
                ["return", 3, []],
                # A condition missing its test, or one that is no pair, would leave the exit
                # said to be reached on a path without it.
                ["return", "4", [["if"]]],
                ["return", "5", [["else", "x"], "y"]],
                "junk",
            ]
        )
    )
    assert change is not None
    assert change.exits == (("return", "1", (("if", "x"),)),)
    assert change.exits_total == 6
    unread = describe_change(added("junk"))
    assert unread is not None
    assert unread.exits == ()
