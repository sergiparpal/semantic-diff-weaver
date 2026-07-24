"""Deterministic, evidence-backed semantic candidate rules."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from .ast_diff import StructuralDelta
from .models import BehaviorCategory, Evidence, Origin, WeaverConfig

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

Classification = tuple[BehaviorCategory, str, str, float, list[str], str]
Classifier = Callable[[StructuralDelta, WeaverConfig, str], Classification | None]


@dataclass
class SemanticCandidate:
    category: BehaviorCategory
    summary: str
    observable_impact: str
    evidence: list[Evidence]
    confidence_baseline: float
    assumptions: list[str] = field(default_factory=list)
    origin: Origin = Origin.DETERMINISTIC
    rule_ids: list[str] = field(default_factory=list)
    related_calls: set[str] = field(default_factory=set)
    related_paths: set[str] = field(default_factory=set)

    @property
    def path(self) -> str:
        return self.evidence[0].path

    @property
    def symbol(self) -> str:
        return self.evidence[0].symbol or "<module>"


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    tokens = set(re.findall(r"[a-z0-9]+", camel_split.casefold()))
    return bool(tokens.intersection(terms))


def _classify_parse_incomplete(
    delta: StructuralDelta, config: WeaverConfig, combined: str
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
    delta: StructuralDelta, config: WeaverConfig, combined: str
) -> Classification | None:
    if delta.kind not in {"symbol_added", "symbol_removed"}:
        return None
    ambiguous = bool(delta.metadata.get("ambiguous_match"))
    direction = "added" if delta.kind == "symbol_added" else "removed"
    assumptions = ["The runtime and caller contract is unavailable to static analysis."]
    if ambiguous:
        assumptions.append(
            "Multiple near-equal symbol matches prevented a reliable rename or move correlation."
        )
    return (
        BehaviorCategory.UNKNOWN,
        f"A callable or structural symbol was {direction} without a reliable semantic match.",
        "Callers or observable behavior may change; the intended compatibility contract needs review.",
        0.42 if ambiguous else 0.58,
        assumptions,
        "SDW-RULE-AMBIGUOUS-SYMBOL" if ambiguous else "SDW-RULE-SYMBOL-LIFECYCLE",
    )


def _classify_comparison(
    delta: StructuralDelta, config: WeaverConfig, combined: str
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
    delta: StructuralDelta, config: WeaverConfig, combined: str
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
    delta: StructuralDelta, config: WeaverConfig, combined: str
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
    delta: StructuralDelta, config: WeaverConfig, combined: str
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
    delta: StructuralDelta, config: WeaverConfig, combined: str
) -> Classification | None:
    if delta.kind != "assignment_change":
        return None
    return (
        BehaviorCategory.STATE_TRANSITION,
        "A state assignment or transition appears to have changed.",
        "The operation may now enter, retain, or reject a different observable state.",
        0.82,
        [],
        "SDW-RULE-STATE",
    )


def _classify_loop(
    delta: StructuralDelta, config: WeaverConfig, combined: str
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


def _classify_condition(
    delta: StructuralDelta, config: WeaverConfig, combined: str
) -> Classification | None:
    if delta.kind != "condition_change":
        return None
    if any(operator in combined for operator in ("<", ">", "==", "!=")):
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
    delta: StructuralDelta, config: WeaverConfig, combined: str
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
    delta: StructuralDelta, config: WeaverConfig, combined: str
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
    delta: StructuralDelta, config: WeaverConfig, combined: str
) -> Classification | None:
    if delta.kind != "structural_refactor":
        return None
    materiality = float(delta.metadata.get("materiality", 0.0))
    if materiality > config.rules.refactor_materiality_threshold:
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
    delta: StructuralDelta, config: WeaverConfig, combined: str
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


def classify_delta(delta: StructuralDelta, config: WeaverConfig) -> Classification:
    combined = " ".join(filter(None, (delta.old, delta.new, delta.symbol)))
    for classifier in CLASSIFIERS:
        result = classifier(delta, config, combined)
        if result is not None:
            return result
    return UNKNOWN_FALLBACK


def build_candidates(
    deltas: list[StructuralDelta], config: WeaverConfig | None = None
) -> list[SemanticCandidate]:
    """Apply stable rules, create evidence IDs, and merge same-symbol categories."""
    effective_config = config or WeaverConfig()
    grouped: dict[tuple[str, str, BehaviorCategory], SemanticCandidate] = {}
    for index, delta in enumerate(deltas, start=1):
        category, summary, impact, baseline, assumptions, rule_id = classify_delta(
            delta, effective_config
        )
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
            current.assumptions = sorted(set([*current.assumptions, *assumptions]))
            current.rule_ids.append(rule_id)
            current.related_calls.update(related_calls)
            current.related_paths.update(related_paths)
        else:
            grouped[key] = SemanticCandidate(
                category=category,
                summary=summary,
                observable_impact=impact,
                evidence=[evidence],
                confidence_baseline=baseline,
                assumptions=assumptions,
                rule_ids=[rule_id],
                related_calls=related_calls,
                related_paths=related_paths,
            )
    return sorted(grouped.values(), key=lambda item: (item.path, item.symbol, item.category.value))


# Backward-compatible private alias.
_classification = classify_delta
_contains_terms = _contains
