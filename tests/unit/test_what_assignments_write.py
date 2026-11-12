"""What a changed assignment writes, and what a removed symbol took away.

On the labeled benchmark's 120 development cases, 55 of the 157 behavior changes got no finding in
an expected category. Five removed symbols labeled output contract changes were unknown ones. Six
tenacity changes to how long a retry sleeps read as state transitions, since the target
`retry_state.upcoming_sleep` named a state by the variable it writes into. The attributes a class
body sets, and those a method keeps on its instance, were unknown changes. And httpx's `__all__`
gained a name past where its value was cut, which no assignment showed. `docs/benchmark.md` records
the measurements.
"""

from __future__ import annotations

import ast
from dataclasses import replace

import pytest

from semantic_diff_weaver.ast_diff.compare import compare_symbol, own_call_names
from semantic_diff_weaver.ast_diff.extract import VALUE_DIGEST_CHARS, extract_symbols, unparse_whole
from semantic_diff_weaver.ast_diff.limits import MAX_ASSIGNED_VALUE_CHARS
from semantic_diff_weaver.ast_diff.types import StructuralDelta
from semantic_diff_weaver.models import BehaviorCategory, RulesConfig
from semantic_diff_weaver.semantic_candidates import classify_delta
from semantic_diff_weaver.source import SourceHunk

WIDE_HUNK = SourceHunk(id="hunk-001", old_start=1, old_count=400, new_start=1, new_count=400)

REMOVED = (BehaviorCategory.OUTPUT_CONTRACT, "SDW-RULE-SYMBOL-REMOVED")
RETRY = (BehaviorCategory.RETRY_TIMEOUT, "SDW-RULE-RETRY-ASSIGNMENT")
STATE = (BehaviorCategory.STATE_TRANSITION, "SDW-RULE-STATE")
CLASS_ATTRIBUTE = (BehaviorCategory.OUTPUT_CONTRACT, "SDW-RULE-CLASS-ATTRIBUTE")
INSTANCE = (BehaviorCategory.STATE_TRANSITION, "SDW-RULE-INSTANCE-STATE")
UNKNOWN = (BehaviorCategory.UNKNOWN, "SDW-RULE-UNKNOWN-ASSIGNMENT")
FLOW = (BehaviorCategory.OUTPUT_CONTRACT, "SDW-RULE-OUTPUT-FLOW")
EXPORTS = (BehaviorCategory.OUTPUT_CONTRACT, "SDW-RULE-EXPORTS")


def _compare(old: str, new: str, name: str) -> list[StructuralDelta]:
    before, after = extract_symbols(old), extract_symbols(new)
    return compare_symbol(
        "pkg/mod.py",
        {item.qualified_name: item for item in before}.get(name),
        {item.qualified_name: item for item in after}.get(name),
        (WIDE_HUNK,),
        own_calls=own_call_names(before, after),
    )


def _rules(old: str, new: str, name: str) -> dict[str, tuple[BehaviorCategory, str]]:
    result = {}
    for delta in _compare(old, new, name):
        category, _, _, _, _, rule = classify_delta(delta, RulesConfig())
        result[delta.kind] = (category, rule)
    return result


def test_a_removed_method_is_an_output_contract_change() -> None:
    """werkzeug#3322 removed `_FileWrapper.seek`, which callers of the wrapper it returns used."""
    old = "class Wrapper:\n    def seek(self, offset):\n        return self.file.seek(offset)\n"
    new = "class Wrapper:\n    pass\n"
    assert _rules(old, new, "Wrapper.seek") == {"symbol_removed": REMOVED}


@pytest.mark.parametrize(
    ("old", "new", "name"),
    [
        pytest.param(
            "def wait(retry_state, policy):\n    retry_state.upcoming_sleep = policy(retry_state)\n",
            "def wait(retry_state, policy):\n    retry_state.upcoming_sleep = 0.0\n"
            "    retry_state.upcoming_sleep = policy(retry_state)\n",
            "wait",
            id="the-delay-a-retry-state-holds",
        ),
        pytest.param(
            "def connect(options):\n    options.timeout = 5\n",
            "def connect(options):\n    options.timeout = 10\n",
            "connect",
            id="a-timeout",
        ),
        pytest.param("MAX_RETRIES = 3\n", "MAX_RETRIES = 5\n", "<module>", id="a-module-constant"),
        pytest.param(
            "class Client:\n    timeout = 5\n",
            "class Client:\n    timeout = 10\n",
            "Client",
            id="a-class-attribute",
        ),
    ],
)
def test_an_assignment_that_writes_a_delay_or_a_timeout_is_a_retry_change(
    old: str, new: str, name: str
) -> None:
    """tenacity#694 set `retry_state.upcoming_sleep = 0.0` before asking the wait strategy, and
    the word `state` in the variable made it a state transition."""
    assert _rules(old, new, name)["assignment_change"] == RETRY


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        pytest.param(
            "def f(retry_policy):\n    retry_policy.name = 'a'\n",
            "def f(retry_policy):\n    retry_policy.name = 'b'\n",
            UNKNOWN,
            id="the-variable-written-into-names-a-retry",
        ),
        pytest.param(
            "def f(options):\n    options.limit = 5\n",
            "def f(options):\n    options.limit = 10\n",
            UNKNOWN,
            id="a-limit-is-no-retry",
        ),
        pytest.param(
            "def move(order):\n    order.state = 'old'\n",
            "def move(order):\n    order.state = 'new'\n",
            STATE,
            id="a-state-written-is-a-state",
        ),
    ],
)
def test_only_the_names_an_assignment_writes_make_it_a_retry_change(
    old: str, new: str, expected: tuple[BehaviorCategory, str]
) -> None:
    assert _rules(old, new, "f" if "def f" in old else "move")["assignment_change"] == expected


@pytest.mark.parametrize(
    ("old", "new", "name", "expected"),
    [
        pytest.param(
            "class Preview(Enum):\n    a = auto()\n",
            "class Preview(Enum):\n    a = auto()\n    b = auto()\n",
            "Preview",
            CLASS_ATTRIBUTE,
            id="an-enum-member-added",
        ),
        pytest.param(
            "class Response:\n    default_mimetype = 'text/plain'\n",
            "class Response:\n    default_mimetype = 'text/html'\n",
            "Response",
            CLASS_ATTRIBUTE,
            id="a-default",
        ),
        pytest.param(
            "class Job:\n    status = 'new'\n",
            "class Job:\n    status = 'queued'\n",
            "Job",
            STATE,
            id="named-for-state",
        ),
    ],
)
def test_a_class_attribute_is_an_output_contract_change(
    old: str, new: str, name: str, expected: tuple[BehaviorCategory, str]
) -> None:
    """black#5643 added a `Preview` member and werkzeug#3327 changed how `Response.location`
    converts: each was an unknown change, labeled an output contract change. A name the state or
    retry rules read keeps their category."""
    assert _rules(old, new, name)["assignment_change"] == expected


@pytest.mark.parametrize(
    ("old", "new", "name"),
    [
        pytest.param(
            "class Stream:\n    def close(self):\n        self._closed = True\n",
            "class Stream:\n    def close(self):\n        self._closed = True\n"
            "        self._waiters = None\n",
            "Stream.close",
            id="an-attribute",
        ),
        pytest.param(
            "class Registry:\n    def add(self, key, value):\n        self._items[key] = value\n",
            "class Registry:\n    def add(self, key, value):\n        self._items[key] = [value]\n",
            "Registry.add",
            id="an-item",
        ),
        pytest.param(
            "class Registry:\n    @classmethod\n    def register(cls, name):\n        cls.names = name\n",
            "class Registry:\n    @classmethod\n    def register(cls, name):\n"
            "        cls.names = [name]\n",
            "Registry.register",
            id="a-class-attribute",
        ),
        pytest.param(
            "class Stream:\n    async def receive(self):\n        self._buffer.waiting = 1\n",
            "class Stream:\n    async def receive(self):\n        self._buffer.waiting = 2\n",
            "Stream.receive",
            id="deep-in-an-async-method",
        ),
    ],
)
def test_what_a_method_keeps_on_its_instance_is_its_state(old: str, new: str, name: str) -> None:
    """anyio#1443's `close` now also empties `self._waiters`, an unknown change labeled a state
    transition."""
    assert _rules(old, new, name)["assignment_change"] == INSTANCE


@pytest.mark.parametrize(
    ("old", "new", "name", "expected"),
    [
        pytest.param(
            "class C:\n    def f(self):\n        x = 1\n        log(x)\n",
            "class C:\n    def f(self):\n        x = 2\n        log(x)\n",
            "C.f",
            UNKNOWN,
            id="a-local",
        ),
        pytest.param(
            "class C:\n    def f(self):\n        self.x = 1\n        return self.x\n",
            "class C:\n    def f(self):\n        self.x = 2\n        return self.x\n",
            "C.f",
            FLOW,
            id="kept-and-returned",
        ),
        pytest.param(
            "def f(self):\n    self.x = 1\n",
            "def f(self):\n    self.x = 2\n",
            "f",
            UNKNOWN,
            id="a-function-is-no-method",
        ),
    ],
)
def test_only_what_a_method_keeps_and_does_not_return_is_its_state(
    old: str, new: str, name: str, expected: tuple[BehaviorCategory, str]
) -> None:
    assert _rules(old, new, name)["assignment_change"] == expected


def test_an_assignment_delta_says_what_it_writes() -> None:
    (delta,) = _compare(
        "class S:\n    def close(self):\n        self._state.closed = False\n        n = 0\n",
        "class S:\n    def close(self):\n        self._state.closed = True\n        n = 1\n",
        "S.close",
    )
    assert delta.metadata["assigned_names"] == ["n", "self"]
    assert delta.metadata["stored_names"] == ["closed", "n"]
    assert delta.metadata["writes_instance"] is True


def test_a_snapshot_without_what_its_assignments_write_names_none() -> None:
    before, after = (
        {item.qualified_name: item for item in extract_symbols(source)}["S.close"]
        for source in (
            "class S:\n    def close(self):\n        self.timeout = 1\n",
            "class S:\n    def close(self):\n        self.timeout = 2\n",
        )
    )
    (delta,) = compare_symbol(
        "m.py", replace(before, stored=(), on_instance=()), after, (WIDE_HUNK,)
    )
    assert delta.metadata["stored_names"] == []
    assert "writes_instance" not in delta.metadata
    assert classify_delta(delta, RulesConfig())[5] == "SDW-RULE-UNKNOWN-ASSIGNMENT"


def _value(source: str) -> ast.expr:
    return ast.parse(source, mode="eval").body


def test_a_value_that_fits_is_shown_whole() -> None:
    assert unparse_whole(_value("[1, 2]"), MAX_ASSIGNED_VALUE_CHARS) == "[1, 2]"
    assert unparse_whole(None, MAX_ASSIGNED_VALUE_CHARS) == "None"
    # A node `ast.unparse` cannot render is named by its type, as `unparse_redacted` does.
    assert unparse_whole(ast.Call(), MAX_ASSIGNED_VALUE_CHARS) == "Call"


def test_a_value_cut_ends_with_a_digest_of_all_of_it() -> None:
    names = [f"name_{index:03d}" for index in range(40)]
    first, second, same = (
        unparse_whole(_value(repr(items)), MAX_ASSIGNED_VALUE_CHARS)
        for items in (names, [*names, "FunctionAuth"], list(names))
    )
    shown, _, digest = first.rpartition("…#")
    assert shown == repr(names)[:MAX_ASSIGNED_VALUE_CHARS]
    assert len(digest) == VALUE_DIGEST_CHARS
    assert second != first
    assert second.startswith(shown)
    assert same == first


def test_a_name_exported_past_the_cut_is_an_export() -> None:
    """httpx#3699's `__all__` gained `FunctionAuth` 355 characters in: no assignment changed, and
    the module's change was one no rule could name."""
    names = ", ".join(repr(f"name_{index:03d}") for index in range(40))
    old = f"__all__ = [{names}]\n"
    new = f"__all__ = [{names}, 'FunctionAuth']\n"
    assert _rules(old, new, "<module>") == {"assignment_change": EXPORTS}
