"""Deterministic test-obligation templates, priority, deduplication, and caps."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .models import (
    BehaviorCategory,
    BehaviorChange,
    CandidateTest,
    CoverageStatus,
    ObligationType,
    Origin,
    Presentation,
    TestObligation,
    WeaverConfig,
)
from .scoring import obligation_priority
from .textutil import canonical_phrase

if TYPE_CHECKING:
    from .semantic_interpreter import SuggestedScenario

TEST_GAP_WITH_CANDIDATES = 60
TEST_GAP_WITHOUT_CANDIDATES = 90
LLM_SCENARIO_RELEVANCE = 85
REVIEW_SCENARIO_RELEVANCE = 80


@dataclass(frozen=True)
class Scenario:
    type: ObligationType
    title: str
    given: str
    when: str
    then: str
    relevance: int = 95
    origin: Origin | None = None


TEMPLATES: dict[BehaviorCategory, tuple[Scenario, ...]] = {
    BehaviorCategory.BOUNDARY: (
        Scenario(
            ObligationType.BOUNDARY,
            "Exercise just below the changed boundary",
            "An input immediately below the changed boundary",
            "The changed operation is invoked",
            "The below-boundary outcome remains explicitly asserted",
        ),
        Scenario(
            ObligationType.BOUNDARY,
            "Exercise the exact changed boundary",
            "An input exactly at the changed boundary",
            "The changed operation is invoked",
            "The newly inferred inclusive or exclusive outcome is observed",
        ),
        Scenario(
            ObligationType.BOUNDARY,
            "Exercise just above the changed boundary",
            "An input immediately above the changed boundary",
            "The changed operation is invoked",
            "The above-boundary outcome remains explicitly asserted",
        ),
    ),
    BehaviorCategory.DEFAULT_BEHAVIOR: (
        Scenario(
            ObligationType.REGRESSION,
            "Verify omitted-input behavior",
            "The affected argument is omitted",
            "The callable is invoked",
            "The new default-driven observable result is asserted",
        ),
        Scenario(
            ObligationType.POSITIVE,
            "Verify the old value explicitly",
            "The former default value is supplied explicitly",
            "The callable is invoked",
            "Its explicit-value behavior is asserted independently of the default",
        ),
        Scenario(
            ObligationType.POSITIVE,
            "Verify the new value explicitly",
            "The new default value is supplied explicitly",
            "The callable is invoked",
            "Its explicit-value behavior matches the intended new contract",
        ),
    ),
    BehaviorCategory.VALIDATION: (
        Scenario(
            ObligationType.POSITIVE,
            "Verify newly accepted input",
            "An input at the changed validation condition",
            "Validation runs",
            "The intended newly valid case is accepted",
        ),
        Scenario(
            ObligationType.NEGATIVE,
            "Verify newly rejected input",
            "An input outside the changed validation condition",
            "Validation runs",
            "The intended invalid case is rejected observably",
        ),
    ),
    BehaviorCategory.ERROR_HANDLING: (
        Scenario(
            ObligationType.ERROR,
            "Exercise the changed failure trigger",
            "The dependency or operation reaches the changed failure path",
            "The affected symbol runs",
            "The visible error type or fallback matches the intended contract",
        ),
        Scenario(
            ObligationType.ERROR,
            "Verify recovery or propagation",
            "The changed exception is raised",
            "The surrounding handler executes",
            "The error is propagated, wrapped, or recovered exactly as intended",
        ),
    ),
    BehaviorCategory.STATE_TRANSITION: (
        Scenario(
            ObligationType.STATE,
            "Verify the allowed transition",
            "An entity is in a state that should permit the transition",
            "The changed operation runs",
            "The expected next state is observable",
        ),
        Scenario(
            ObligationType.NEGATIVE,
            "Reject an invalid transition",
            "An entity is in a state that should not permit the transition",
            "The changed operation runs",
            "State remains valid and rejection is observable",
        ),
        Scenario(
            ObligationType.STATE,
            "Repeat the transition",
            "The transition has already occurred",
            "The operation is requested again",
            "Repeated execution has the intended stable outcome",
        ),
    ),
    BehaviorCategory.AUTHORIZATION: (
        Scenario(
            ObligationType.POSITIVE,
            "Verify an allowed principal",
            "A principal satisfies the inferred authorization guard",
            "The changed operation is attempted",
            "The authorized outcome remains available",
        ),
        Scenario(
            ObligationType.NEGATIVE,
            "Verify a denied principal",
            "A principal does not satisfy the inferred authorization guard",
            "The changed operation is attempted",
            "Access is denied without the protected side effect",
        ),
    ),
    BehaviorCategory.RETRY_TIMEOUT: (
        Scenario(
            ObligationType.INTERACTION,
            "Recover before the exact retry limit",
            "A dependency fails recoverably and then succeeds before the limit",
            "The changed retry policy runs",
            "The operation succeeds with the expected attempt count",
        ),
        Scenario(
            ObligationType.ERROR,
            "Stop on a terminal failure",
            "A dependency returns a terminal failure",
            "The changed retry policy evaluates it",
            "No unsupported additional attempt is made",
        ),
        Scenario(
            ObligationType.BOUNDARY,
            "Exercise the exact retry or timeout limit",
            "Failures continue through the configured boundary",
            "The changed policy reaches the exact limit",
            "Termination and the visible error occur at the intended point",
        ),
    ),
    BehaviorCategory.OUTPUT_CONTRACT: (
        Scenario(
            ObligationType.REGRESSION,
            "Assert the consumer-visible contract",
            "A representative successful input",
            "The changed callable returns",
            "The value, status, fields, and container shape match the intended contract",
        ),
        Scenario(
            ObligationType.NEGATIVE,
            "Check former contract behavior",
            "A consumer expects the former signature or output",
            "It uses the changed callable",
            "Compatibility or the intended failure is explicit",
        ),
    ),
    BehaviorCategory.SIDE_EFFECT: (
        Scenario(
            ObligationType.INTERACTION,
            "Verify side-effect occurrence",
            "The conditions for the inferred side effect are met",
            "The changed operation runs",
            "The external write, event, or notification occurs exactly as intended",
        ),
        Scenario(
            ObligationType.NEGATIVE,
            "Verify side-effect absence",
            "The conditions for the inferred side effect are not met",
            "The changed operation runs",
            "No external side effect occurs",
        ),
        Scenario(
            ObligationType.INTERACTION,
            "Verify ordering and idempotency",
            "The operation may be repeated or partially fail",
            "It is invoked more than once",
            "Side-effect order and duplication match the intended contract",
        ),
    ),
    BehaviorCategory.ORDERING: (
        Scenario(
            ObligationType.REGRESSION,
            "Exercise competing conditions",
            "More than one changed condition can apply",
            "The changed sequence runs",
            "The intended precedence determines the observable outcome",
        ),
        Scenario(
            ObligationType.INTERACTION,
            "Verify sequence-sensitive interactions",
            "Multiple affected calls are observable",
            "The operation executes",
            "The calls occur in the intended order",
        ),
    ),
    BehaviorCategory.DEPENDENCY_INTERACTION: (
        Scenario(
            ObligationType.INTERACTION,
            "Handle dependency success",
            "The dependency returns a successful response",
            "The changed interaction runs",
            "The success response is consumed as intended",
        ),
        Scenario(
            ObligationType.ERROR,
            "Handle dependency failure",
            "The dependency returns or raises a known failure",
            "The changed interaction runs",
            "The visible failure or fallback is asserted",
        ),
        Scenario(
            ObligationType.INTERACTION,
            "Handle an unexpected dependency response",
            "The dependency returns an unexpected but representable response",
            "The changed interaction runs",
            "The operation fails or degrades safely",
        ),
    ),
    BehaviorCategory.REFACTOR: (
        Scenario(
            ObligationType.REGRESSION,
            "Preserve characterized behavior",
            "Representative existing inputs for the refactored symbol",
            "The refactored path runs",
            "Previously observable outcomes remain unchanged",
            75,
        ),
    ),
    BehaviorCategory.UNKNOWN: (
        Scenario(
            ObligationType.REVIEW,
            "Clarify and characterize the changed contract",
            "The missing runtime or business contract is identified",
            "The changed cases are reviewed",
            "Expected observable outcomes are documented and tested",
            70,
        ),
    ),
}


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


def _coverage_status(candidates: list[CandidateTest], mapping_incomplete: bool) -> CoverageStatus:
    if candidates:
        return CoverageStatus.CANDIDATE_UNVERIFIED
    if mapping_incomplete:
        return CoverageStatus.INCOMPLETE
    return CoverageStatus.NONE_FOUND


def generate_obligations(
    behaviors: list[BehaviorChange],
    candidate_tests: dict[str, list[CandidateTest]],
    mapping_incomplete: bool,
    config: WeaverConfig,
    llm_suggestions: list[SuggestedScenario] | None = None,
) -> tuple[list[TestObligation], int]:
    generated: list[TestObligation] = []
    by_semantics: dict[tuple[str, str, str], TestObligation] = {}
    for behavior in behaviors:
        candidates = candidate_tests.get(behavior.id, [])
        coverage = _coverage_status(candidates, mapping_incomplete)
        gap = TEST_GAP_WITH_CANDIDATES if candidates else TEST_GAP_WITHOUT_CANDIDATES
        scenarios: tuple[Scenario, ...] = TEMPLATES[behavior.category]
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
        scenarios = (*scenarios, *supported_suggestions)[
            : config.rules.max_obligations_per_behavior
        ]
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
                *scenarios[: max(0, config.rules.max_obligations_per_behavior - 1)],
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
                )[: config.rules.max_candidate_tests_per_obligation]
                existing.coverage_status = _coverage_status(
                    existing.candidate_existing_tests, mapping_incomplete
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
    maximum = config.rules.max_test_obligations
    required_behaviors = [
        behavior for behavior in behaviors if behavior.risk.value in {"high", "critical"}
    ]
    required: list[TestObligation] = []
    for behavior in required_behaviors:
        obligation = next(item for item in ordered if behavior.id in item.behavior_change_ids)
        if obligation not in required:
            required.append(obligation)
    if len(required) > maximum:
        individually_kept = required[: max(0, maximum - 1)]
        overflow_ids = sorted(
            {
                behavior_id
                for obligation in required[max(0, maximum - 1) :]
                for behavior_id in obligation.behavior_change_ids
            }
        )
        overflow_behaviors = [
            behavior for behavior in required_behaviors if behavior.id in overflow_ids
        ]
        overflow_candidates: list[CandidateTest] = []
        for behavior_id in overflow_ids:
            overflow_candidates = _merge_candidate_tests(
                overflow_candidates, candidate_tests.get(behavior_id, [])
            )
        overflow_candidates = overflow_candidates[: config.rules.max_candidate_tests_per_obligation]
        overflow_origins = {item.origin for item in overflow_behaviors}
        if overflow_origins == {Origin.LLM_SUPPORTED}:
            overflow_origin = Origin.LLM_SUPPORTED
        elif Origin.DETERMINISTIC_FALLBACK in overflow_origins:
            overflow_origin = Origin.DETERMINISTIC_FALLBACK
        else:
            overflow_origin = Origin.DETERMINISTIC
        grouped = TestObligation(
            id="to-000",
            behavior_change_ids=overflow_ids,
            type=ObligationType.REVIEW,
            priority=max(item.risk_score for item in overflow_behaviors),
            title="Review remaining high-risk behavior changes",
            given="Multiple high-risk behavior changes exceed the individual obligation cap",
            when="The bounded review is planned",
            then="Each linked behavior receives an explicit regression scenario before release",
            candidate_existing_tests=overflow_candidates,
            coverage_status=_coverage_status(overflow_candidates, mapping_incomplete),
            origin=overflow_origin,
            confidence=min(item.confidence for item in overflow_behaviors),
        )
        selected = [*individually_kept, grouped]
    else:
        selected = list(required)
        selected.extend(item for item in ordered if item not in selected)
        selected = selected[:maximum]
    omitted = max(0, len(generated) - min(maximum, len(generated)))
    selected.sort(key=lambda item: (-item.priority, item.behavior_change_ids[0], item.title))
    for index, obligation in enumerate(selected, start=1):
        obligation.id = f"to-{index:03d}"
    return selected, omitted
