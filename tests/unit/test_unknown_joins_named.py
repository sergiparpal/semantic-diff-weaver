"""An unknown finding beside one that names a category, on the same symbol, joins it.

On the labeled benchmark's 180 development cases, 81 unknown findings stood beside a named one on
their symbol, each in no expected category. `docs/benchmark.md` records the measurements. The
join comes after the obligations are written, so the unknown finding's obligations, such as the
one exercising both outcomes of a changed condition, stay and point to the named finding.
"""

from __future__ import annotations

from semantic_diff_weaver.models import (
    BehaviorCategory,
    BehaviorChange,
    CoverageStatus,
    Evidence,
    ObligationType,
    Origin,
    Presentation,
    RiskLabel,
    ScoreExplanation,
)
from semantic_diff_weaver.models import TestObligation as Obligation
from semantic_diff_weaver.semantic_candidates import SemanticCandidate
from semantic_diff_weaver.service import _join_unknown_to_named, _ScoredCandidate, analyze

SAVE_OLD = (
    "import store\n\n\ndef save(record):\n    if record.dirty:\n        store.write(record)\n"
)
SAVE_NEW = (
    "import store\n\n\ndef save(record):\n    if record.dirty or record.new:\n"
    "        store.write(record, flush=True)\n"
)


def _analysis(repo_factory, old: str, new: str) -> dict:
    repo, base, head = repo_factory({"app.py": old}, {"app.py": new})
    return analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}
    )


def test_an_unknown_finding_joins_the_named_finding_of_its_symbol(repo_factory) -> None:
    analysis = _analysis(repo_factory, SAVE_OLD, SAVE_NEW)
    (finding,) = analysis["behavior_changes"]
    assert finding["id"] == "bc-001"
    assert finding["category"] == "side_effect_change"
    # The named finding's evidence comes first, the joined one's after it.
    assert [item["kind"] for item in finding["evidence"]] == ["call_change", "condition_change"]
    titles = {item["title"]: item["behavior_change_ids"] for item in analysis["test_obligations"]}
    assert titles == {
        "Verify the side-effect occurrence of the changed call in `save`": ["bc-001"],
        "Exercise both outcomes of the changed condition in `save`": ["bc-001"],
    }
    assert analysis["summary"]["behavior_changes"] == 1
    assert analysis["summary"]["test_obligations"] == 2


def test_an_unknown_finding_alone_stays(repo_factory) -> None:
    # The condition changes, and the call it guards does not.
    new = SAVE_NEW.replace(", flush=True", "")
    analysis = _analysis(repo_factory, SAVE_OLD, new)
    assert [item["category"] for item in analysis["behavior_changes"]] == [
        "unknown_semantic_change"
    ]


def _evidence(number: int, symbol: str, kind: str = "call_change") -> Evidence:
    return Evidence(id=f"ev-{number:03d}", path="app.py", symbol=symbol, kind=kind)


def _behavior(
    number: int,
    category: BehaviorCategory,
    symbol: str = "save",
    *,
    confidence: float = 0.7,
    presentation: Presentation = Presentation.FINDING,
) -> BehaviorChange:
    return BehaviorChange(
        id=f"bc-{number:03d}",
        category=category,
        summary=f"{category.value} in {symbol}",
        observable_impact="Callers can observe it.",
        risk=RiskLabel.MEDIUM,
        risk_score=40,
        confidence=confidence,
        evidence=[_evidence(number, symbol)],
        presentation=presentation,
        origin=Origin.DETERMINISTIC,
        score_explanation=ScoreExplanation(
            behavioral_impact=40,
            critical_path_weight=0,
            test_gap_weight=50,
            change_surface_weight=10,
        ),
    )


def _item(behavior: BehaviorChange, names: frozenset[str] = frozenset()) -> _ScoredCandidate:
    first = behavior.evidence[0]
    return _ScoredCandidate(
        candidate=SemanticCandidate(
            path=first.path,
            symbol=first.symbol or "<module>",
            category=behavior.category,
            summary=behavior.summary,
            observable_impact=behavior.observable_impact,
            evidence=tuple(behavior.evidence),
            confidence_baseline=behavior.confidence,
            assigned_names=names,
        ),
        confidence=behavior.confidence,
    )


def _obligation(number: int, linked: list[str], priority: int, title: str) -> Obligation:
    return Obligation(
        id=f"to-{number:03d}",
        behavior_change_ids=linked,
        type=ObligationType.REGRESSION,
        priority=priority,
        title=title,
        given="The changed code",
        when="It runs",
        then="Its outcome is asserted",
        coverage_status=CoverageStatus.NONE_FOUND,
        origin=Origin.DETERMINISTIC,
        confidence=0.7,
    )


def _join(
    behaviors: list[BehaviorChange],
    obligations: list[Obligation] | None = None,
    names: dict[str, frozenset[str]] | None = None,
) -> tuple[list[BehaviorChange], list[Obligation], list[_ScoredCandidate]]:
    items = [_item(behavior, (names or {}).get(behavior.id, frozenset())) for behavior in behaviors]
    return _join_unknown_to_named(behaviors, obligations or [], items)


def test_the_named_finding_keeps_what_it_says_and_gains_the_evidence() -> None:
    named = _behavior(1, BehaviorCategory.SIDE_EFFECT, confidence=0.72)
    unknown = _behavior(2, BehaviorCategory.UNKNOWN, confidence=0.55)
    (finding,), _, (item,) = _join([named, unknown])
    assert finding.model_dump(exclude={"evidence"}) == named.model_dump(exclude={"evidence"})
    assert [item.id for item in finding.evidence] == ["ev-001", "ev-002"]
    assert item.candidate.category is BehaviorCategory.SIDE_EFFECT


def test_the_named_finding_of_highest_confidence_takes_it() -> None:
    behaviors = [
        _behavior(1, BehaviorCategory.DEPENDENCY_INTERACTION, confidence=0.6),
        _behavior(2, BehaviorCategory.OUTPUT_CONTRACT, confidence=0.8),
        _behavior(3, BehaviorCategory.UNKNOWN),
    ]
    found, _, _ = _join(behaviors)
    assert [(item.category, [e.id for e in item.evidence]) for item in found] == [
        (BehaviorCategory.DEPENDENCY_INTERACTION, ["ev-001"]),
        (BehaviorCategory.OUTPUT_CONTRACT, ["ev-002", "ev-003"]),
    ]


def test_of_two_named_findings_alike_in_confidence_the_category_decides() -> None:
    behaviors = [
        _behavior(1, BehaviorCategory.STATE_TRANSITION),
        _behavior(2, BehaviorCategory.DEPENDENCY_INTERACTION),
        _behavior(3, BehaviorCategory.UNKNOWN),
    ]
    found, _, _ = _join(behaviors)
    # By the category's value, `state_transition_change` comes after `dependency_...`.
    assert [len(item.evidence) for item in found] == [2, 1]


def test_an_unknown_finding_of_another_symbol_stays() -> None:
    behaviors = [
        _behavior(1, BehaviorCategory.SIDE_EFFECT),
        _behavior(2, BehaviorCategory.UNKNOWN, "load"),
    ]
    found, _, _ = _join(behaviors)
    assert [item.category for item in found] == [
        BehaviorCategory.SIDE_EFFECT,
        BehaviorCategory.UNKNOWN,
    ]


def test_an_unknown_finding_beside_a_refactor_alone_stays() -> None:
    behaviors = [_behavior(1, BehaviorCategory.REFACTOR), _behavior(2, BehaviorCategory.UNKNOWN)]
    found, _, _ = _join(behaviors)
    assert [item.category for item in found] == [
        BehaviorCategory.REFACTOR,
        BehaviorCategory.UNKNOWN,
    ]


def test_a_review_question_stays() -> None:
    behaviors = [
        _behavior(1, BehaviorCategory.SIDE_EFFECT),
        _behavior(2, BehaviorCategory.UNKNOWN, presentation=Presentation.REVIEW_QUESTION),
    ]
    found, _, _ = _join(behaviors)
    assert [item.presentation for item in found] == [
        Presentation.FINDING,
        Presentation.REVIEW_QUESTION,
    ]


def test_a_named_review_question_takes_an_unknown_finding() -> None:
    behaviors = [
        _behavior(1, BehaviorCategory.AUTHORIZATION, presentation=Presentation.REVIEW_QUESTION),
        _behavior(2, BehaviorCategory.UNKNOWN),
    ]
    (finding,), _, _ = _join(behaviors)
    assert finding.presentation is Presentation.REVIEW_QUESTION
    assert len(finding.evidence) == 2


def test_nothing_to_join_returns_the_lists_as_they_were() -> None:
    behaviors = [_behavior(1, BehaviorCategory.SIDE_EFFECT)]
    obligations = [_obligation(1, ["bc-001"], 60, "Assert it")]
    items = [_item(behaviors[0])]
    found = _join_unknown_to_named(behaviors, obligations, items)
    assert found[0] is behaviors and found[1] is obligations and found[2] is items


def test_obligations_point_to_the_named_finding_once_and_are_numbered_again() -> None:
    behaviors = [
        _behavior(1, BehaviorCategory.UNKNOWN, "load"),
        _behavior(2, BehaviorCategory.SIDE_EFFECT),
        _behavior(3, BehaviorCategory.UNKNOWN),
    ]
    obligations = [
        _obligation(1, ["bc-002", "bc-003"], 70, "Shared"),
        _obligation(2, ["bc-001"], 60, "Load"),
        _obligation(3, ["bc-003"], 60, "Condition"),
    ]
    found, relinked, items = _join(behaviors, obligations)
    assert [(item.id, item.category) for item in found] == [
        ("bc-001", BehaviorCategory.UNKNOWN),
        ("bc-002", BehaviorCategory.SIDE_EFFECT),
    ]
    assert len(items) == 2
    # Ordered by priority, then the first finding linked, then the title.
    assert [(item.id, item.behavior_change_ids, item.title) for item in relinked] == [
        ("to-001", ["bc-002"], "Shared"),
        ("to-002", ["bc-001"], "Load"),
        ("to-003", ["bc-002"], "Condition"),
    ]
    # The input obligations are left as they were.
    assert [item.id for item in obligations] == ["to-001", "to-002", "to-003"]
    assert obligations[0].behavior_change_ids == ["bc-002", "bc-003"]


def test_the_variables_the_joined_finding_writes_reach_the_change_groups() -> None:
    named = _behavior(1, BehaviorCategory.DEPENDENCY_INTERACTION, "<module>")
    unknown = _behavior(2, BehaviorCategory.UNKNOWN, "<module>")
    _, _, (item,) = _join(
        [named, unknown],
        names={"bc-001": frozenset({"ROOTS"}), "bc-002": frozenset({"LIMIT"})},
    )
    assert item.candidate.assigned_names == frozenset({"ROOTS", "LIMIT"})
