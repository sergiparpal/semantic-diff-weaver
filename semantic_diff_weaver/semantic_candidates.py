"""Deterministic, evidence-backed semantic candidate rules."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from .ast_diff import StructuralDelta
from .models import BehaviorCategory, Evidence, Origin, RulesConfig, WeaverConfig

AUTH_TERMS = (
    "auth",
    "authenticate",
    "authentication",
    "authorize",
    "authorization",
    "permission",
    "permit",
    "role",
    "owner",
    "identity",
    "principal",
)
VALIDATION_TERMS = (
    "valid",
    "validate",
    "validation",
    "validator",
    "allowed",
    "reject",
    "accept",
)
RETRY_TERMS = (
    "retry",
    "retries",
    "attempt",
    "attempts",
    "timeout",
    "sleep",
    "backoff",
    "deadline",
    "limit",
)
SIDE_EFFECT_TERMS = (
    "save",
    "write",
    "delete",
    "remove",
    "commit",
    "publish",
    "send",
    "notify",
    "emit",
    "dispatch",
    "persist",
    "enqueue",
)
STATE_TERMS = (
    "state",
    "states",
    "status",
    "statuses",
    "transition",
    "transitions",
)

COMPARISON_OPERATORS = ("<", ">", "==", "!=")
Classification = tuple[BehaviorCategory, str, str, float, list[str], str]
Classifier = Callable[[StructuralDelta, RulesConfig, str], Classification | None]


@dataclass(frozen=True)
class SemanticCandidate:
    """One deterministic or model-supported behavior finding.

    Immutable, and ``path``/``symbol`` are stored rather than derived from ``evidence[0]``.
    Three stages merge candidates — rule grouping here, model reconciliation in
    ``semantic_interpreter``, and deduplication in ``service`` — and each now produces a new
    instance. Derived identity was unsafe because merging re-sorts the evidence list, so a
    candidate could silently change the ``(path, symbol)`` another stage had already keyed on.
    """

    path: str
    symbol: str
    category: BehaviorCategory
    summary: str
    observable_impact: str
    evidence: tuple[Evidence, ...]
    confidence_baseline: float
    assumptions: tuple[str, ...] = ()
    origin: Origin = Origin.DETERMINISTIC
    rule_ids: tuple[str, ...] = ()
    related_calls: frozenset[str] = frozenset()
    related_paths: frozenset[str] = frozenset()


def evidence_identity(evidence: Sequence[Evidence]) -> tuple[str, str]:
    """Return the ``(path, symbol)`` a candidate built from this evidence is identified by."""
    first = evidence[0]
    return first.path, first.symbol or "<module>"


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    tokens = set(re.findall(r"[a-z0-9]+", camel_split.casefold()))
    return bool(tokens.intersection(terms))


def _classify_parse_incomplete(
    delta: StructuralDelta, rules: RulesConfig, combined: str
) -> Classification | None:
    if delta.kind != "parse_incomplete":
        return None
    return (
        BehaviorCategory.UNKNOWN,
        "Changed Python syntax could not be parsed completely.",
        "The affected committed hunk may change observable behavior and requires review.",
        0.68,
        ["Only bounded Git hunk metadata is available because static parsing failed."],
        "SDW-RULE-PARSE-INCOMPLETE",
    )


def _classify_symbol_lifecycle(
    delta: StructuralDelta, rules: RulesConfig, combined: str
) -> Classification | None:
    if delta.kind not in {"symbol_added", "symbol_removed"}:
        return None
    direction = "added" if delta.kind == "symbol_added" else "removed"
    if delta.metadata.get("ambiguous_match"):
        return (
            BehaviorCategory.UNKNOWN,
            f"A callable or structural symbol was {direction} without a reliable semantic match.",
            "Callers or observable behavior may change; the intended compatibility contract needs "
            "review.",
            0.42,
            [
                "The runtime and caller contract is unavailable to static analysis.",
                "Multiple near-equal symbol matches prevented a reliable rename or move correlation.",
            ],
            "SDW-RULE-AMBIGUOUS-SYMBOL",
        )
    # Without a near-equal symbol on the other side there was nothing to match, and only a
    # removal has callers to break. Added code, a whole new file's included, has no earlier
    # callers, only an intended contract for tests to pin down.
    if delta.kind == "symbol_added":
        return (
            BehaviorCategory.UNKNOWN,
            "A callable or structural symbol was added.",
            "New behavior becomes reachable; its intended contract needs characterizing tests.",
            0.58,
            ["The intended contract of new code is unavailable to static analysis."],
            "SDW-RULE-SYMBOL-LIFECYCLE",
        )
    return (
        BehaviorCategory.UNKNOWN,
        "A callable or structural symbol was removed.",
        "Callers that still use it may fail or change behavior; the intended compatibility "
        "contract needs review.",
        0.58,
        ["The runtime and caller contract is unavailable to static analysis."],
        "SDW-RULE-SYMBOL-LIFECYCLE",
    )


def _classify_comparison(
    delta: StructuralDelta, rules: RulesConfig, combined: str
) -> Classification | None:
    if delta.kind != "comparison_change":
        return None
    if _contains(combined, RETRY_TERMS):
        return (
            BehaviorCategory.RETRY_TIMEOUT,
            "A retry, timeout, or limit boundary appears to have changed.",
            "The exact attempt, timeout, or termination boundary may now produce a different outcome.",
            0.90,
            [],
            "SDW-RULE-RETRY-BOUNDARY",
        )
    return (
        BehaviorCategory.BOUNDARY,
        "A comparison boundary appears to have changed.",
        "Inputs at or adjacent to the changed boundary may now be accepted, rejected, or routed differently.",
        0.94,
        [],
        "SDW-RULE-BOUNDARY",
    )


def _classify_signature(
    delta: StructuralDelta, rules: RulesConfig, combined: str
) -> Classification | None:
    if delta.kind != "signature_change":
        return None
    if delta.metadata.get("default_changed"):
        return (
            BehaviorCategory.DEFAULT_BEHAVIOR,
            "A callable default argument appears to have changed.",
            "Callers that omit the affected argument may now observe a different result or path.",
            0.94,
            [],
            "SDW-RULE-DEFAULT",
        )
    return (
        BehaviorCategory.OUTPUT_CONTRACT,
        "A callable signature appears to have changed.",
        "Existing callers may need to supply different arguments or handle a changed callable contract.",
        0.88,
        [],
        "SDW-RULE-SIGNATURE",
    )


def _classify_error(
    delta: StructuralDelta, rules: RulesConfig, combined: str
) -> Classification | None:
    if delta.kind not in {"raise_change", "exception_handler_change"}:
        return None
    return (
        BehaviorCategory.ERROR_HANDLING,
        "An exception or error-handling path appears to have changed.",
        "The same failure trigger may now propagate, be swallowed, or expose a different error.",
        0.91,
        [],
        "SDW-RULE-ERROR",
    )


def _classify_return(
    delta: StructuralDelta, rules: RulesConfig, combined: str
) -> Classification | None:
    if delta.kind != "return_change":
        return None
    return (
        BehaviorCategory.OUTPUT_CONTRACT,
        "A visible return expression or shape appears to have changed.",
        "Consumers may observe a different value, status, field, or container shape.",
        0.88,
        [],
        "SDW-RULE-RETURN",
    )


def _classify_assignment(
    delta: StructuralDelta, rules: RulesConfig, combined: str
) -> Classification | None:
    if delta.kind != "assignment_change":
        return None
    if _contains(combined, STATE_TERMS):
        return (
            BehaviorCategory.STATE_TRANSITION,
            "A state assignment or transition appears to have changed.",
            "The operation may now enter, retain, or reject a different observable state.",
            0.82,
            [],
            "SDW-RULE-STATE",
        )
    # Most assignments hold configuration, data, or intermediate values. A module constant
    # that gained an entry is no transition to test as allowed, invalid, and repeated.
    return (
        BehaviorCategory.UNKNOWN,
        "An assigned value changed without enough local contract context.",
        "Code that reads the value may now behave differently; the observable effect needs review.",
        0.55,
        ["Whether the value is state, configuration, or data is unavailable to static analysis."],
        "SDW-RULE-UNKNOWN-ASSIGNMENT",
    )


def _classify_loop(
    delta: StructuralDelta, rules: RulesConfig, combined: str
) -> Classification | None:
    if delta.kind != "loop_change":
        return None
    return (
        BehaviorCategory.RETRY_TIMEOUT,
        "A loop, retry, or stop condition appears to have changed.",
        "The operation may perform a different number of attempts or terminate under different conditions.",
        0.84,
        [],
        "SDW-RULE-LOOP",
    )


def _has_boundary_comparison(delta: StructuralDelta) -> bool:
    """Whether a changed condition contains an ordering or equality comparison.

    Read from the AST-built probe. The display-text fallback, kept for deltas without one,
    scans for operator characters: it also matches operators inside string literals and shift
    operators, and misses an `==` that redaction consumed. It reads only the changed
    expressions, never the symbol name, whose synthetic forms (``<module>``, ``<unparsed>``)
    contain angle brackets.
    """
    if delta.probe is not None:
        return delta.probe.compares
    expressions = " ".join(filter(None, (delta.old, delta.new)))
    return any(operator in expressions for operator in COMPARISON_OPERATORS)


def _classify_condition(
    delta: StructuralDelta, rules: RulesConfig, combined: str
) -> Classification | None:
    if delta.kind != "condition_change":
        return None
    if _has_boundary_comparison(delta):
        category = (
            BehaviorCategory.RETRY_TIMEOUT
            if _contains(combined, RETRY_TERMS)
            else BehaviorCategory.BOUNDARY
        )
        return (
            category,
            "A condition boundary appears to have changed.",
            "Inputs at or adjacent to the changed condition may now follow a different path.",
            0.88,
            [],
            "SDW-RULE-CONDITION-BOUNDARY",
        )
    if _contains(combined, AUTH_TERMS):
        return (
            BehaviorCategory.AUTHORIZATION,
            "An authorization-related guard appears to have changed.",
            "Allowed and denied principals may no longer follow the same path.",
            0.70,
            ["Authorization meaning is inferred from conservative names and guard structure."],
            "SDW-RULE-AUTH-GUARD",
        )
    if _contains(combined, VALIDATION_TERMS):
        return (
            BehaviorCategory.VALIDATION,
            "An input validation condition appears to have changed.",
            "Some inputs may now be newly accepted or newly rejected.",
            0.74,
            ["Validation meaning is inferred from conservative names and condition structure."],
            "SDW-RULE-VALIDATION-GUARD",
        )
    if _contains(combined, RETRY_TERMS):
        return (
            BehaviorCategory.RETRY_TIMEOUT,
            "A retry or termination predicate appears to have changed.",
            "Recoverable, terminal, or exact-limit cases may now perform a different number of attempts.",
            0.78,
            [],
            "SDW-RULE-RETRY-GUARD",
        )
    return (
        BehaviorCategory.UNKNOWN,
        "A control-flow condition changed without enough local contract context.",
        "Competing input cases may now follow a different branch; the observable effect needs review.",
        0.55,
        ["The surrounding runtime contract is unavailable to static analysis."],
        "SDW-RULE-UNKNOWN-CONDITION",
    )


def _classify_call(
    delta: StructuralDelta, rules: RulesConfig, combined: str
) -> Classification | None:
    if delta.kind != "call_change":
        return None
    if _contains(combined, AUTH_TERMS):
        return (
            BehaviorCategory.AUTHORIZATION,
            "An authorization-related interaction appears to have changed.",
            "An allowed or denied principal may now receive a different outcome.",
            0.68,
            ["Authorization meaning is inferred from call names and structure."],
            "SDW-RULE-AUTH-CALL",
        )
    if _contains(combined, VALIDATION_TERMS):
        return (
            BehaviorCategory.VALIDATION,
            "A validator interaction appears to have changed.",
            "New input classes may be accepted or rejected.",
            0.70,
            ["Validation meaning is inferred from call names and structure."],
            "SDW-RULE-VALIDATION-CALL",
        )
    if _contains(combined, RETRY_TERMS):
        return (
            BehaviorCategory.RETRY_TIMEOUT,
            "A retry, delay, or timeout interaction appears to have changed.",
            "Attempt count, wait behavior, or termination may differ for failures.",
            0.72,
            ["Retry meaning is inferred from call names and structure."],
            "SDW-RULE-RETRY-CALL",
        )
    if _contains(combined, SIDE_EFFECT_TERMS):
        return (
            BehaviorCategory.SIDE_EFFECT,
            "An external or persistent side-effect call appears to have changed.",
            "An observable write, event, notification, or dispatch may now occur or be absent.",
            0.70,
            ["Side-effect meaning is inferred from conservative call-name signals."],
            "SDW-RULE-SIDE-EFFECT",
        )
    return (
        BehaviorCategory.DEPENDENCY_INTERACTION,
        "A dependency call or its arguments appear to have changed.",
        "Dependency success, failure, or unexpected responses may now be handled differently.",
        0.66,
        ["The dependency contract is not available to static analysis."],
        "SDW-RULE-DEPENDENCY",
    )


def _classify_ordering(
    delta: StructuralDelta, rules: RulesConfig, combined: str
) -> Classification | None:
    if delta.kind not in {"call_order_change", "condition_order_change", "statement_order_change"}:
        return None
    return (
        BehaviorCategory.ORDERING,
        "Execution or call ordering appears to have changed.",
        "Sequence-sensitive cases or competing conditions may now produce a different outcome.",
        0.72,
        [],
        "SDW-RULE-ORDERING",
    )


def _classify_refactor(
    delta: StructuralDelta, rules: RulesConfig, combined: str
) -> Classification | None:
    if delta.kind != "structural_refactor":
        return None
    materiality = float(delta.metadata.get("materiality", 0.0))
    if materiality > rules.refactor_materiality_threshold:
        return (
            BehaviorCategory.UNKNOWN,
            "A refactor-like change exceeds the configured materiality threshold.",
            "Observable behavior may have changed even though the symbol appears refactored.",
            0.58,
            ["Normalized structural similarity was insufficient for a no-behavior-change claim."],
            "SDW-RULE-REFACTOR-MATERIAL",
        )
    return (
        BehaviorCategory.REFACTOR,
        "The changed symbol retains the same normalized behavior-bearing structure.",
        "No material observable behavior change is apparent from the normalized syntax.",
        0.82,
        [],
        "SDW-RULE-REFACTOR",
    )


def _classify_decorator(
    delta: StructuralDelta, rules: RulesConfig, combined: str
) -> Classification | None:
    if delta.kind != "decorator_change" or not _contains(combined, AUTH_TERMS):
        return None
    return (
        BehaviorCategory.AUTHORIZATION,
        "An authorization-related decorator appears to have changed.",
        "Access to the decorated callable may now differ by principal.",
        0.72,
        ["Authorization meaning is inferred from decorator naming."],
        "SDW-RULE-AUTH-DECORATOR",
    )


CLASSIFIERS: tuple[Classifier, ...] = (
    _classify_parse_incomplete,
    _classify_symbol_lifecycle,
    _classify_comparison,
    _classify_signature,
    _classify_error,
    _classify_return,
    _classify_assignment,
    _classify_loop,
    _classify_condition,
    _classify_call,
    _classify_ordering,
    _classify_refactor,
    _classify_decorator,
)

UNKNOWN_FALLBACK: Classification = (
    BehaviorCategory.UNKNOWN,
    "A material structural change cannot be classified reliably from local evidence.",
    "An observable outcome may have changed; the affected contract needs review.",
    0.50,
    ["Static syntax does not expose the complete runtime contract."],
    "SDW-RULE-UNKNOWN",
)


def classify_delta(delta: StructuralDelta, rules: RulesConfig) -> Classification:
    # Keyword rules read the delta's probe, which leaves out the words of prose string
    # literals, so a changed message such as "Retry later" is not a retry change. A delta
    # without a probe falls back to its display text.
    words = (
        delta.probe.words
        if delta.probe is not None
        else " ".join(filter(None, (delta.old, delta.new)))
    )
    combined = " ".join(filter(None, (words, delta.symbol)))
    for classifier in CLASSIFIERS:
        result = classifier(delta, rules, combined)
        if result is not None:
            return result
    return UNKNOWN_FALLBACK


@dataclass
class _CandidateAccumulator:
    """Mutable scratch state for one ``(path, symbol, category)`` group.

    Kept separate from the frozen candidate so grouping can merge cheaply and the immutable
    result is constructed exactly once, when the group is complete.
    """

    summary: str
    observable_impact: str
    confidence_baseline: float
    evidence: list[Evidence] = field(default_factory=list)
    assumptions: set[str] = field(default_factory=set)
    rule_ids: list[str] = field(default_factory=list)
    related_calls: set[str] = field(default_factory=set)
    related_paths: set[str] = field(default_factory=set)

    def build(self, path: str, symbol: str, category: BehaviorCategory) -> SemanticCandidate:
        return SemanticCandidate(
            path=path,
            symbol=symbol,
            category=category,
            summary=self.summary,
            observable_impact=self.observable_impact,
            evidence=tuple(self.evidence),
            confidence_baseline=self.confidence_baseline,
            assumptions=tuple(sorted(self.assumptions)),
            rule_ids=tuple(self.rule_ids),
            related_calls=frozenset(self.related_calls),
            related_paths=frozenset(self.related_paths),
        )


def build_candidates(
    deltas: list[StructuralDelta], config: WeaverConfig | None = None
) -> list[SemanticCandidate]:
    """Apply stable rules, create evidence IDs, and merge same-symbol categories."""
    rules = (config or WeaverConfig()).rules
    grouped: dict[tuple[str, str, BehaviorCategory], _CandidateAccumulator] = {}
    for index, delta in enumerate(deltas, start=1):
        category, summary, impact, baseline, assumptions, rule_id = classify_delta(delta, rules)
        evidence = Evidence(
            id=f"ev-{index:03d}",
            path=delta.path,
            symbol=delta.symbol,
            old_lines=delta.old_lines,
            new_lines=delta.new_lines,
            hunk_id=delta.hunk_id,
            old=delta.old,
            new=delta.new,
            kind=delta.kind,
            parser_complete=delta.parser_complete,
        )
        key = (delta.path, delta.symbol, category)
        related_calls = {
            call
            for field_name in ("old_calls", "new_calls")
            for call in delta.metadata.get(field_name, [])
            if isinstance(call, str) and call != "<dynamic>"
        }
        related_paths = {
            path
            for field_name in ("old_path", "new_path")
            if isinstance((path := delta.metadata.get(field_name)), str)
        }
        related_paths.add(delta.path)
        if key in grouped:
            current = grouped[key]
            current.evidence.append(evidence)
            current.confidence_baseline = max(current.confidence_baseline, baseline)
            current.assumptions.update(assumptions)
            current.rule_ids.append(rule_id)
            current.related_calls.update(related_calls)
            current.related_paths.update(related_paths)
        else:
            grouped[key] = _CandidateAccumulator(
                summary=summary,
                observable_impact=impact,
                confidence_baseline=baseline,
                evidence=[evidence],
                assumptions=set(assumptions),
                rule_ids=[rule_id],
                related_calls=related_calls,
                related_paths=related_paths,
            )
    candidates = [
        accumulator.build(path, symbol, category)
        for (path, symbol, category), accumulator in grouped.items()
    ]
    return sorted(candidates, key=lambda item: (item.path, item.symbol, item.category.value))
