"""A module's changed top-level names are judged each on its own.

The Action's field test on meal-manager reviewed module-level findings that bundled every changed
top-level name with every reader of any of them. A tool's `SCHEMA` whose descriptions alone changed,
telling the agent what to say, sat beside a new `DAMAGED_WARNING` constant, so the module was no
redescribed body: its obligation asked what `HANDLER` does with both, where the agent's dialog was
the test to write, and `HANDLER`'s own findings already said what it does with the warning. A
package's `__init__.py` that added a name to `__all__`, a logger, and a constant said its new helper
reads all three, `__all__` included.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

import pytest

from semantic_diff_weaver.ast_diff import analyze_ast, module_references
from semantic_diff_weaver.ast_diff.extract import described_value, module_exports
from semantic_diff_weaver.call_graph import CallGraph, package_of, source_roots
from semantic_diff_weaver.config import WeaverConfig
from semantic_diff_weaver.evidence_scenarios import (
    Change,
    Context,
    changes_by_evidence,
    contexts_of,
    module_parts,
    weakly_anchored,
)
from semantic_diff_weaver.models import (
    BehaviorCategory,
    BehaviorChange,
    Evidence,
    Origin,
    Presentation,
    RiskLabel,
    ScoreExplanation,
)
from semantic_diff_weaver.obligations import generate_obligations
from semantic_diff_weaver.semantic_candidates import build_candidates
from semantic_diff_weaver.source import SourceHunk, SourceRevisionPair

if TYPE_CHECKING:
    from semantic_diff_weaver.models import TestObligation

WIDE_HUNK = SourceHunk(id="hunk-001", old_start=1, old_count=400, new_start=1, new_count=400)

HANDLER_BEFORE = (
    "NAME = 'list_items'\n"
    "SCHEMA = {'description': 'List the saved items.', 'type': 'object', 'properties': {}}\n\n\n"
    "def HANDLER(args):\n"
    "    return {'items': []}\n"
)
HANDLER_AFTER = (
    "NAME = 'list_items'\n"
    "DAMAGED_WARNING = 'The saved file is damaged.'\n"
    "SCHEMA = {\n"
    "    'description': 'List the saved items. If the file is damaged, tell the user.',\n"
    "    'type': 'object',\n"
    "    'properties': {},\n"
    "}\n\n\n"
    "def HANDLER(args):\n"
    "    result = {'items': []}\n"
    "    if args.get('damaged'):\n"
    "        result['warning'] = DAMAGED_WARNING\n"
    "    return result\n"
)


def _graph(sources: dict[str, str]) -> CallGraph:
    roots = source_roots(sources)
    modules = {
        path: module_references(source, package_of(path, roots)) for path, source in sources.items()
    }
    return CallGraph(modules, incomplete=False, roots=roots)


def _analysis(
    old: dict[str, str], new: dict[str, str], *, graph: bool = True
) -> tuple[list[BehaviorChange], list[TestObligation]]:
    deltas = analyze_ast(
        [
            SourceRevisionPair(
                path=path,
                old_path=path,
                new_path=path,
                hunks=(WIDE_HUNK,),
                old_text=old[path],
                new_text=new[path],
            )
            for path in new
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
        contexts=contexts_of(behaviors, changes, _graph(new) if graph else None),
    )
    return behaviors, obligations


def _of_module(
    behaviors: list[BehaviorChange], obligations: list[TestObligation], path: str
) -> list[TestObligation]:
    module = {
        item.id
        for item in behaviors
        if item.evidence[0].path == path and item.evidence[0].symbol == "<module>"
    }
    assert module
    return [item for item in obligations if module & set(item.behavior_change_ids)]


def test_a_new_constant_beside_a_redescribed_schema_asks_only_for_the_agent_s_dialog() -> None:
    path = "tools/list_items.py"
    behaviors, obligations = _analysis({path: HANDLER_BEFORE}, {path: HANDLER_AFTER})
    (dialog,) = _of_module(behaviors, obligations, path)
    assert dialog.title == "Check that an agent follows the changed description in `SCHEMA`"
    # The text that changed, which the agent is now told, rather than the assignment.
    assert dialog.given.startswith(
        "In the top-level code of `tools/list_items.py`, a description in `SCHEMA` now reads "
        "`List the saved items. If the file is damaged, tell the user.` where it read `List the "
        "saved items.`"
    )
    # `HANDLER` changed to read the new constant, and its own findings say what it does with it.
    assert not any("DAMAGED_WARNING" in item.title for item in obligations)
    assert any(item.evidence[0].symbol == "HANDLER" for item in behaviors), (
        "HANDLER keeps its own finding"
    )


def test_without_a_call_graph_a_new_constant_beside_the_schema_is_asked_about_apart() -> None:
    path = "tools/list_items.py"
    behaviors, obligations = _analysis({path: HANDLER_BEFORE}, {path: HANDLER_AFTER}, graph=False)
    titles = [item.title for item in _of_module(behaviors, obligations, path)]
    assert titles == [
        "Check that an agent follows the changed description in `SCHEMA`",
        "Clarify and characterize the code that reads `DAMAGED_WARNING`",
    ]


def test_a_changed_all_asks_what_a_star_import_binds_and_not_who_reads_it() -> None:
    path = "pkg/__init__.py"
    old = "__all__ = ['first']\n\n\ndef first():\n    return 1\n"
    new = (
        "import logging\n\n"
        "__all__ = ['first', 'second']\n"
        "logger = logging.getLogger(__name__)\n\n\n"
        "def first():\n    return 1\n\n\n"
        "def second():\n    logger.info('second')\n    return 2\n"
    )
    behaviors, obligations = _analysis({path: old}, {path: new})
    module = _of_module(behaviors, obligations, path)
    assert [item.title for item in module] == [
        "Assert the names a star import of `pkg/__init__.py` binds"
    ]
    assert (
        module[0].given
        == "In the top-level code of `pkg/__init__.py`, `__all__` now also lists `second`"
    )
    assert module[0].when == "Code imports `*` from the module"
    # The new logger is read only by the new `second`, whose own finding speaks for it.
    assert not any("`logger`" in item.title for item in obligations)


def test_a_name_no_code_reads_is_not_said_to_be_read_by_the_others_readers() -> None:
    path = "app/config.py"
    old = "LIMIT = 10\nLABEL = 'x'\n\n\ndef limit():\n    return LIMIT\n"
    new = "LIMIT = 20\nLABEL = 'y'\n\n\ndef limit():\n    return LIMIT\n"
    behaviors, obligations = _analysis({path: old}, {path: new})
    by_title = {item.title: item for item in _of_module(behaviors, obligations, path)}
    assert set(by_title) == {
        "Clarify and characterize what `limit` does with the changed `LIMIT`",
        "Clarify and characterize the code that reads `LABEL`",
    }
    assert by_title[
        "Clarify and characterize what `limit` does with the changed `LIMIT`"
    ].given == (
        "The top-level code of `app/config.py` now assigns `LIMIT = 20` where it assigned "
        "`LIMIT = 10`, which `limit` reads"
    )


def test_names_all_read_keep_one_obligation_as_before() -> None:
    path = "app/config.py"
    old = "LIMIT = 10\nLABEL = 'x'\n\n\ndef limit():\n    return LIMIT, LABEL\n"
    new = "LIMIT = 20\nLABEL = 'y'\n\n\ndef limit():\n    return LIMIT, LABEL\n"
    behaviors, obligations = _analysis({path: old}, {path: new})
    (module,) = _of_module(behaviors, obligations, path)
    assert (
        module.title
        == "Clarify and characterize what `limit` does with the changed `LABEL` and `LIMIT`"
    )


@pytest.mark.parametrize(
    ("change", "covered", "expected"),
    [
        (
            Change(
                kind="assignment_change",
                assigned_names=("SCHEMA", "WARNING"),
                described_names=("SCHEMA",),
                added_names=("WARNING",),
            ),
            ("WARNING",),
            (("SCHEMA",), False, ()),
        ),
        (
            Change(
                kind="assignment_change",
                assigned_names=("__all__", "logger"),
                added_names=("logger",),
                exports_added=("second",),
                exports_added_total=1,
            ),
            (),
            ((), True, ("logger",)),
        ),
        (
            Change(kind="assignment_change", assigned_names=("__all__",)),
            (),
            ((), False, ("__all__",)),
        ),
    ],
)
def test_module_parts_judges_each_name_on_its_own(
    change: Change, covered: tuple[str, ...], expected: tuple
) -> None:
    assert module_parts(change, covered) == expected


def _module_behavior() -> BehaviorChange:
    return BehaviorChange(
        id="bc-001",
        category=BehaviorCategory.UNKNOWN,
        summary="Changed.",
        observable_impact="Something differs.",
        risk=RiskLabel.MEDIUM,
        risk_score=50,
        confidence=0.8,
        evidence=[Evidence(id="ev-001", path="m.py", symbol="<module>", kind="assignment_change")],
        origin=Origin.DETERMINISTIC,
        score_explanation=ScoreExplanation(
            behavioral_impact=50,
            critical_path_weight=0,
            test_gap_weight=90,
            change_surface_weight=10,
        ),
    )


@pytest.mark.parametrize(
    ("change", "context", "weak"),
    [
        # Every name it writes is covered, with nothing else to say: it keeps the scenario that
        # names their readers.
        (
            Change(kind="assignment_change", assigned_names=("logger",), added_names=("logger",)),
            Context(readers=("ping",), read_by=(("logger", ("ping",)),), covered=("logger",)),
            False,
        ),
        # A redescribed schema says what to assert, whatever else is covered.
        (
            Change(
                kind="assignment_change",
                assigned_names=("SCHEMA", "WARNING"),
                described_names=("SCHEMA",),
                added_names=("WARNING",),
            ),
            Context(read_by=(("WARNING", ("HANDLER",)),), covered=("WARNING",)),
            False,
        ),
        # A name some code reads says what to call; one no code reads, nothing.
        (
            Change(kind="assignment_change", assigned_names=("LIMIT",)),
            Context(readers=("limit",), read_by=(("LIMIT", ("limit",)),)),
            False,
        ),
        (
            Change(kind="assignment_change", assigned_names=("LABEL",)),
            Context(read_by=(("LABEL", ()),)),
            True,
        ),
    ],
)
def test_a_module_s_names_decide_whether_its_scenario_says_enough(
    change: Change, context: Context, weak: bool
) -> None:
    assert weakly_anchored(_module_behavior(), {"ev-001": change}, context) is weak


def _value(source: str) -> ast.expr:
    statement = ast.parse(source).body[0]
    assert isinstance(statement, ast.Assign)
    return statement.value


def test_an_f_string_description_s_prose_is_redescribed_but_not_what_it_interpolates() -> None:
    before = described_value(_value("S = {'type': 'object', 'description': f'List {N} items.'}"))
    reworded = described_value(
        _value("S = {'type': 'object', 'description': f'List {N} items. Tell the user.'}")
    )
    recomputed = described_value(
        _value("S = {'type': 'object', 'description': f'List {M} items.'}")
    )
    assert before[1] == ("List {N} items.",)
    assert before[0] == reworded[0]
    assert before[0] != recomputed[0]
    # A value with no schema's description records nothing to compare.
    assert described_value(_value("S = {'description': 'A record, not a schema.'}")) == ("", ())


def test_a_module_s_exports_are_the_names_its_all_lists() -> None:
    body = ast.parse(
        "__all__ = ['b', 'a']\n__all__ += ['c']\n__all__.extend(['d'])\nOTHER = ['e']\n"
    ).body
    assert module_exports(body) == ("a", "b", "c", "d")


def test_a_module_s_assignment_delta_says_each_name_s_part() -> None:
    old = "__all__ = ['a']\nSCHEMA = {'type': 'object', 'description': 'List.'}\n"
    new = (
        "__all__ = ['a', 'b']\n"
        "WARNING = 'Damaged.'\n"
        "SCHEMA = {'type': 'object', 'description': 'List. Tell the user.'}\n"
    )
    (delta,) = [
        item
        for item in analyze_ast(
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
        if item.kind == "assignment_change"
    ]
    assert delta.metadata["assigned_names"] == ["SCHEMA", "WARNING", "__all__"]
    assert delta.metadata["described_names"] == ["SCHEMA"]
    assert delta.metadata["described_texts"] == [["List.", "List. Tell the user."]]
    assert delta.metadata["added_names"] == ["WARNING"]
    assert delta.metadata["exports_added"] == ["b"]
    assert delta.metadata["exports_removed"] == []
    # Not the module as a whole: a constant was added beside the schema.
    assert "described_only" not in delta.metadata


def test_two_values_alike_as_far_as_recorded_are_said_to_differ_past_it() -> None:
    # Each side's value is recorded only so far: quoted around where they differ, a schema that
    # gained a property past that point read the same on both sides.
    path = "app/limits.py"
    head = "X = '" + "a" * 300
    behaviors, obligations = _analysis({path: f"{head}b'\n"}, {path: f"{head}c'\n"}, graph=False)
    (module,) = _of_module(behaviors, obligations, path)
    assert module.given.startswith("The top-level code of `app/limits.py` now assigns `X = 'aaa")
    assert module.given.endswith("…` (it differs from what it assigned only past the part shown)")


def test_a_module_whose_every_name_is_covered_keeps_the_scenario_naming_their_readers() -> None:
    # Linked away, it joined the call of its top-level code, which runs only as the module is
    # imported: no better than the readers it names.
    path = "app/style.py"
    old = "class Style:\n    def __init__(self):\n        self.id = 0\n"
    new = (
        "from itertools import count\n\n"
        "_ids = count()\n\n\n"
        "class Style:\n    def __init__(self):\n        self.id = next(_ids)\n"
    )
    behaviors, obligations = _analysis({path: old}, {path: new})
    titles = [item.title for item in _of_module(behaviors, obligations, path)]
    assert "Clarify and characterize what `Style.__init__` does with the changed `_ids`" in titles


def test_covered_names_beside_one_no_code_reads_keep_the_scenario_naming_their_readers() -> None:
    # Only the unread name left to ask about, the finding was linked to its top-level call.
    path = "app/hooks.py"
    old = "def f():\n    return 1\n"
    new = (
        "def f():\n    return 1\n\n\n"
        "HOOK = [f]\nUNUSED = [f]\n\n\n"
        "def g():\n    return HOOK\n"
    )
    behaviors, obligations = _analysis({path: old}, {path: new})
    titles = [item.title for item in _of_module(behaviors, obligations, path)]
    assert titles == ["Clarify and characterize what `g` does with the changed `HOOK` and `UNUSED`"]
