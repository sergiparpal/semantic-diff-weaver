from __future__ import annotations

import pytest

from semantic_diff_weaver.models import (
    BehaviorCategory,
    BehaviorChange,
    CandidateTest,
    Evidence,
    ObligationType,
    Origin,
    Presentation,
    RiskLabel,
    ScoreExplanation,
    WeaverConfig,
)
from semantic_diff_weaver.obligations import generate_obligations
from semantic_diff_weaver.semantic_interpreter import SuggestedScenario


def behavior(category: BehaviorCategory, index: int = 1, risk: RiskLabel = RiskLabel.HIGH):
    return BehaviorChange(
        id=f"bc-{index:03d}",
        category=category,
        summary="Changed behavior.",
        observable_impact="An outcome appears to differ.",
        risk=risk,
        risk_score=70 if risk is RiskLabel.HIGH else 20,
        confidence=0.8,
        evidence=[Evidence(id=f"ev-{index:03d}", path="src/a.py", kind="comparison_change")],
        assumptions=[],
        presentation=Presentation.FINDING,
        origin=Origin.DETERMINISTIC,
        score_explanation=ScoreExplanation(
            behavioral_impact=70,
            critical_path_weight=10,
            test_gap_weight=90,
            change_surface_weight=30,
        ),
    )


def test_boundary_generates_below_at_above_obligations() -> None:
    obligations, omitted = generate_obligations(
        [behavior(BehaviorCategory.BOUNDARY)], {}, False, WeaverConfig()
    )
    assert omitted == 0
    assert len(obligations) == 3
    assert any("below" in item.title for item in obligations)
    assert any("exact" in item.title for item in obligations)
    assert any("above" in item.title for item in obligations)
    assert all(item.behavior_change_ids == ["bc-001"] for item in obligations)


def test_high_behavior_always_has_obligation_and_cap_is_visible() -> None:
    config = WeaverConfig()
    config.rules.max_test_obligations = 2
    obligations, omitted = generate_obligations(
        [behavior(BehaviorCategory.RETRY_TIMEOUT)], {}, False, config
    )
    assert obligations
    assert len(obligations) == 2
    assert omitted == 1


def test_mapping_incomplete_is_not_claimed_as_coverage() -> None:
    obligations, _ = generate_obligations(
        [behavior(BehaviorCategory.ERROR_HANDLING)], {}, True, WeaverConfig()
    )
    assert all(item.coverage_status.value == "mapping_incomplete" for item in obligations)


def test_equivalent_templates_do_not_remove_another_high_risk_behaviors_obligation() -> None:
    obligations, _ = generate_obligations(
        [
            behavior(BehaviorCategory.BOUNDARY, index=1),
            behavior(BehaviorCategory.BOUNDARY, index=2),
        ],
        {},
        False,
        WeaverConfig(),
    )
    linked = {
        behavior_id for obligation in obligations for behavior_id in obligation.behavior_change_ids
    }
    assert {"bc-001", "bc-002"} <= linked
    assert len(obligations) == 3
    assert all(item.behavior_change_ids == ["bc-001", "bc-002"] for item in obligations)


def test_merged_obligations_union_and_cap_candidate_tests() -> None:
    candidate_tests = {
        f"bc-{behavior_index:03d}": [
            CandidateTest(
                path=f"tests/test_{behavior_index}_{test_index}.py",
                symbol=f"test_case_{test_index}",
                match_score=0.5 + test_index / 100,
                match_reasons=["direct module or symbol import"],
            )
            for test_index in range(4)
        ]
        for behavior_index in (1, 2)
    }
    obligations, _ = generate_obligations(
        [
            behavior(BehaviorCategory.BOUNDARY, index=1),
            behavior(BehaviorCategory.BOUNDARY, index=2),
        ],
        candidate_tests,
        False,
        WeaverConfig(),
    )
    assert all(len(item.candidate_existing_tests) == 5 for item in obligations)
    assert all(item.coverage_status.value == "candidate_exists_unverified" for item in obligations)


def test_duplicate_llm_obligations_are_semantically_deduplicated() -> None:
    suggestion = SuggestedScenario(
        evidence_ids=("ev-001",),
        type=ObligationType.BOUNDARY,
        title="Model boundary scenario",
        given="A specially selected boundary input",
        when="The boundary operation runs",
        then="The intended boundary outcome is visible",
    )
    obligations, _ = generate_obligations(
        [behavior(BehaviorCategory.BOUNDARY)],
        {},
        False,
        WeaverConfig(),
        [suggestion, suggestion],
    )
    assert len(obligations) == 4
    model_obligation = next(item for item in obligations if item.title == "Model boundary scenario")
    assert model_obligation.origin is Origin.LLM_SUPPORTED


def test_global_cap_groups_overflowing_high_risk_behavior_links() -> None:
    config = WeaverConfig()
    config.rules.max_test_obligations = 2
    obligations, omitted = generate_obligations(
        [behavior(BehaviorCategory.BOUNDARY, index=index) for index in range(1, 4)],
        {},
        False,
        config,
    )
    linked = {
        behavior_id for obligation in obligations for behavior_id in obligation.behavior_change_ids
    }
    assert len(obligations) == 2
    assert omitted == 1
    assert linked == {"bc-001", "bc-002", "bc-003"}


def test_grouped_overflow_obligation_preserves_candidate_mapping() -> None:
    config = WeaverConfig()
    config.rules.max_test_obligations = 2
    mapped = CandidateTest(
        path="tests/test_a.py",
        symbol="test_boundary",
        match_score=0.8,
        match_reasons=["direct module or symbol import"],
    )
    behaviors = [
        behavior(BehaviorCategory.BOUNDARY, index=1),
        behavior(BehaviorCategory.AUTHORIZATION, index=2),
        behavior(BehaviorCategory.RETRY_TIMEOUT, index=3),
    ]
    for item in behaviors:
        item.origin = Origin.LLM_SUPPORTED
    obligations, _ = generate_obligations(
        behaviors,
        {"bc-003": [mapped]},
        True,
        config,
    )
    grouped = next(item for item in obligations if len(item.behavior_change_ids) > 1)
    assert grouped.candidate_existing_tests == [mapped]
    assert grouped.coverage_status.value == "candidate_exists_unverified"
    assert grouped.origin is Origin.LLM_SUPPORTED


@pytest.mark.parametrize("origin", [Origin.DETERMINISTIC, Origin.DETERMINISTIC_FALLBACK])
def test_grouped_overflow_obligation_uses_conservative_origin(origin: Origin) -> None:
    config = WeaverConfig()
    config.rules.max_test_obligations = 2
    behaviors = [
        behavior(BehaviorCategory.BOUNDARY, index=1),
        behavior(BehaviorCategory.AUTHORIZATION, index=2),
        behavior(BehaviorCategory.RETRY_TIMEOUT, index=3),
    ]
    for item in behaviors:
        item.origin = origin
    obligations, _ = generate_obligations(behaviors, {}, False, config)
    grouped = next(item for item in obligations if len(item.behavior_change_ids) > 1)
    assert grouped.origin is origin
