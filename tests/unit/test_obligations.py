from __future__ import annotations

import pytest

from semantic_diff_weaver.models import (
    BehaviorCategory,
    BehaviorChange,
    CandidateTest,
    CoverageStatus,
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
        [behavior(BehaviorCategory.BOUNDARY)], {}, False, WeaverConfig().rules
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
        [behavior(BehaviorCategory.RETRY_TIMEOUT)], {}, False, config.rules
    )
    assert obligations
    assert len(obligations) == 2
    assert omitted == 1


def test_mapping_incomplete_is_not_claimed_as_coverage() -> None:
    obligations, _ = generate_obligations(
        [behavior(BehaviorCategory.ERROR_HANDLING)], {}, True, WeaverConfig().rules
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
        WeaverConfig().rules,
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
        WeaverConfig().rules,
    )
    assert all(len(item.candidate_existing_tests) == 5 for item in obligations)
    assert all(item.coverage_status.value == "candidate_exists_unverified" for item in obligations)


def test_a_behavior_without_tests_never_shares_an_obligation_with_one_that_has_them() -> None:
    """The templates name no symbol, so equivalent scenarios used to merge across behaviors.

    The untested behavior was then listed under the other's candidate tests, with a
    `candidate_exists_unverified` status that was false for it.
    """
    tested = CandidateTest(
        path="tests/test_pricing.py",
        symbol="test_discount_above_threshold",
        match_score=0.8,
        match_reasons=["mirrored source/test path"],
    )
    obligations, _ = generate_obligations(
        [
            behavior(BehaviorCategory.BOUNDARY, index=1),
            behavior(BehaviorCategory.BOUNDARY, index=2),
        ],
        {"bc-001": [tested]},
        False,
        WeaverConfig().rules,
    )
    assert len(obligations) == 6
    for obligation in obligations:
        if obligation.behavior_change_ids == ["bc-001"]:
            assert obligation.candidate_existing_tests == [tested]
            assert obligation.coverage_status is CoverageStatus.CANDIDATE_UNVERIFIED
        else:
            assert obligation.behavior_change_ids == ["bc-002"]
            assert obligation.candidate_existing_tests == []
            assert obligation.coverage_status is CoverageStatus.NONE_FOUND


def test_behaviors_share_an_obligation_only_when_their_coverage_status_agrees() -> None:
    obligations, _ = generate_obligations(
        [behavior(BehaviorCategory.BOUNDARY, index=index) for index in range(1, 5)],
        {},
        False,
        WeaverConfig().rules,
        coverage_states={"bc-001": "covered", "bc-002": "covered", "bc-003": "uncovered"},
    )
    statuses = {(tuple(item.behavior_change_ids), item.coverage_status) for item in obligations}
    assert statuses == {
        (("bc-001", "bc-002"), CoverageStatus.COVERED),
        (("bc-003",), CoverageStatus.UNCOVERED),
        (("bc-004",), CoverageStatus.NONE_FOUND),
    }
    assert len(obligations) == 9


@pytest.mark.parametrize(
    ("states", "with_candidate", "mapping_incomplete", "expected"),
    [
        ({"bc-002": "covered", "bc-003": "covered"}, False, False, CoverageStatus.COVERED),
        ({"bc-002": "covered"}, True, False, CoverageStatus.CANDIDATE_UNVERIFIED),
        ({"bc-002": "covered"}, False, True, CoverageStatus.INCOMPLETE),
        ({"bc-002": "covered"}, False, False, CoverageStatus.NONE_FOUND),
        ({"bc-002": "uncovered"}, False, False, CoverageStatus.UNCOVERED),
    ],
)
def test_a_grouped_overflow_obligation_reports_its_least_tested_behavior(
    states: dict[str, str],
    with_candidate: bool,
    mapping_incomplete: bool,
    expected: CoverageStatus,
) -> None:
    config = WeaverConfig()
    config.rules.max_test_obligations = 2
    candidate = CandidateTest(
        path="tests/test_a.py",
        symbol="test_retry",
        match_score=0.8,
        match_reasons=["direct module or symbol import"],
    )
    obligations, _ = generate_obligations(
        [
            behavior(BehaviorCategory.BOUNDARY, index=1),
            behavior(BehaviorCategory.AUTHORIZATION, index=2),
            behavior(BehaviorCategory.RETRY_TIMEOUT, index=3),
        ],
        {"bc-003": [candidate]} if with_candidate else {},
        mapping_incomplete,
        config.rules,
        coverage_states=states,
    )
    grouped = next(item for item in obligations if len(item.behavior_change_ids) > 1)
    assert grouped.behavior_change_ids == ["bc-002", "bc-003"]
    assert grouped.coverage_status is expected


@pytest.mark.parametrize("strong_first", [True, False])
def test_a_pooled_candidate_keeps_the_reasons_of_its_best_match(strong_first: bool) -> None:
    """Two behaviors matching one test for different reasons used to union them.

    Beside the best score, that union no longer added up: 0.60 with reasons worth 0.80.
    """
    weak = CandidateTest(
        path="tests/test_cli.py",
        symbol="test_options_become_run_args",
        match_score=0.5,
        match_reasons=["mirrored source/test path", "direct module or symbol import"],
    )
    strong = CandidateTest(
        path="tests/test_cli.py",
        symbol="test_options_become_run_args",
        match_score=0.55,
        match_reasons=[
            "direct module or symbol import",
            "changed symbol token in test name",
            "changed symbol token in bounded test body",
        ],
    )
    first, second = (strong, weak) if strong_first else (weak, strong)
    obligations, _ = generate_obligations(
        [
            behavior(BehaviorCategory.BOUNDARY, index=1),
            behavior(BehaviorCategory.BOUNDARY, index=2),
        ],
        {"bc-001": [first], "bc-002": [second]},
        False,
        WeaverConfig().rules,
    )
    assert all(item.candidate_existing_tests == [strong] for item in obligations)


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
        WeaverConfig().rules,
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
        config.rules,
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
        config.rules,
    )
    grouped = next(item for item in obligations if len(item.behavior_change_ids) > 1)
    assert grouped.candidate_existing_tests == [mapped]
    # The candidate is bc-003's alone. bc-002 has none in an incomplete mapping, and a group
    # reports its least-tested behavior's status.
    assert grouped.coverage_status.value == "mapping_incomplete"
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
    obligations, _ = generate_obligations(behaviors, {}, False, config.rules)
    grouped = next(item for item in obligations if len(item.behavior_change_ids) > 1)
    assert grouped.origin is origin


def test_grouped_overflow_counts_the_absorbed_obligation_as_omitted() -> None:
    """The synthetic grouped obligation replaces a generated one, so it is not credited.

    The cap keeps ``maximum - 1`` real obligations plus one grouped review, so exactly
    ``len(generated) - (maximum - 1)`` generated obligations were dropped. The previous
    formula subtracted ``maximum`` and therefore under-reported the omission by one.
    """
    config = WeaverConfig()
    config.rules.max_test_obligations = 2
    behaviors = [
        behavior(BehaviorCategory.BOUNDARY, index=1),
        behavior(BehaviorCategory.AUTHORIZATION, index=2),
        behavior(BehaviorCategory.RETRY_TIMEOUT, index=3),
    ]
    obligations, omitted = generate_obligations(behaviors, {}, False, config.rules)
    grouped = [
        item for item in obligations if item.title == "Review remaining high-risk behavior changes"
    ]
    assert len(grouped) == 1
    assert len(obligations) == 2
    # Eight scenarios are generated; one real obligation survives beside the grouped review.
    assert omitted == 7
    linked = {
        behavior_id for obligation in obligations for behavior_id in obligation.behavior_change_ids
    }
    assert linked == {"bc-001", "bc-002", "bc-003"}
