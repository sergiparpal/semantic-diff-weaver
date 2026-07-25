"""Deterministic test-obligation templates, priority, deduplication, and caps."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .models import (
    BehaviorChange,
    CandidateTest,
    CoverageStatus,
    ObligationType,
    Origin,
    Presentation,
    RiskLabel,
    RulesConfig,
    TestObligation,
)
from .scoring import obligation_priority
from .taxonomy import CATEGORY_PROFILES, Scenario
from .textutil import canonical_phrase

if TYPE_CHECKING:
    from .semantic_interpreter import SuggestedScenario

TEST_GAP_WITH_CANDIDATES = 60
TEST_GAP_WITHOUT_CANDIDATES = 90
LLM_SCENARIO_RELEVANCE = 85
REVIEW_SCENARIO_RELEVANCE = 80


def _merge_candidate_tests(
    current: list[CandidateTest], incoming: list[CandidateTest]
) -> list[CandidateTest]:
    merged: dict[tuple[str, str], CandidateTest] = {
        (item.path, item.symbol): item.model_copy(deep=True) for item in current
    }
    for item in incoming:
        key = (item.path, item.symbol)
        if key not in merged:
            merged[key] = item.model_copy(deep=True)
            continue
        existing = merged[key]
        existing.match_score = max(existing.match_score, item.match_score)
        existing.match_reasons = sorted(set([*existing.match_reasons, *item.match_reasons]))
    return sorted(merged.values(), key=lambda item: (-item.match_score, item.path, item.symbol))


def _coverage_status(
    candidates: list[CandidateTest],
    mapping_incomplete: bool,
    grounded: str | None = None,
) -> CoverageStatus:
    """Prefer a grounded verdict from an ingested report over static candidate matching.

    A grounded verdict is only produced when every changed line the behavior rests on agrees;
    a mixed or unmatched range falls through to the static semantics unchanged. Coverage says
    a line was executed, never that a candidate test asserts the changed behavior — which is
    why ``CandidateTest.verified`` stays ``False`` regardless of what is returned here.
    """
    if grounded == "uncovered":
        return CoverageStatus.UNCOVERED
    if grounded == "covered":
        return CoverageStatus.COVERED
    if candidates:
        return CoverageStatus.CANDIDATE_UNVERIFIED
    if mapping_incomplete:
        return CoverageStatus.INCOMPLETE
    return CoverageStatus.NONE_FOUND


def _merge_grounded(behavior_ids: list[str], states: dict[str, str] | None) -> str | None:
    """A grouped obligation keeps a grounded verdict only when every behavior agrees."""
    if not states:
        return None
    verdicts = {states.get(behavior_id) for behavior_id in behavior_ids}
    if len(verdicts) != 1:
        return None
    single = verdicts.pop()
    return single if single in {"covered", "uncovered"} else None


def _overflow_origin(behaviors: list[BehaviorChange]) -> Origin:
    """Report the weakest provenance among the behaviors a grouped obligation stands in for."""
    origins = {item.origin for item in behaviors}
    if origins == {Origin.LLM_SUPPORTED}:
        return Origin.LLM_SUPPORTED
    if Origin.DETERMINISTIC_FALLBACK in origins:
        return Origin.DETERMINISTIC_FALLBACK
    return Origin.DETERMINISTIC


def _group_overflow(
    overflow: list[TestObligation],
    required_behaviors: list[BehaviorChange],
    candidate_tests: dict[str, list[CandidateTest]],
    mapping_incomplete: bool,
    rules: RulesConfig,
) -> TestObligation:
    """Collapse high-risk obligations past the global cap into one linked review obligation.

    Every high or critical behavior must stay linked to some obligation — the canonical schema
    rejects the result otherwise — so the cap sheds individual scenarios, never the link.
    """
    behavior_ids = sorted(
        {behavior_id for item in overflow for behavior_id in item.behavior_change_ids}
    )
    behaviors = [item for item in required_behaviors if item.id in behavior_ids]
    candidates: list[CandidateTest] = []
    for behavior_id in behavior_ids:
        candidates = _merge_candidate_tests(candidates, candidate_tests.get(behavior_id, []))
    candidates = candidates[: rules.max_candidate_tests_per_obligation]
    return TestObligation(
        id="to-000",
        behavior_change_ids=behavior_ids,
        type=ObligationType.REVIEW,
        priority=max(item.risk_score for item in behaviors),
        title="Review remaining high-risk behavior changes",
        given="Multiple high-risk behavior changes exceed the individual obligation cap",
        when="The bounded review is planned",
        then="Each linked behavior receives an explicit regression scenario before release",
        candidate_existing_tests=candidates,
        coverage_status=_coverage_status(candidates, mapping_incomplete),
        origin=_overflow_origin(behaviors),
        confidence=min(item.confidence for item in behaviors),
    )


def generate_obligations(
    behaviors: list[BehaviorChange],
    candidate_tests: dict[str, list[CandidateTest]],
    mapping_incomplete: bool,
    rules: RulesConfig,
    llm_suggestions: list[SuggestedScenario] | None = None,
    coverage_states: dict[str, str] | None = None,
) -> tuple[list[TestObligation], int]:
    generated: list[TestObligation] = []
    by_semantics: dict[tuple[str, str, str], TestObligation] = {}
    for behavior in behaviors:
        candidates = candidate_tests.get(behavior.id, [])
        grounded = (coverage_states or {}).get(behavior.id)
        coverage = _coverage_status(candidates, mapping_incomplete, grounded)
        gap = TEST_GAP_WITH_CANDIDATES if candidates else TEST_GAP_WITHOUT_CANDIDATES
        scenarios: tuple[Scenario, ...] = CATEGORY_PROFILES[behavior.category].scenarios
        behavior_evidence_ids = {item.id for item in behavior.evidence}
        supported_suggestions = [
            Scenario(
                item.type,
                item.title,
                item.given,
                item.when,
                item.then,
                LLM_SCENARIO_RELEVANCE,
                Origin.LLM_SUPPORTED,
            )
            for item in (llm_suggestions or [])
            if set(item.evidence_ids) <= behavior_evidence_ids
        ]
        scenarios = (*scenarios, *supported_suggestions)[: rules.max_obligations_per_behavior]
        if behavior.presentation is Presentation.REVIEW_QUESTION:
            scenarios = (
                Scenario(
                    ObligationType.REVIEW,
                    "Resolve the high-risk review question",
                    "The missing assumptions and external contract are available",
                    "The inferred behavior change is reviewed",
                    "The intended observable outcome is confirmed and captured by a regression test",
                    REVIEW_SCENARIO_RELEVANCE,
                ),
                *scenarios[: max(0, rules.max_obligations_per_behavior - 1)],
            )
        for scenario in scenarios:
            key = (
                canonical_phrase(scenario.given),
                canonical_phrase(scenario.when),
                canonical_phrase(scenario.then),
            )
            priority = obligation_priority(
                behavior.risk_score, scenario.relevance, gap, behavior.confidence
            )
            scenario_origin = scenario.origin or behavior.origin
            if key in by_semantics:
                existing = by_semantics[key]
                existing.behavior_change_ids = sorted(
                    set([*existing.behavior_change_ids, behavior.id])
                )
                existing.priority = max(existing.priority, priority)
                existing.candidate_existing_tests = _merge_candidate_tests(
                    existing.candidate_existing_tests, candidates
                )[: rules.max_candidate_tests_per_obligation]
                existing.coverage_status = _coverage_status(
                    existing.candidate_existing_tests,
                    mapping_incomplete,
                    _merge_grounded(existing.behavior_change_ids, coverage_states),
                )
                if behavior.confidence > existing.confidence:
                    existing.type = scenario.type
                    existing.title = scenario.title
                    existing.given = scenario.given
                    existing.when = scenario.when
                    existing.then = scenario.then
                    existing.origin = scenario_origin
                existing.confidence = max(existing.confidence, behavior.confidence)
                continue
            obligation = TestObligation(
                id=f"to-{len(generated) + 1:03d}",
                behavior_change_ids=[behavior.id],
                type=scenario.type,
                priority=priority,
                title=scenario.title,
                given=scenario.given,
                when=scenario.when,
                then=scenario.then,
                candidate_existing_tests=candidates,
                coverage_status=coverage,
                origin=scenario_origin,
                confidence=behavior.confidence,
            )
            generated.append(obligation)
            by_semantics[key] = obligation
    ordered = sorted(
        generated, key=lambda item: (-item.priority, item.behavior_change_ids[0], item.title)
    )
    maximum = rules.max_test_obligations
    required_behaviors = [
        behavior for behavior in behaviors if behavior.risk in {RiskLabel.HIGH, RiskLabel.CRITICAL}
    ]
    required: list[TestObligation] = []
    required_ids: set[int] = set()
    for behavior in required_behaviors:
        # Every behavior emits at least one scenario, so a match is expected — but a bare
        # `next` would raise `StopIteration` rather than a `WeaverError` if that ever stopped
        # holding, and the tool boundary can only report that as an opaque internal error.
        linked = next((item for item in ordered if behavior.id in item.behavior_change_ids), None)
        if linked is None:
            continue
        if id(linked) not in required_ids:
            required_ids.add(id(linked))
            required.append(linked)
    if len(required) > maximum:
        selected = [
            *required[: max(0, maximum - 1)],
            _group_overflow(
                required[max(0, maximum - 1) :],
                required_behaviors,
                candidate_tests,
                mapping_incomplete,
                rules,
            ),
        ]
    else:
        # Identity, not value equality: obligations are Pydantic models whose comparison is
        # field-wise, and reading a list while extend appends to it is too subtle to rely on.
        selected = list(required)
        taken = {id(item) for item in selected}
        selected.extend(item for item in ordered if id(item) not in taken)
        selected = selected[:maximum]
    # Count what was generated and then dropped. The grouped overflow obligation is synthetic,
    # so it is deliberately not credited as having emitted the behaviors it merely links.
    emitted_ids = {item.id for item in selected}
    omitted = sum(1 for item in generated if item.id not in emitted_ids)
    selected.sort(key=lambda item: (-item.priority, item.behavior_change_ids[0], item.title))
    for index, obligation in enumerate(selected, start=1):
        obligation.id = f"to-{index:03d}"
    return selected, omitted
