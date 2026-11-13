"""Deterministic test obligations: their scenarios, priority, deduplication, and caps."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from .evidence_scenarios import (
    LIFECYCLE_KINDS,
    NO_CONTEXT,
    Change,
    Context,
    evidence_scenarios,
    primary_kind,
    weakly_anchored,
)
from .models import (
    BehaviorCategory,
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
    """Pool candidate tests, keeping for each its strongest match and the reasons for it.

    Two behaviors can match one test for different reasons. Their union shown beside the best
    score no longer added up to it: a test at 0.60 listed four reasons worth 0.80.

    Among equal scores the test files take turns. Ordered by path, every place of an obligation
    that a new package's findings shared with a changed `CODE_ROOTS` tuple went to
    `test_escalations.py`, and none of the tests reading the tuple showed.
    """
    merged: dict[tuple[str, str], CandidateTest] = {
        (item.path, item.symbol): item.model_copy(deep=True) for item in current
    }
    for item in incoming:
        key = (item.path, item.symbol)
        if key not in merged or item.match_score > merged[key].match_score:
            merged[key] = item.model_copy(deep=True)
    turns: Counter[tuple[float, str]] = Counter()
    ranked: list[tuple[tuple[float, int, str, str], CandidateTest]] = []
    for item in sorted(
        merged.values(), key=lambda item: (-item.match_score, item.path, item.symbol)
    ):
        turn = turns[(item.match_score, item.path)]
        turns[(item.match_score, item.path)] += 1
        ranked.append(((-item.match_score, turn, item.path, item.symbol), item))
    return [item for _, item in sorted(ranked, key=lambda pair: pair[0])]


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


# From the least test evidence to the most.
_TEST_EVIDENCE_ORDER = (
    CoverageStatus.UNCOVERED,
    CoverageStatus.NONE_FOUND,
    CoverageStatus.INCOMPLETE,
    CoverageStatus.CANDIDATE_UNVERIFIED,
    CoverageStatus.COVERED,
)


def _least_tested(statuses: Iterable[CoverageStatus]) -> CoverageStatus:
    """The coverage status of the least-tested behavior a grouped obligation stands in for.

    Like its origin and confidence, the grouped obligation reports its weakest link, so it never
    claims a candidate test or an executed line that one of its behaviors lacks.
    """
    return min(statuses, key=_TEST_EVIDENCE_ORDER.index)


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
    coverage_by_behavior: dict[str, CoverageStatus],
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
        coverage_status=_least_tested(coverage_by_behavior[item] for item in behavior_ids),
        origin=_overflow_origin(behaviors),
        confidence=min(item.confidence for item in behaviors),
    )


def _symbol(behavior: BehaviorChange) -> tuple[str, str]:
    first = behavior.evidence[0]
    return first.path, first.symbol or "<module>"


def _folded(
    behaviors: list[BehaviorChange],
    quoting: Mapping[str, tuple[Scenario, ...]],
    weak: set[str],
    kinds: Mapping[str, str | None],
    contexts: Mapping[str, Context],
    coverage_by_behavior: Mapping[str, CoverageStatus],
) -> dict[str, str]:
    """Each weakly anchored behavior another speaks for, to that other one.

    A behavior whose scenario quotes a call that only computes a value, an assignment, or a
    private symbol added or removed says where to look but not what to assert. The first behavior
    of its symbol whose scenario does say it stands for both; else one of a related symbol, the
    context's: the code calling an added private symbol, the code that stopped calling a removed
    one, the other symbols of its class. Without one, an assignment joins the symbol's call, whose
    scenario asks at least for the symbol's outcome, and a private symbol any behavior of a related
    symbol. A function that changed only as the functions nested in it did joins the behavior of
    its category on the first of them. Only behaviors whose coverage status agrees are linked, so
    the obligation's status and candidates stay true of each, and a review question keeps its own.
    An anchor that is linked in turn hands the link on; behaviors that would link in a circle keep
    their own.
    """
    strong: dict[tuple[str, str], str] = {}
    calls: dict[tuple[str, str], str] = {}
    first: dict[tuple[str, str], str] = {}
    # The first behavior of each symbol in each category.
    alike: dict[tuple[tuple[str, str], BehaviorCategory], str] = {}
    for behavior in behaviors:
        if not quoting[behavior.id] or behavior.presentation is Presentation.REVIEW_QUESTION:
            continue
        symbol = _symbol(behavior)
        first.setdefault(symbol, behavior.id)
        alike.setdefault((symbol, behavior.category), behavior.id)
        if behavior.id not in weak:
            strong.setdefault(symbol, behavior.id)
        elif kinds[behavior.id] == "call_change":
            calls.setdefault(symbol, behavior.id)
    folded: dict[str, str] = {}
    for behavior in behaviors:
        if behavior.id not in weak or behavior.presentation is Presentation.REVIEW_QUESTION:
            continue
        symbol = _symbol(behavior)
        context = contexts.get(behavior.id, NO_CONTEXT)
        related = context.related
        candidates = [
            *(alike.get(((symbol[0], name), behavior.category)) for name in context.nested),
            strong.get(symbol),
            *(strong.get(node) for node in related),
        ]
        if kinds[behavior.id] == "assignment_change":
            candidates.append(calls.get(symbol))
        if kinds[behavior.id] in LIFECYCLE_KINDS:
            candidates.extend(first.get(node) for node in related)
        anchor = next(
            (
                item
                for item in candidates
                if item is not None
                and item != behavior.id
                and coverage_by_behavior[item] is coverage_by_behavior[behavior.id]
            ),
            None,
        )
        if anchor is not None:
            folded[behavior.id] = anchor
    resolved: dict[str, str] = {}
    for behavior_id, anchor in folded.items():
        seen = {behavior_id}
        while anchor in folded and anchor not in seen:
            seen.add(anchor)
            anchor = folded[anchor]
        if anchor not in folded:
            resolved[behavior_id] = anchor
    return resolved


def generate_obligations(
    behaviors: list[BehaviorChange],
    candidate_tests: dict[str, list[CandidateTest]],
    mapping_incomplete: bool,
    rules: RulesConfig,
    llm_suggestions: list[SuggestedScenario] | None = None,
    coverage_states: dict[str, str] | None = None,
    changes: Mapping[str, Change] | None = None,
    contexts: Mapping[str, Context] | None = None,
) -> tuple[list[TestObligation], int]:
    """Each behavior's scenarios, deduplicated, prioritized, and capped.

    A behavior's scenarios quote what its evidence changed, by `changes`, when a Python delta
    describes it; any other takes its category's scenarios. `contexts` says, by behavior ID, what
    the call graph and the other findings say around it. A behavior whose scenario would say
    little is linked to a stronger one's obligation instead (see `_folded`).
    """
    generated: list[TestObligation] = []
    by_semantics: dict[tuple[str, str, str, CoverageStatus], TestObligation] = {}
    coverage_by_behavior: dict[str, CoverageStatus] = {}
    for behavior in behaviors:
        grounded = (coverage_states or {}).get(behavior.id)
        coverage_by_behavior[behavior.id] = _coverage_status(
            candidate_tests.get(behavior.id, []), mapping_incomplete, grounded
        )
    contexts = contexts or {}
    quoting = {
        behavior.id: evidence_scenarios(behavior, changes or {}, contexts.get(behavior.id))
        for behavior in behaviors
    }
    weak = {
        behavior.id
        for behavior in behaviors
        if quoting[behavior.id]
        and weakly_anchored(behavior, changes or {}, contexts.get(behavior.id))
    }
    kinds = {behavior.id: primary_kind(behavior, changes or {}) for behavior in behaviors}
    folded = _folded(behaviors, quoting, weak, kinds, contexts, coverage_by_behavior)
    # The obligation each behavior's first scenario became, for a folded behavior to join.
    first_obligation: dict[str, TestObligation] = {}
    for behavior in behaviors:
        candidates = candidate_tests.get(behavior.id, [])
        coverage = coverage_by_behavior[behavior.id]
        gap = TEST_GAP_WITH_CANDIDATES if candidates else TEST_GAP_WITHOUT_CANDIDATES
        scenarios: tuple[Scenario, ...] = (
            ()
            if behavior.id in folded
            else quoting[behavior.id] or CATEGORY_PROFILES[behavior.category].scenarios
        )
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
        for position, scenario in enumerate(scenarios):
            # A category's scenarios name no symbol, so without the coverage status in the key
            # every behavior of a category shared one obligation, and a behavior with no test
            # showed another's candidates under `candidate_exists_unverified`. Merging only
            # behaviors whose status agrees keeps an obligation's status and candidates true of
            # each one. A scenario that quotes its evidence merges only with the same quotes.
            key = (
                canonical_phrase(scenario.given),
                canonical_phrase(scenario.when),
                canonical_phrase(scenario.then),
                coverage,
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
                if behavior.confidence > existing.confidence:
                    existing.type = scenario.type
                    existing.title = scenario.title
                    existing.given = scenario.given
                    existing.when = scenario.when
                    existing.then = scenario.then
                    existing.origin = scenario_origin
                existing.confidence = max(existing.confidence, behavior.confidence)
                if position == 0:
                    first_obligation.setdefault(behavior.id, existing)
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
            if position == 0:
                first_obligation.setdefault(behavior.id, obligation)
    by_id = {behavior.id: behavior for behavior in behaviors}
    for behavior_id, anchor in folded.items():
        target = first_obligation[anchor]
        behavior = by_id[behavior_id]
        candidates = candidate_tests.get(behavior_id, [])
        gap = TEST_GAP_WITH_CANDIDATES if candidates else TEST_GAP_WITHOUT_CANDIDATES
        relevance = quoting[behavior_id][0].relevance
        target.behavior_change_ids = sorted({*target.behavior_change_ids, behavior_id})
        target.priority = max(
            target.priority,
            obligation_priority(behavior.risk_score, relevance, gap, behavior.confidence),
        )
        target.candidate_existing_tests = _merge_candidate_tests(
            target.candidate_existing_tests, candidates
        )[: rules.max_candidate_tests_per_obligation]
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
                coverage_by_behavior,
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
