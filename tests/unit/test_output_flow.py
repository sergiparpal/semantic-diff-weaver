"""What a changed line feeds: the value its function returns, or the names its module exports.

On the labeled benchmark's development cases, 46 of the 74 behavior changes that got no finding
in an expected category were output contract changes, and most of those were reported as unknown
changes of an assignment, a condition, or a comparison that decided what the function returned.
`docs/benchmark.md` records the measurements.
"""

from __future__ import annotations

import ast

import pytest

from semantic_diff_weaver.ast_diff.compare import changes_output, compare_symbol, own_call_names
from semantic_diff_weaver.ast_diff.extract import extract_symbols, output_lines
from semantic_diff_weaver.ast_diff.types import StructuralDelta, SymbolSnapshot
from semantic_diff_weaver.models import BehaviorCategory, RulesConfig
from semantic_diff_weaver.semantic_candidates import classify_delta
from semantic_diff_weaver.source import SourceHunk

WIDE_HUNK = SourceHunk(id="hunk-001", old_start=1, old_count=400, new_start=1, new_count=400)


def _lines(source: str) -> set[int]:
    (function,) = ast.parse(source).body
    assert isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
    return set(output_lines(function))


def _symbol(source: str, name: str) -> SymbolSnapshot:
    return {item.qualified_name: item for item in extract_symbols(source)}[name]


def _compare(old: str, new: str, name: str) -> list[StructuralDelta]:
    before, after = extract_symbols(old), extract_symbols(new)
    return compare_symbol(
        "pkg/mod.py",
        {item.qualified_name: item for item in before}[name],
        {item.qualified_name: item for item in after}[name],
        (WIDE_HUNK,),
        own_calls=own_call_names(before, after),
    )


def _rules(old: str, new: str, name: str = "f") -> dict[str, tuple[BehaviorCategory, str]]:
    result = {}
    for delta in _compare(old, new, name):
        category, _, _, _, _, rule = classify_delta(delta, RulesConfig())
        result[delta.kind] = (category, rule)
    return result


FLOW = (BehaviorCategory.OUTPUT_CONTRACT, "SDW-RULE-OUTPUT-FLOW")
# Functions of the module itself: a call to one is no dependency interaction.
HELPERS = "def _a(x):\n    return x\n\n\ndef _b(x):\n    return x\n\n\n"


@pytest.mark.parametrize(
    ("source", "lines"),
    [
        pytest.param(
            "def f(x):\n    y = x + 1\n    z = 0\n    return y\n", {2, 4}, id="what-it-returns"
        ),
        pytest.param(
            "def f(x):\n    a = x\n    b = a * 2\n    return b\n", {2, 3, 4}, id="through-names"
        ),
        pytest.param(
            "def f(x):\n    if x:\n        return 1\n    log(x)\n    return 0\n",
            {2, 3, 5},
            id="a-condition-guarding-a-return",
        ),
        pytest.param(
            "def f(xs):\n    total = 0\n    for x in xs:\n        total += x\n    return total\n",
            {2, 3, 4, 5},
            id="a-loop-adding-to-it",
        ),
        pytest.param(
            "def f(xs):\n    out = []\n    for x in xs:\n        out.append(x)\n"
            "    return '/'.join(out)\n",
            {2, 3, 4, 5},
            id="a-method-changing-it",
        ),
        pytest.param(
            "def f(p):\n    with open(p) as h:\n        return h.read()\n",
            {2, 3},
            id="a-with-block",
        ),
        pytest.param(
            "def f(xs):\n    for x in xs:\n        yield x * 2\n", {2, 3}, id="a-generator"
        ),
        pytest.param(
            "def f(self):\n    self._cache = 1\n    self.other = 2\n    return self._cache\n",
            {2, 4},
            id="an-attribute",
        ),
        pytest.param(
            "def f(self):\n    self.count = 1\n    return self\n", {2, 3}, id="the-object-itself"
        ),
        pytest.param("def f(x):\n    log(x)\n    print(x)\n", set(), id="nothing-returned"),
        pytest.param(
            "def f(x):\n    y = 1\n    def g():\n        return y\n    return None\n",
            {5},
            id="a-nested-function-s-return",
        ),
    ],
)
def test_output_lines_follow_what_the_function_returns(source: str, lines: set[int]) -> None:
    assert _lines(source) == lines


def test_a_value_that_feeds_the_return_is_an_output_contract_change() -> None:
    """attrs#1593's `annot = getattr(annot, '__forward_arg__', annot)` decided what
    `_is_class_var` returned, and was an unknown change."""
    old = "def f(annot):\n    annot = str(annot)\n    return annot.startswith('C')\n"
    new = (
        "def f(annot):\n    annot = getattr(annot, '__forward_arg__', annot)\n"
        "    annot = str(annot)\n    return annot.startswith('C')\n"
    )
    assert _rules(old, new)["assignment_change"] == FLOW


@pytest.mark.parametrize(
    ("old", "new", "kind"),
    [
        pytest.param(
            "def f(x):\n    if x.a:\n        return 1\n    return 0\n",
            "def f(x):\n    if x.b:\n        return 1\n    return 0\n",
            "condition_change",
            id="a-condition",
        ),
        pytest.param(
            "def f(x):\n    return x is None\n",
            "def f(x):\n    return x is not None\n",
            "comparison_change",
            id="a-comparison",
        ),
        pytest.param(
            "def f(xs):\n    n = 0\n    for x in xs:\n        n += 1\n    return n\n",
            "def f(xs):\n    n = 0\n    for x in xs[1:]:\n        n += 1\n    return n\n",
            "loop_change",
            id="a-loop",
        ),
        pytest.param(
            f"{HELPERS}def f(x):\n    return _a(x)\n",
            f"{HELPERS}def f(x):\n    return _b(x)\n",
            "call_change",
            id="an-internal-call",
        ),
    ],
)
def test_each_unknown_change_that_feeds_the_return_changes_it(
    old: str, new: str, kind: str
) -> None:
    assert _rules(old, new)[kind] == FLOW


@pytest.mark.parametrize(
    ("old", "new", "kind"),
    [
        pytest.param(
            "def f(x):\n    y = 1\n    log(y)\n    return x\n",
            "def f(x):\n    y = 2\n    log(y)\n    return x\n",
            "assignment_change",
            id="a-value-it-only-logs",
        ),
        pytest.param(
            "def f(x):\n    if x.a:\n        log(x)\n",
            "def f(x):\n    if x.b:\n        log(x)\n",
            "condition_change",
            id="nothing-returned",
        ),
    ],
)
def test_a_change_that_feeds_no_return_stays_unknown(old: str, new: str, kind: str) -> None:
    category, rule = _rules(old, new)[kind]
    assert category is BehaviorCategory.UNKNOWN
    assert rule != "SDW-RULE-OUTPUT-FLOW"


@pytest.mark.parametrize(
    ("old", "new", "kind", "category"),
    [
        pytest.param(
            "def f(size):\n    if size > 3:\n        return 1\n    return 0\n",
            "def f(size):\n    if size >= 3:\n        return 1\n    return 0\n",
            "comparison_change",
            BehaviorCategory.BOUNDARY,
            id="a-boundary",
        ),
        pytest.param(
            "def f(order):\n    order.status = 'a'\n    return order\n",
            "def f(order):\n    order.status = 'b'\n    return order\n",
            "assignment_change",
            BehaviorCategory.STATE_TRANSITION,
            id="a-state",
        ),
    ],
)
def test_a_change_a_rule_names_keeps_its_category(
    old: str, new: str, kind: str, category: BehaviorCategory
) -> None:
    assert _rules(old, new)[kind][0] is category


def test_a_removed_value_feeds_the_return_it_fed_before() -> None:
    old = "def f(x):\n    if x:\n        return 1\n    return 0\n"
    new = "def f(x):\n    return 0\n"
    before, after = _symbol(old, "f"), _symbol(new, "f")
    assert changes_output(before, after, "conditions")


@pytest.mark.parametrize(
    ("old", "new"),
    [
        pytest.param(
            "__all__ = ['Auth', 'BasicAuth']\n",
            "__all__ = ['Auth', 'BasicAuth', 'FunctionAuth']\n",
            id="a-name-exported",
        ),
        pytest.param("__all__ = ['a']\n", "__all__ = ['a']\n__all__ += ['b']\n", id="extended"),
    ],
)
def test_the_names_a_module_exports_are_its_output_contract(old: str, new: str) -> None:
    """httpx#3699 exported `FunctionAuth` and attrs#1571 a new validator: each was an unknown
    change of the module, labeled an output contract change."""
    assert _rules(old, new, "<module>")["assignment_change"] == (
        BehaviorCategory.OUTPUT_CONTRACT,
        "SDW-RULE-EXPORTS",
    )


def test_a_local_named_like_all_is_not_an_export() -> None:
    old = "def f():\n    __all__ = ['a']\n    log(__all__)\n"
    new = "def f():\n    __all__ = ['b']\n    log(__all__)\n"
    assert _rules(old, new)["assignment_change"][1] != "SDW-RULE-EXPORTS"
