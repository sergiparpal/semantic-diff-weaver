"""The single per-category behavior profile.

Impact weight, obligation scenario templates, and candidate-test terminology used to live as
three independent dictionaries in ``scoring``, ``obligations``, and ``test_mapper``, each
indexed with a bare subscript and none checked for completeness. Adding a taxonomy value meant
finding all three or hitting ``KeyError`` at the first analysis that reached the new category.

They are one registry here, and completeness is enforced at import so an incomplete taxonomy
fails immediately and obviously rather than on a specific diff.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import BehaviorCategory, ObligationType, Origin


@dataclass(frozen=True)
class Scenario:
    type: ObligationType
    title: str
    given: str
    when: str
    then: str
    relevance: int = 95
    origin: Origin | None = None


_IMPACT: dict[BehaviorCategory, int] = {
    BehaviorCategory.AUTHORIZATION: 92,
    BehaviorCategory.SIDE_EFFECT: 84,
    BehaviorCategory.STATE_TRANSITION: 78,
    BehaviorCategory.ERROR_HANDLING: 72,
    BehaviorCategory.OUTPUT_CONTRACT: 70,
    BehaviorCategory.RETRY_TIMEOUT: 68,
    BehaviorCategory.VALIDATION: 65,
    BehaviorCategory.BOUNDARY: 64,
    BehaviorCategory.DEPENDENCY_INTERACTION: 62,
    BehaviorCategory.ORDERING: 60,
    BehaviorCategory.DEFAULT_BEHAVIOR: 58,
    BehaviorCategory.UNKNOWN: 55,
    BehaviorCategory.REFACTOR: 12,
}


_TEST_TERMS: dict[BehaviorCategory, set[str]] = {
    BehaviorCategory.BOUNDARY: {"boundary", "limit", "threshold"},
    BehaviorCategory.VALIDATION: {"valid", "invalid", "reject", "accept"},
    BehaviorCategory.ERROR_HANDLING: {"error", "exception", "failure"},
    BehaviorCategory.STATE_TRANSITION: {"state", "transition", "status"},
    BehaviorCategory.AUTHORIZATION: {"auth", "permission", "allowed", "denied"},
    BehaviorCategory.RETRY_TIMEOUT: {"retry", "timeout", "attempt", "limit"},
    BehaviorCategory.OUTPUT_CONTRACT: {"return", "output", "response", "field"},
    BehaviorCategory.SIDE_EFFECT: {"event", "notify", "persist", "write"},
    BehaviorCategory.ORDERING: {"order", "sequence", "precedence"},
    BehaviorCategory.DEFAULT_BEHAVIOR: {"default", "omitted"},
    BehaviorCategory.DEPENDENCY_INTERACTION: {"dependency", "client", "service"},
    BehaviorCategory.REFACTOR: {"regression", "characterization"},
    BehaviorCategory.UNKNOWN: {"review", "behavior"},
}


_SCENARIOS: dict[BehaviorCategory, tuple[Scenario, ...]] = {
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


@dataclass(frozen=True)
class CategoryProfile:
    """Everything the pipeline derives from a behavior category."""

    impact: int
    scenarios: tuple[Scenario, ...]
    test_terms: frozenset[str]


_incomplete = sorted(
    category.value
    for category in BehaviorCategory
    if category not in _IMPACT or category not in _SCENARIOS or category not in _TEST_TERMS
)
# Must precede the registry below: an incomplete taxonomy would otherwise surface as a bare
# KeyError from the comprehension rather than as this named, actionable failure.
if _incomplete:  # pragma: no cover - guards a taxonomy edit, not a runtime input
    raise RuntimeError(f"BehaviorCategory values without a complete profile: {_incomplete}")

CATEGORY_PROFILES: dict[BehaviorCategory, CategoryProfile] = {
    category: CategoryProfile(
        impact=_IMPACT[category],
        scenarios=_SCENARIOS[category],
        test_terms=frozenset(_TEST_TERMS[category]),
    )
    for category in BehaviorCategory
}
