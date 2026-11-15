"""An added callable's obligation says what the local variables its outcomes read are assigned.

A blind review of the obligations that list what an added callable returns and raises judged 5 of
its 9 noise for one reason: the outcome was a local variable, or an expression of one, whose value
the code before the `return` decides. werkzeug#3332's `wrap_file` "returns `cls(file,
buffer_size)`", where what a test checks is the choice of `cls` between the server's file wrapper
and the library's own; django-axes#1414's `get_username_from_data` "returns `username`", which a
fallback may reassign.
"""

from __future__ import annotations

import ast

import pytest

from semantic_diff_weaver.ast_diff import limits
from semantic_diff_weaver.ast_diff.extract import callable_exits
from semantic_diff_weaver.ast_diff.types import LocalDefinition, StructuralDelta
from semantic_diff_weaver.evidence_scenarios import describe_change
from semantic_diff_weaver.models import MAX_OBLIGATION_TEXT_CHARS
from tests.unit.test_added_callables import BASE, _obligations


def _definitions(source: str) -> tuple[LocalDefinition, ...]:
    function = ast.parse(source).body[0]
    assert isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
    return callable_exits(function).definitions


def test_a_local_an_exit_returns_is_defined_by_its_assignments_and_their_paths() -> None:
    source = (
        "def get_username(data):\n"
        "    username = data.get('form', None)\n"
        "    if username is None:\n"
        "        if data:\n"
        "            username = data.get('user', None)\n"
        "    return username\n"
    )
    assert _definitions(source) == (
        (
            "username",
            (
                ("value", "data.get('form', None)", ()),
                ("value", "data.get('user', None)", (("if", "username is None"), ("if", "data"))),
            ),
            2,
            (),
            0,
        ),
    )


@pytest.mark.parametrize(
    ("body", "name", "assignments"),
    [
        pytest.param(
            "    cls: type = environ.get('wrapper', Wrapper)\n    return cls(file)\n",
            "cls",
            (("value", "environ.get('wrapper', Wrapper)", ()),),
            id="annotated",
        ),
        pytest.param(
            "    total = 0\n    for item in items:\n        total += item\n    return total\n",
            "total",
            (("value", "0", ()), ("value", "total + item", (("for", "items"),))),
            id="augmented-in-a-loop",
        ),
        pytest.param(
            "    for row in rows:\n        if row.ok:\n            return row\n",
            "row",
            (("item", "rows", ()),),
            id="a-loop-s-variable",
        ),
        pytest.param(
            "    low, high = bounds[0], bounds[1]\n    return high - low\n",
            "high",
            (("value", "bounds[1]", ()),),
            id="unpacked-item-by-item",
        ),
        pytest.param(
            "    low, high = bounds\n    return high - low\n",
            "high",
            (("item", "bounds", ()),),
            id="unpacked-from-one-value",
        ),
        pytest.param(
            "    first, *rest = items\n    return rest\n",
            "rest",
            (("item", "items", ()),),
            id="starred",
        ),
    ],
)
def test_each_kind_of_assignment_defines_a_local(
    body: str, name: str, assignments: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...]
) -> None:
    definitions = {
        item[0]: item
        for item in _definitions(f"def f(environ, file, items, rows, bounds):\n{body}")
    }
    assert definitions[name][1] == assignments


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("def f(x):\n    x = x + 1\n    return x\n", id="a-parameter-reassigned"),
        pytest.param(
            "def f():\n    global CACHE\n    CACHE = {}\n    return CACHE\n", id="a-global"
        ),
        pytest.param(
            "def f(xs):\n    x = 1\n    return [x for x in xs]\n", id="a-comprehension-s-own"
        ),
        pytest.param("def f():\n    y = 2\n    return lambda y: y\n", id="a-lambda-s-own"),
        pytest.param("def f(self):\n    self.x = 1\n    return self.x\n", id="an-attribute"),
        pytest.param("def f():\n    def g():\n        z = 1\n    return z\n", id="a-nested-scope"),
    ],
)
def test_a_name_a_test_passes_or_another_scope_binds_is_not_defined(source: str) -> None:
    assert _definitions(source) == ()


def test_locals_are_defined_in_the_order_the_exits_read_them_and_bounded() -> None:
    names = [f"v{index}" for index in range(limits.MAX_DEFINED_LOCALS + 2)]
    assigned = "".join(f"    {name} = {index}\n" for index, name in enumerate(names))
    reassigned = "".join(f"    v0 = v0 + {index}\n" for index in range(5))
    source = f"def f():\n{assigned}{reassigned}    return {' + '.join(reversed(names))}\n"
    definitions = _definitions(source)
    assert [item[0] for item in definitions] == list(reversed(names))[: limits.MAX_DEFINED_LOCALS]
    assert _definitions(f"def f():\n{assigned}{reassigned}    return v0\n") == (
        (
            "v0",
            (("value", "0", ()), ("value", "v0 + 0", ()), ("value", "v0 + 1", ())),
            6,
            (),
            0,
        ),
    )


def test_what_changes_a_local_in_place_after_its_assignment_is_recorded() -> None:
    source = (
        "def note(state, on):\n"
        "    new_state = dict(state)\n"
        "    new_state['last_change'] = on\n"
        "    if on:\n"
        "        del new_state['stale']\n"
        "    new_state.update(seen=True)\n"
        "    new_state.count += 1\n"
        "    return new_state\n"
    )
    ((name, _, _, changes, total),) = _definitions(source)
    assert name == "new_state"
    assert changes == (
        ("set", "new_state['last_change']", ()),
        ("delete", "new_state['stale']", (("if", "on"),)),
    )
    assert total == limits.MAX_LOCAL_CHANGES + 2


@pytest.mark.parametrize(
    ("added", "given"),
    [
        pytest.param(
            "def wrap_file(environ, file, size=128):\n"
            "    cls = environ.get('wsgi.file_wrapper', FileWrapper)\n"
            "    return cls(file, size)\n",
            "`wrap_file` is new: `(environ, file, size=128)`; it returns `cls(file, size)`; `cls` is "
            "`environ.get('wsgi.file_wrapper', FileWrapper)`",
            id="werkzeug-wrap-file",
        ),
        pytest.param(
            "def get_username(data):\n"
            "    username = data.get(FORM_FIELD, None)\n"
            "    if username is None:\n"
            "        username = data.get(USERNAME_FIELD, None)\n"
            "    return username\n",
            "`get_username` is new: `(data)`; it returns `username`; `username` is "
            "`data.get(FORM_FIELD, None)`, or `data.get(USERNAME_FIELD, None)` when "
            "`username is None`",
            id="axes-username-and-its-fallback",
        ),
        pytest.param(
            # A local assigned once is what that assignment gives wherever an exit reads it.
            "def utc_time(obj, name):\n"
            "    try:\n"
            "        return getattr(obj, f'{name}_utc')\n"
            "    except AttributeError:\n"
            "        value = getattr(obj, name)\n"
            "        return value and value.replace(tzinfo=UTC)\n",
            "`utc_time` is new: `(obj, name)`; it returns `value and value.replace(tzinfo=UTC)` "
            "when `AttributeError` is raised, or returns `getattr(obj, f'{name}_utc')` otherwise; "
            "`value` is `getattr(obj, name)`",
            id="assigned-once-under-a-condition",
        ),
        pytest.param(
            "def level(x):\n"
            "    code = 0\n"
            "    if x > 1:\n"
            "        code = 1\n"
            "    if x > 2:\n"
            "        code = 2\n"
            "    return code\n",
            "`level` is new: `(x)`; it returns `code`; `code` is `0`, or `1` when `x > 1`, or 1 "
            "more",
            id="more-assignments-than-are-named",
        ),
        pytest.param(
            # meal-manager#43's `note_deploy_change`: `dict(state)` is not what it returns.
            "def note(state, on):\n"
            "    new_state = dict(state)\n"
            "    new_state['last_change'] = on\n"
            "    return new_state\n",
            "`note` is new: `(state, on)`; it returns `new_state`; `new_state` is `dict(state)`, "
            "then `new_state['last_change']` is set",
            id="changed-in-place-after-its-assignment",
        ),
        pytest.param(
            "def strip(state, cache):\n"
            "    copy = dict(state)\n"
            "    if cache:\n"
            "        copy.pop('lock', None)\n"
            "        copy.pop('gen', None)\n"
            "        copy['cache'] = []\n"
            "    return copy\n",
            "`strip` is new: `(state, cache)`; it returns `copy`; `copy` is `dict(state)`, then "
            "`copy.pop('lock', None)` runs when `cache`, and 2 more changes",
            id="several-changes",
        ),
        pytest.param(
            "def first(rows):\n    for row in rows:\n        return row\n",
            "`first` is new: `(rows)`; it returns `row` for an item of `rows`, or returns `None` "
            "otherwise",
            id="a-loop-s-variable-the-outcome-names-already",
        ),
    ],
)
def test_an_outcome_says_what_the_locals_it_reads_are_assigned(added: str, given: str) -> None:
    ((_, _, found, _, _),) = _obligations(BASE, BASE + added)
    assert found == given


def _long(name: str) -> str:
    return " + ".join(f"{name}{index}" for index in range(40))


def test_definitions_give_way_before_outcomes_where_both_do_not_fit() -> None:
    parameters = ", ".join(f"parameter_number_{index}: int = {index}" for index in range(30))
    lines = [f"def wide({parameters}):"]
    for name in ("alpha", "beta"):
        lines.append(f"    {name} = {_long(name[0])}")
        for test in ("one", "two"):
            lines += [f"    if ({_long(name[0] + test)}) > 0:", f"        return {name}"]
    added = "\n".join([*lines, f"    return {_long('z')}"]) + "\n"
    ((_, _, given, _, _),) = _obligations(BASE, BASE + added)
    assert len(given) <= MAX_OBLIGATION_TEXT_CHARS
    # Both paths of both outcomes are still named, and what is left of the room defines one.
    assert given.count(" when `") == 4
    assert "`alpha` is `a0 + a1" in given
    assert "`beta` is" not in given


def test_a_definition_out_of_shape_is_dropped_whole() -> None:
    def added(definitions: object) -> StructuralDelta:
        return StructuralDelta(
            path="m.py",
            symbol="f",
            kind="symbol_added",
            old=None,
            new="(x)",
            old_lines=None,
            new_lines=None,
            hunk_id=None,
            metadata={
                "exits": [["return", "y", []]],
                "exits_total": 1,
                "definitions": definitions,
            },
        )

    change = describe_change(
        added(
            [
                ["y", [["value", "x + 1", [["if", "x"]]]], 1, [["set", "y.a", []]], 1],
                # An assignment or a change of another kind, or one whose conditions are not
                # pairs, would leave the variable said to hold less than it may.
                ["z", [["value", "1", []], ["copied", "w", []]], 2, [], 0],
                ["w", [["value", "2", [["if"]]]], 1, [], 0],
                ["t", [["value", "{}", []]], 1, [["mutated", "t['k']", []]], 1],
                ["s", [["value", "{}", []]], 1, "junk", 0],
                ["v", [], 0, [], 0],
                ["u", [["value", "3", []]], True, [], 0],
                ["r", [["value", "3", []]], 1],
                "junk",
            ]
        )
    )
    assert change is not None
    assert change.definitions == (
        ("y", (("value", "x + 1", (("if", "x"),)),), 1, (("set", "y.a", ()),), 1),
    )
    unread = describe_change(added("junk"))
    assert unread is not None
    assert unread.definitions == ()
