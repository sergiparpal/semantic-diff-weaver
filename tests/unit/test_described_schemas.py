"""A schema that changed only how it describes a tool is tested through the agent that reads it.

A tool's schema tells an agent what the tool does and when to call it, and a validator reads none
of its descriptions. Replaying the Action's field test on meal-manager, three findings on schemas
whose descriptions alone changed asked to call the tool's handler with input the changed value
decides, though the handler is handed the same arguments: what changed is what the agent warns
about and waits for before it calls the tool. A fourth said that a repeated call keeps a
transition's state stable, which a handler that counts the meals it is handed never does.
"""

from __future__ import annotations

import ast

import pytest

from semantic_diff_weaver.ast_diff import analyze_ast
from semantic_diff_weaver.ast_diff.compare import compare_symbol, is_redescribed
from semantic_diff_weaver.ast_diff.extract import (
    description_literals,
    extract_symbols,
    fingerprint_without_descriptions,
)
from semantic_diff_weaver.ast_diff.types import StructuralDelta, SymbolSnapshot
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

TOOL = (
    "NAME = 'clear_fridge'\n"
    "SCHEMA = {\n"
    "    'description': 'Empty the fridge completely.',\n"
    "    'type': 'object',\n"
    "    'properties': {'keep': {'type': 'string', 'description': 'What to keep.'}},\n"
    "    'required': [],\n"
    "}\n"
)
WARNED = TOOL.replace("completely.", "completely, pantry staples included: ask first.")


def _symbol(source: str, name: str) -> SymbolSnapshot:
    return {item.qualified_name: item for item in extract_symbols(source)}[name]


def _compare(old: str, new: str, name: str = "<module>") -> list[StructuralDelta]:
    return compare_symbol("tools/fridge.py", _symbol(old, name), _symbol(new, name), (WIDE_HUNK,))


def _described(old: str, new: str, name: str = "<module>") -> list[tuple[str, bool]]:
    return [
        (item.kind, bool(item.metadata.get("described_only"))) for item in _compare(old, new, name)
    ]


@pytest.mark.parametrize(
    "new",
    [
        pytest.param(WARNED, id="the-tool"),
        pytest.param(TOOL.replace("What to keep.", "What to leave in."), id="a-property"),
        pytest.param(
            TOOL.replace("'Empty the fridge completely.'", "('Empty the fridge.' ' Ask first.')"),
            id="implicitly-joined",
        ),
    ],
)
def test_a_schema_that_changed_only_its_descriptions_is_described_only(new: str) -> None:
    assert _described(TOOL, new) == [("assignment_change", True)]
    assert is_redescribed(_symbol(TOOL, "<module>"), _symbol(new, "<module>"))


@pytest.mark.parametrize(
    ("old", "new"),
    [
        pytest.param(
            "TOOLS = [{'name': 'clear', 'description': 'Empty it.', 'parameters': {}}]\n",
            "TOOLS = [{'name': 'clear', 'description': 'Empty it all.', 'parameters': {}}]\n",
            id="a-function-tool",
        ),
        pytest.param(
            "TOOL = {'name': 'clear', 'description': 'Empty it.', 'input_schema': {}}\n",
            "TOOL = {'name': 'clear', 'description': 'Empty it all.', 'input_schema': {}}\n",
            id="input-schema",
        ),
        pytest.param(
            "TOOL = {'name': 'clear', 'description': 'Empty it.', 'inputSchema': {}}\n",
            "TOOL = {'name': 'clear', 'description': 'Empty it all.', 'inputSchema': {}}\n",
            id="mcp-input-schema",
        ),
    ],
)
def test_a_tool_definition_that_changed_only_its_description_is_described_only(
    old: str, new: str
) -> None:
    assert _described(old, new) == [("assignment_change", True)]


@pytest.mark.parametrize(
    ("old", "new"),
    [
        pytest.param(
            TOOL,
            WARNED.replace("'required': []", "'required': ['keep']"),
            id="and-what-it-requires",
        ),
        pytest.param(
            "DISH = {'name': 'soup', 'description': 'Hot.'}\n",
            "DISH = {'name': 'soup', 'description': 'Very hot.'}\n",
            id="a-record-is-no-schema",
        ),
        pytest.param(
            "TEXT = 'Empty it.'\nSCHEMA = {'description': TEXT, 'type': 'object'}\n",
            "TEXT = 'Empty it all.'\nSCHEMA = {'description': TEXT, 'type': 'object'}\n",
            id="a-description-held-elsewhere",
        ),
        pytest.param(
            "SCHEMA = {'description': 'Empty it.', 'type': 'object'}\nLIMIT = 1\n",
            "SCHEMA = {'description': 'Empty it all.', 'type': 'object'}\nLIMIT = 2\n",
            id="beside-another-change",
        ),
    ],
)
def test_a_schema_that_changed_more_than_its_descriptions_is_not(old: str, new: str) -> None:
    assert [described for _, described in _described(old, new)] == [False]
    assert not is_redescribed(_symbol(old, "<module>"), _symbol(new, "<module>"))


def test_a_schema_a_function_returns_is_described_only_too() -> None:
    old = "def schema():\n    return {'description': 'Empty it.', 'type': 'object'}\n"
    new = old.replace("Empty it.", "Empty it all.")
    assert _described(old, new, "schema") == [("return_change", True)]


def test_blanking_a_description_leaves_the_tree_as_it_was() -> None:
    """The literals are blanked in place for one fingerprint, in a tree every symbol shares."""
    tree = ast.parse(TOOL)
    before = ast.dump(tree)
    assert fingerprint_without_descriptions(tree.body, "plain") != "plain"
    assert ast.dump(tree) == before
    assert [literal.value for literal in description_literals(tree.body[1])] == [
        "Empty the fridge completely.",
        "What to keep.",
    ]
    assert fingerprint_without_descriptions(ast.parse("LIMIT = 1\n").body, "plain") == "plain"


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


def test_a_redescribed_schema_asks_what_the_agent_does_with_the_new_text() -> None:
    ((kind, title, given, when, then),) = _obligations(TOOL, WARNED)
    assert kind is ObligationType.INTERACTION
    assert title == "Check that an agent follows the changed description in `SCHEMA`"
    assert given.startswith("The top-level code of `m.py` now assigns ")
    assert "pantry staples included: ask first." in given
    assert given.endswith(
        ": only schema descriptions changed, which an agent given the tool reads before it calls "
        "it, and no code computes with"
    )
    assert when == (
        "An agent is given the tool under the new description, with a request the changed text "
        "speaks to"
    )
    assert then == (
        "What the agent tells the user, asks before it acts, and calls follows the new text"
    )


def test_a_schema_that_changed_what_it_requires_keeps_the_obligation_of_its_readers() -> None:
    changed = WARNED.replace("'required': []", "'required': ['keep']")
    ((kind, title, _, _, _),) = _obligations(TOOL, changed)
    assert kind is ObligationType.REVIEW
    assert title == "Clarify and characterize the code that reads `SCHEMA`"


def test_a_transition_asserts_what_a_repeated_call_leaves_without_calling_it_stable() -> None:
    """A handler that counts each meal it is handed leaves another state on every call."""
    old = "def register(machine, meal):\n    machine.state = 'cooked'\n"
    new = "def register(machine, meal):\n    machine.state = 'learned'\n"
    found = [item for item in _obligations(old, new) if item[0] is ObligationType.STATE]
    assert [(title, then) for _, title, _, _, then in found] == [
        (
            "Verify the allowed transition `register` now makes",
            "The resulting state is asserted, and so is the state a repeated call leaves",
        )
    ]
