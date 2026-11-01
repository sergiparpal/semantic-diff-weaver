"""Findings in Python code that calls connect are grouped, each group led by its entry points."""

from __future__ import annotations

import pytest

from semantic_diff_weaver.ast_diff import module_references
from semantic_diff_weaver.call_graph import CallGraph, package_of, source_roots
from semantic_diff_weaver.change_groups import group_changes
from semantic_diff_weaver.models import (
    BehaviorCategory,
    BehaviorChange,
    ChangeGroup,
    Evidence,
    Origin,
    Presentation,
    RiskLabel,
    ScoreExplanation,
)

MODULES = {
    "src/shop/__init__.py": "",
    "src/shop/money.py": (
        "def parse_amount(text):\n    return int(text)\n\n\n"
        "def round_total(value):\n    return round(value, 2)\n"
    ),
    "src/shop/cart.py": (
        "from shop.money import parse_amount, round_total\n\n\n"
        "def _validate(text):\n    return parse_amount(text)\n\n\n"
        "def checkout(text):\n    return round_total(_validate(text))\n\n\n"
        "def refund(text):\n    return parse_amount(text)\n\n\n"
        "def main(text):\n    return checkout(text), refund(text)\n\n\n"
        "class Cart:\n"
        "    def total(self):\n        return self._discount()\n\n"
        "    def _discount(self):\n        return 0\n"
    ),
    "src/shop/loop.py": (
        "def ping(n):\n    return pong(n - 1) if n else 0\n\n\n"
        "def pong(n):\n    return ping(n - 1) if n else 1\n\n\n"
        "def echo(n):\n    return n\n\n\n"
        "def serve(n):\n    return ping(n) + echo(n)\n"
    ),
    "src/shop/limits.py": "MAX_TOTAL = 100\nROUNDING = 2\nUNUSED = 0\n",
    "src/shop/billing.py": (
        "from shop import limits\n"
        "from shop.cart import checkout\n"
        "from shop.limits import MAX_TOTAL\n\n\n"
        "def cap(total):\n    return min(total, MAX_TOTAL)\n\n\n"
        "def bill(text):\n    return cap(checkout(text))\n\n\n"
        "def precision():\n    return limits.ROUNDING\n\n\n"
        "def audit(MAX_TOTAL):\n    return MAX_TOTAL\n"
    ),
}


def graph() -> CallGraph:
    roots = source_roots(MODULES)
    return CallGraph(
        {path: module_references(text, package_of(path, roots)) for path, text in MODULES.items()},
        roots=roots,
    )


def behavior(index: int, path: str, symbol: str | None) -> BehaviorChange:
    return BehaviorChange(
        id=f"bc-{index:03d}",
        category=BehaviorCategory.BOUNDARY,
        summary="Changed behavior.",
        observable_impact="An outcome appears to differ.",
        risk=RiskLabel.MEDIUM,
        risk_score=50,
        confidence=0.8,
        evidence=[
            Evidence(id=f"ev-{index:03d}", path=path, symbol=symbol, kind="comparison_change")
        ],
        assumptions=[],
        presentation=Presentation.FINDING,
        origin=Origin.DETERMINISTIC,
        score_explanation=ScoreExplanation(
            behavioral_impact=50,
            critical_path_weight=10,
            test_gap_weight=90,
            change_surface_weight=30,
        ),
    )


def groups(*changed: tuple[str, str | None]) -> list[tuple[list[str], list[str]]]:
    behaviors = [behavior(index, path, symbol) for index, (path, symbol) in enumerate(changed, 1)]
    return [
        (group.behavior_change_ids, group.entry_points)
        for group in group_changes(behaviors, graph())
    ]


CART, MONEY, LOOP = "src/shop/cart.py", "src/shop/money.py", "src/shop/loop.py"
LIMITS, BILLING = "src/shop/limits.py", "src/shop/billing.py"


def test_a_change_reached_through_unchanged_code_is_grouped_with_what_reaches_it() -> None:
    # `checkout` reaches `parse_amount` through `_validate`, which did not change.
    assert groups((CART, "checkout"), (MONEY, "parse_amount"), (MONEY, "round_total")) == [
        (["bc-001", "bc-002", "bc-003"], ["bc-001"])
    ]


def test_each_entry_point_leads_the_group_of_what_it_reaches() -> None:
    assert groups((CART, "checkout"), (CART, "refund"), (MONEY, "parse_amount")) == [
        (["bc-001", "bc-003"], ["bc-001"]),
        (["bc-002", "bc-003"], ["bc-002"]),
    ]


def test_changes_that_only_share_a_caller_are_not_connected() -> None:
    # `main` reaches both, but did not change; neither reaches the other.
    assert groups((CART, "refund"), (MONEY, "round_total")) == []
    assert groups((CART, "checkout"), (CART, "refund")) == []


def test_a_chain_is_one_group_led_by_its_head() -> None:
    assert groups((MONEY, "parse_amount"), (CART, "_validate"), (CART, "main")) == [
        (["bc-001", "bc-002", "bc-003"], ["bc-003"])
    ]


def test_changes_that_reach_each_other_lead_their_group_together() -> None:
    assert groups((LOOP, "pong"), (LOOP, "ping")) == [(["bc-001", "bc-002"], ["bc-001", "bc-002"])]
    # `serve` reaches the pair and `echo`, and nothing reaches it.
    assert groups((LOOP, "ping"), (LOOP, "serve"), (LOOP, "pong"), (LOOP, "echo")) == [
        (["bc-001", "bc-002", "bc-003", "bc-004"], ["bc-002"])
    ]


def test_a_method_reached_on_its_own_instance_is_connected() -> None:
    assert groups((CART, "Cart._discount"), (CART, "Cart.total")) == [
        (["bc-001", "bc-002"], ["bc-002"])
    ]


def test_the_findings_of_one_symbol_go_together_in_the_findings_order() -> None:
    assert groups((MONEY, "parse_amount"), (CART, "refund"), (MONEY, "parse_amount")) == [
        (["bc-001", "bc-002", "bc-003"], ["bc-002"])
    ]


@pytest.mark.parametrize(
    "changed",
    [
        ((CART, "<module>"), (MONEY, "parse_amount")),
        ((CART, None), (MONEY, "parse_amount")),
        ((CART, "<unparsed>"), (MONEY, "parse_amount")),
        ((CART, "removed_function"), (MONEY, "parse_amount")),
        (("openapi.yaml", "GET /orders"), (MONEY, "parse_amount")),
        (("config/app.yaml", "http.max_retries"), (MONEY, "parse_amount")),
    ],
    ids=["module", "no-symbol", "unparsed", "not-in-graph", "api-schema", "config-key"],
)
def test_what_no_call_reaches_is_never_grouped(changed: tuple[tuple[str, str | None], ...]) -> None:
    assert groups(*changed) == []


def test_without_a_graph_nothing_is_grouped() -> None:
    assert (
        group_changes([behavior(1, CART, "checkout"), behavior(2, MONEY, "parse_amount")], None)
        == []
    )


def test_group_ids_number_the_groups_in_order() -> None:
    behaviors = [
        behavior(1, CART, "checkout"),
        behavior(2, MONEY, "parse_amount"),
        behavior(3, LOOP, "serve"),
        behavior(4, LOOP, "echo"),
    ]
    assert [item.id for item in group_changes(behaviors, graph())] == ["cg-001", "cg-002"]
    assert all(isinstance(item, ChangeGroup) for item in group_changes(behaviors, graph()))


def written(
    *changed: tuple[str, str | None], variables: frozenset[str]
) -> list[tuple[list[str], list[str], dict[str, list[str]]]]:
    """The groups of `changed`, whose findings in synthetic symbols are said to assign
    `variables`."""
    behaviors = [behavior(index, path, symbol) for index, (path, symbol) in enumerate(changed, 1)]
    assigned = {
        item.id: variables
        for item in behaviors
        if (item.evidence[0].symbol or "<module>").startswith("<")
    }
    return [
        (
            group.behavior_change_ids,
            group.entry_points,
            {item.behavior_change_id: item.names for item in group.variables},
        )
        for group in group_changes(behaviors, graph(), assigned)
    ]


TOTAL = frozenset({"MAX_TOTAL"})


def test_top_level_code_is_grouped_with_the_changed_code_that_reads_its_variable() -> None:
    assert written((LIMITS, "<module>"), (BILLING, "cap"), variables=TOTAL) == [
        (["bc-001", "bc-002"], ["bc-002"], {"bc-001": ["MAX_TOTAL"]})
    ]
    assert written((LIMITS, None), (BILLING, "cap"), variables=TOTAL) == [
        (["bc-001", "bc-002"], ["bc-002"], {"bc-001": ["MAX_TOTAL"]})
    ]


def test_a_variable_read_through_unchanged_code_is_reached_by_what_calls_it() -> None:
    # `bill` reads no limit itself; the `cap` it calls does.
    assert written((BILLING, "bill"), (LIMITS, "<module>"), variables=TOTAL) == [
        (["bc-001", "bc-002"], ["bc-001"], {"bc-002": ["MAX_TOTAL"]})
    ]
    assert written((BILLING, "cap"), (BILLING, "bill"), (LIMITS, "<module>"), variables=TOTAL) == [
        (["bc-001", "bc-002", "bc-003"], ["bc-002"], {"bc-003": ["MAX_TOTAL"]})
    ]


def test_each_reader_leads_a_group_naming_only_the_variables_it_reads() -> None:
    assert written(
        (LIMITS, "<module>"),
        (BILLING, "cap"),
        (BILLING, "precision"),
        variables=frozenset({"MAX_TOTAL", "ROUNDING", "UNUSED"}),
    ) == [
        (["bc-001", "bc-002"], ["bc-002"], {"bc-001": ["MAX_TOTAL"]}),
        (["bc-001", "bc-003"], ["bc-003"], {"bc-001": ["ROUNDING"]}),
    ]


def test_a_variable_a_call_group_reaches_joins_that_group() -> None:
    assert written(
        (BILLING, "bill"), (MONEY, "parse_amount"), (LIMITS, "<module>"), variables=TOTAL
    ) == [(["bc-001", "bc-002", "bc-003"], ["bc-001"], {"bc-003": ["MAX_TOTAL"]})]


@pytest.mark.parametrize(
    ("changed", "variables"),
    [
        # Nothing reads it.
        (((LIMITS, "<module>"), (BILLING, "cap")), frozenset({"UNUSED"})),
        # `audit` reads its own parameter of that name.
        (((LIMITS, "<module>"), (BILLING, "audit")), TOTAL),
        # Top-level code leads nothing: nothing calls it.
        (((LIMITS, "<module>"), (BILLING, "<module>")), TOTAL),
        # Its first evidence is no top-level code, whatever `assigned` says.
        (((BILLING, "<unparsed>"), (BILLING, "cap")), TOTAL),
    ],
    ids=["unread", "shadowed", "no-reader-changed", "unparsed"],
)
def test_top_level_code_whose_variables_no_changed_code_reads_is_never_grouped(
    changed: tuple[tuple[str, str | None], ...], variables: frozenset[str]
) -> None:
    assert written(*changed, variables=variables) == []
