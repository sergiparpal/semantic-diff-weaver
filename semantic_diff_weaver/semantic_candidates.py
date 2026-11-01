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
# Every loop has a limit, so the word alone makes none a retry: `while len(payload) > limit`
# shrinks a payload until it fits.
LOOP_RETRY_TERMS = tuple(term for term in RETRY_TERMS if term != "limit")
SIDE_EFFECT_TERMS = (
    # A warning reaches whoever runs the code, as `pytest.warns` observes; it depends on nothing.
    "warn",
    # So does a printed line, as `capsys` observes; `sys.stdout.write()` was one already, by `write`.
    "print",
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
    # The variables its changed assignments write to, which name a module-level change.
    assigned_names: frozenset[str] = frozenset()


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
    if not _has_boundary_comparison(delta):
        # `is None`, `in STARS`, and `method == 'GET'` have no below or above to test. They are
        # read the way a condition comparing no boundary is, so they share a category with the
        # condition that holds them, when it changed too, and join its finding.
        return _classify_guard(combined) or (
            BehaviorCategory.UNKNOWN,
            "A membership, identity, or equality test changed without enough local contract "
            "context.",
            "Values that pass or fail the changed test may now follow a different path; the "
            "observable effect needs review.",
            0.55,
            ["The surrounding runtime contract is unavailable to static analysis."],
            "SDW-RULE-UNKNOWN-COMPARISON",
        )
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
    if delta.metadata.get("annotations_only"):
        # The declared contract changed, so it stays an output contract change, but nothing a
        # caller passes did: the text used to ask callers to supply different arguments.
        return (
            BehaviorCategory.OUTPUT_CONTRACT,
            "Only the type annotations of a callable signature appear to have changed.",
            "Callers pass the same arguments; type checkers, and code that reads annotations at "
            "runtime, may see a different declared contract.",
            0.88,
            [
                "Annotations change behavior only where something reads them at runtime, such as "
                "a framework the callable is registered with."
            ],
            "SDW-RULE-SIGNATURE-ANNOTATIONS",
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
    if _contains(combined, LOOP_RETRY_TERMS):
        return (
            BehaviorCategory.RETRY_TIMEOUT,
            "A loop, retry, or stop condition appears to have changed.",
            "The operation may perform a different number of attempts or terminate under different conditions.",
            0.84,
            [],
            "SDW-RULE-LOOP",
        )
    # Most loops walk a collection or trim a value, and every changed loop used to be a retry: a
    # `for` over a command's parameters, or a `while i > 0` dropping words until a text fit. A loop
    # is a retry when its own names or its symbol's say so.
    return (
        BehaviorCategory.UNKNOWN,
        "A loop's iteration or stop condition changed without enough local contract context.",
        "The operation may now process a different set or number of items; the observable effect "
        "needs review.",
        0.55,
        ["The surrounding runtime contract is unavailable to static analysis."],
        "SDW-RULE-UNKNOWN-LOOP",
    )


def _has_boundary_comparison(delta: StructuralDelta) -> bool:
    """Whether a value the delta changed compares a boundary: an ordering, or an equality with a
    number or a length.

    Read from the AST-built probe. The display-text fallback, kept for deltas without one,
    scans for operator characters: it also matches operators inside string literals and shift
    operators, counts every equality, and misses an `==` that redaction consumed. It reads only
    the changed expressions, never the symbol name, whose synthetic forms (``<module>``,
    ``<unparsed>``) contain angle brackets.
    """
    if delta.probe is not None:
        return delta.probe.compares
    expressions = " ".join(filter(None, (delta.old, delta.new)))
    return any(operator in expressions for operator in COMPARISON_OPERATORS)


def _classify_guard(combined: str) -> Classification | None:
    """The keyword rules for a changed condition or comparison that compares no boundary."""
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
    return None


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
    return _classify_guard(combined) or (
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
    if delta.metadata.get("internal_calls"):
        # A call to its own method, its module's own function, or a pure builtin has no failure or
        # unexpected response of a dependency to test for: 8 of the 15 dependency findings on the
        # real pull requests of 2026-10-31 changed such calls.
        return (
            BehaviorCategory.UNKNOWN,
            "A call to the module's own code changed without enough local contract context.",
            "The symbol now relies on different logic of its own module; the observable effect "
            "needs review.",
            0.55,
            # The unknown condition's own assumption: a finding is penalized for each distinct
            # one, and this call usually joins its symbol's unknown condition or comparison.
            # A third assumption took six findings of a truncated range below the reporting
            # confidence on adaptive-policy-compilation.
            ["The surrounding runtime contract is unavailable to static analysis."],
            "SDW-RULE-UNKNOWN-INTERNAL-CALL",
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


# A changed API schema, by its delta's kind. One change can narrow what a request may send — a
# validation change — or alter what a response returns — an output contract change — so where
# in the document it is decides: a parameter, a request body, or a standalone JSON Schema
# validates input, while a response or a reusable component is what clients read.
_VALIDATING_CONTEXTS = frozenset({"request", "schema"})
_API_CLIENTS = "The API's clients are unavailable to static analysis."
_SCHEMA_REFS = "References between schemas are compared as text and never resolved."
_API_RULES: dict[str, Classification] = {
    "document_parse_incomplete": (
        BehaviorCategory.UNKNOWN,
        "A changed API schema could not be parsed completely.",
        "The operations and schemas it defines may have changed and need review.",
        0.68,
        ["Only the document's path is available because parsing failed."],
        "SDW-RULE-SCHEMA-PARSE-INCOMPLETE",
    ),
    "api_operation_removed": (
        BehaviorCategory.OUTPUT_CONTRACT,
        "An API operation was removed.",
        "Clients that call it now get an error instead of its response.",
        0.86,
        [_API_CLIENTS],
        "SDW-RULE-API-OPERATION",
    ),
    "api_operation_added": (
        BehaviorCategory.UNKNOWN,
        "An API operation was added.",
        "A new operation becomes reachable; its intended contract needs characterizing tests.",
        0.58,
        ["The intended contract of a new operation is unavailable to static analysis."],
        "SDW-RULE-API-OPERATION",
    ),
    "api_parameter_removed": (
        BehaviorCategory.VALIDATION,
        "An API parameter was removed.",
        "Requests that still pass it may be rejected or have it ignored.",
        0.76,
        [_API_CLIENTS],
        "SDW-RULE-API-PARAMETER",
    ),
    "api_parameter_required_change": (
        BehaviorCategory.VALIDATION,
        "Whether an API parameter is required changed.",
        "Requests that omit it are now rejected, or accepted where they were not.",
        0.84,
        [_API_CLIENTS],
        "SDW-RULE-API-PARAMETER",
    ),
    "api_request_body_change": (
        BehaviorCategory.VALIDATION,
        "An API request body was added or removed.",
        "Requests are accepted with a different body than before.",
        0.78,
        [_API_CLIENTS],
        "SDW-RULE-API-REQUEST-BODY",
    ),
    "api_request_body_required_change": (
        BehaviorCategory.VALIDATION,
        "Whether an API request needs a body changed.",
        "Requests without one are now rejected, or accepted where they were not.",
        0.84,
        [_API_CLIENTS],
        "SDW-RULE-API-REQUEST-BODY",
    ),
    "api_response_added": (
        BehaviorCategory.OUTPUT_CONTRACT,
        "An API response was added.",
        "Clients can now receive a response they do not handle.",
        0.8,
        [_API_CLIENTS],
        "SDW-RULE-API-RESPONSE",
    ),
    "api_response_removed": (
        BehaviorCategory.OUTPUT_CONTRACT,
        "An API response was removed.",
        "Clients that handle it no longer receive it, or receive another in its place.",
        0.82,
        [_API_CLIENTS],
        "SDW-RULE-API-RESPONSE",
    ),
    "api_security_change": (
        BehaviorCategory.AUTHORIZATION,
        "The security an API requires changed.",
        "Callers with the former credentials may now be denied, or callers without them allowed.",
        0.86,
        [_API_CLIENTS],
        "SDW-RULE-API-SECURITY",
    ),
    "api_server_change": (
        BehaviorCategory.OUTPUT_CONTRACT,
        "Where the API is served from changed.",
        "Clients built for the former address may no longer reach its operations.",
        0.72,
        [_API_CLIENTS],
        "SDW-RULE-API-SERVER",
    ),
    "schema_required_change": (
        BehaviorCategory.VALIDATION,
        "Which schema properties are required changed.",
        "Input that omits a newly required property is now rejected, or accepted where it was not.",
        0.84,
        [_SCHEMA_REFS],
        "SDW-RULE-SCHEMA-REQUIRED",
    ),
    "schema_bound_change": (
        BehaviorCategory.BOUNDARY,
        "A schema bound changed.",
        "Values at or past the bound are accepted or rejected differently.",
        0.84,
        [_SCHEMA_REFS],
        "SDW-RULE-SCHEMA-BOUND",
    ),
    "schema_pattern_change": (
        BehaviorCategory.VALIDATION,
        "A schema's pattern or format changed.",
        "Strings the former pattern or format accepted may be rejected, or the reverse.",
        0.8,
        [_SCHEMA_REFS],
        "SDW-RULE-SCHEMA-PATTERN",
    ),
    "schema_default_change": (
        BehaviorCategory.DEFAULT_BEHAVIOR,
        "A schema default changed.",
        "Input that omits the value gets the new default.",
        0.84,
        [_SCHEMA_REFS],
        "SDW-RULE-SCHEMA-DEFAULT",
    ),
    "schema_keyword_change": (
        BehaviorCategory.UNKNOWN,
        "A schema keyword changed.",
        "What the schema accepts or returns may have changed; the intended contract needs review.",
        0.55,
        [_SCHEMA_REFS],
        "SDW-RULE-SCHEMA-KEYWORD",
    ),
}
# Kinds that mean one thing for input and another for output: (validating, output-facing).
_API_CONTEXT_RULES: dict[str, tuple[Classification, Classification]] = {
    "api_media_type_added": (
        (
            BehaviorCategory.VALIDATION,
            "An API request media type was added.",
            "Requests can now be sent in a form the operation did not accept.",
            0.74,
            [_API_CLIENTS],
            "SDW-RULE-API-MEDIA-TYPE",
        ),
        (
            BehaviorCategory.OUTPUT_CONTRACT,
            "An API response media type was added.",
            "Clients can now receive the response in a form they did not.",
            0.74,
            [_API_CLIENTS],
            "SDW-RULE-API-MEDIA-TYPE",
        ),
    ),
    "api_media_type_removed": (
        (
            BehaviorCategory.VALIDATION,
            "An API request media type was removed.",
            "Requests sent in that form are now rejected.",
            0.8,
            [_API_CLIENTS],
            "SDW-RULE-API-MEDIA-TYPE",
        ),
        (
            BehaviorCategory.OUTPUT_CONTRACT,
            "An API response media type was removed.",
            "Clients that ask for that form no longer get it.",
            0.8,
            [_API_CLIENTS],
            "SDW-RULE-API-MEDIA-TYPE",
        ),
    ),
    "schema_property_removed": (
        (
            BehaviorCategory.VALIDATION,
            "A schema property was removed from accepted input.",
            "Input that still sends it may now be rejected or have it ignored.",
            0.78,
            [_SCHEMA_REFS],
            "SDW-RULE-SCHEMA-PROPERTY",
        ),
        (
            BehaviorCategory.OUTPUT_CONTRACT,
            "A schema property was removed.",
            "Consumers that read it no longer get it.",
            0.84,
            [_SCHEMA_REFS],
            "SDW-RULE-SCHEMA-PROPERTY",
        ),
    ),
    "schema_property_added": (
        (
            BehaviorCategory.VALIDATION,
            "A schema property was added to accepted input.",
            "Input can now carry it, and its value needs validating.",
            0.62,
            [_SCHEMA_REFS],
            "SDW-RULE-SCHEMA-PROPERTY",
        ),
        (
            BehaviorCategory.OUTPUT_CONTRACT,
            "A schema property was added.",
            "Output now carries a field consumers may not expect.",
            0.62,
            [_SCHEMA_REFS],
            "SDW-RULE-SCHEMA-PROPERTY",
        ),
    ),
    "schema_enum_change": (
        (
            BehaviorCategory.VALIDATION,
            "The values a schema accepts changed.",
            "Input with a value the enumeration gained or lost is accepted or rejected "
            "differently.",
            0.82,
            [_SCHEMA_REFS],
            "SDW-RULE-SCHEMA-ENUM",
        ),
        (
            BehaviorCategory.OUTPUT_CONTRACT,
            "The values a schema can carry changed.",
            "Consumers can receive a value they do not handle, or stop receiving one they do.",
            0.8,
            [_SCHEMA_REFS],
            "SDW-RULE-SCHEMA-ENUM",
        ),
    ),
    "schema_type_change": (
        (
            BehaviorCategory.VALIDATION,
            "The type a schema accepts changed.",
            "Input of the former type may be rejected, or input of another type accepted.",
            0.82,
            [_SCHEMA_REFS],
            "SDW-RULE-SCHEMA-TYPE",
        ),
        (
            BehaviorCategory.OUTPUT_CONTRACT,
            "A schema's type changed.",
            "Consumers receive a value of another type than they parse.",
            0.84,
            [_SCHEMA_REFS],
            "SDW-RULE-SCHEMA-TYPE",
        ),
    ),
    "schema_ref_change": (
        (
            BehaviorCategory.VALIDATION,
            "Input is now validated against another schema.",
            "Input the former schema accepted may be rejected, or the reverse.",
            0.72,
            [_SCHEMA_REFS],
            "SDW-RULE-SCHEMA-REF",
        ),
        (
            BehaviorCategory.OUTPUT_CONTRACT,
            "Output now follows another schema.",
            "Consumers receive a value shaped by a schema they did not parse before.",
            0.74,
            [_SCHEMA_REFS],
            "SDW-RULE-SCHEMA-REF",
        ),
    ),
    "schema_removed": (
        (
            BehaviorCategory.VALIDATION,
            "A schema that validated input was removed.",
            "Input it constrained is checked differently, or not at all.",
            0.76,
            [_SCHEMA_REFS],
            "SDW-RULE-SCHEMA-LIFECYCLE",
        ),
        (
            BehaviorCategory.OUTPUT_CONTRACT,
            "A schema was removed.",
            "Output or references that used it no longer have its shape to rely on.",
            0.8,
            [_SCHEMA_REFS],
            "SDW-RULE-SCHEMA-LIFECYCLE",
        ),
    ),
}


def _classify_api_schema(
    delta: StructuralDelta, rules: RulesConfig, combined: str
) -> Classification | None:
    validating = delta.metadata.get("context") in _VALIDATING_CONTEXTS
    if delta.kind in _API_CONTEXT_RULES:
        for_input, for_output = _API_CONTEXT_RULES[delta.kind]
        return for_input if validating else for_output
    if delta.kind == "api_parameter_added":
        if delta.metadata.get("required") is True:
            return (
                BehaviorCategory.VALIDATION,
                "A required API parameter was added.",
                "Requests that omit it are now rejected.",
                0.82,
                [_API_CLIENTS],
                "SDW-RULE-API-PARAMETER",
            )
        return (
            BehaviorCategory.DEFAULT_BEHAVIOR,
            "An optional API parameter was added.",
            "Requests that omit it should behave as before; those that pass it reach new behavior.",
            0.62,
            [_API_CLIENTS],
            "SDW-RULE-API-PARAMETER",
        )
    if delta.kind == "schema_added":
        if delta.metadata.get("context") == "request":
            return (
                BehaviorCategory.VALIDATION,
                "A schema now validates API input.",
                "Input it constrains is checked where it was not.",
                0.74,
                [_SCHEMA_REFS],
                "SDW-RULE-SCHEMA-LIFECYCLE",
            )
        if delta.metadata.get("context") == "response":
            return (
                BehaviorCategory.OUTPUT_CONTRACT,
                "An API response now has a schema.",
                "Clients receive a value with a shape the operation did not declare.",
                0.74,
                [_SCHEMA_REFS],
                "SDW-RULE-SCHEMA-LIFECYCLE",
            )
        return (
            BehaviorCategory.UNKNOWN,
            "A schema was added.",
            "A new shape becomes available; its intended contract needs characterizing tests.",
            0.55,
            [_SCHEMA_REFS],
            "SDW-RULE-SCHEMA-LIFECYCLE",
        )
    return _API_RULES.get(delta.kind)


# A changed value of a configuration file the repository names, read by the words of its key,
# `database.pool.max_size`, in this order: what decides access first, a switch before what it
# switches, and a key no word explains last. A key that was added has no reader yet, maybe.
_CONFIG_AUTH_TERMS = (
    *AUTH_TERMS,
    "admin",
    "admins",
    "roles",
    "permissions",
    "scope",
    "scopes",
    "acl",
    "acls",
)
_CONFIG_VALIDATION_TERMS = (
    *VALIDATION_TERMS,
    "allow",
    "allowlist",
    "deny",
    "denylist",
    "blocklist",
    "pattern",
    "regex",
    "required",
)
_CONFIG_BOUNDARY_TERMS = (
    "max",
    "min",
    "maximum",
    "minimum",
    "limit",
    "limits",
    "threshold",
    "thresholds",
    "size",
    "capacity",
    "quota",
    "cap",
)
_CONFIG_FLAG_TERMS = (
    "enable",
    "enabled",
    "disable",
    "disabled",
    "flag",
    "flags",
    "feature",
    "features",
    "toggle",
    "toggles",
)
_CONFIG_DEPENDENCY_TERMS = (
    "url",
    "urls",
    "uri",
    "host",
    "hosts",
    "hostname",
    "port",
    "endpoint",
    "endpoints",
    "dsn",
    "server",
    "servers",
    "address",
    "region",
    "bucket",
    "queue",
    "topic",
    "database",
    "db",
)
_CONFIG_READER = "What reads the configuration value is unavailable to static analysis."
_CONFIG_VERBS = {
    "config_value_change": "changed",
    "config_key_added": "was added",
    "config_key_removed": "was removed",
}


def _classify_config(
    delta: StructuralDelta, rules: RulesConfig, combined: str
) -> Classification | None:
    if delta.kind == "config_parse_incomplete":
        return (
            BehaviorCategory.UNKNOWN,
            "A changed configuration file could not be parsed completely.",
            "The values it sets may have changed and need review.",
            0.68,
            ["Only the file's path is available because parsing failed."],
            "SDW-RULE-CONFIG-PARSE-INCOMPLETE",
        )
    verb = _CONFIG_VERBS.get(delta.kind)
    if verb is None:
        return None
    key = delta.symbol
    types = {delta.metadata.get("old_type"), delta.metadata.get("new_type")} - {None}
    # A key that was added may not be read yet, so what its words say is less certain.
    added = 0.08 if delta.kind == "config_key_added" else 0.0
    rules_by_words: tuple[tuple[bool, BehaviorCategory, str, str, float, str], ...] = (
        (
            _contains(key, _CONFIG_AUTH_TERMS),
            BehaviorCategory.AUTHORIZATION,
            "access setting",
            "Who may do what can differ wherever the program reads it.",
            0.76,
            "SDW-RULE-CONFIG-AUTH",
        ),
        (
            _contains(key, LOOP_RETRY_TERMS),
            BehaviorCategory.RETRY_TIMEOUT,
            "retry or timeout setting",
            "How long the program waits, or how often it tries again, changes where it reads it.",
            0.78,
            "SDW-RULE-CONFIG-RETRY",
        ),
        (
            types <= {"number"} and _contains(key, _CONFIG_BOUNDARY_TERMS),
            BehaviorCategory.BOUNDARY,
            "limit",
            "Values at or past the limit are handled differently where the program reads it.",
            0.76,
            "SDW-RULE-CONFIG-BOUNDARY",
        ),
        (
            _contains(key, _CONFIG_VALIDATION_TERMS),
            BehaviorCategory.VALIDATION,
            "validation setting",
            "Input the program accepted may now be rejected, or input it rejected accepted.",
            0.72,
            "SDW-RULE-CONFIG-VALIDATION",
        ),
        (
            "boolean" in types or _contains(key, _CONFIG_FLAG_TERMS),
            BehaviorCategory.DEFAULT_BEHAVIOR,
            "switch",
            "The code the switch guards now runs, or stops running, by default.",
            0.74,
            "SDW-RULE-CONFIG-FLAG",
        ),
        (
            _contains(key, _CONFIG_DEPENDENCY_TERMS),
            BehaviorCategory.DEPENDENCY_INTERACTION,
            "connection setting",
            "The program may now reach another service, address, or resource.",
            0.72,
            "SDW-RULE-CONFIG-DEPENDENCY",
        ),
    )
    for matched, category, what, impact, confidence, rule_id in rules_by_words:
        if matched:
            return (
                category,
                f"A configured {what} {verb}.",
                impact,
                confidence - added,
                [_CONFIG_READER],
                rule_id,
            )
    if delta.kind == "config_key_added":
        return (
            BehaviorCategory.UNKNOWN,
            "A configuration key was added.",
            "A new setting becomes available; what reads it needs characterizing tests.",
            0.56,
            [_CONFIG_READER],
            "SDW-RULE-CONFIG-VALUE",
        )
    if delta.kind == "config_key_removed":
        return (
            BehaviorCategory.DEFAULT_BEHAVIOR,
            "A configuration key was removed.",
            "Code that read it falls back to its own default, or fails without it.",
            0.64,
            [_CONFIG_READER],
            "SDW-RULE-CONFIG-VALUE",
        )
    return (
        BehaviorCategory.DEFAULT_BEHAVIOR,
        "A configuration value changed.",
        "Code that reads the value now gets the new one by default.",
        0.62,
        [_CONFIG_READER],
        "SDW-RULE-CONFIG-VALUE",
    )


CLASSIFIERS: tuple[Classifier, ...] = (
    _classify_config,
    _classify_api_schema,
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

# Classifications that give way to any other in the same finding, which is then about the other
# one: grouped with the return change of a method that had stopped returning `None`, a narrowed
# return annotation led its finding with "Only the type annotations ... appear to have changed".
YIELDING_RULES = frozenset({"SDW-RULE-SIGNATURE-ANNOTATIONS"})

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
    assigned_names: set[str] = field(default_factory=set)
    # Whether every classification so far is one of `YIELDING_RULES`.
    yielding: bool = False

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
            assigned_names=frozenset(self.assigned_names),
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
        assigned_names = {
            name for name in delta.metadata.get("assigned_names", []) if isinstance(name, str)
        }
        yields = rule_id in YIELDING_RULES
        if key in grouped:
            current = grouped[key]
            if current.yielding and not yields:
                # What the group said so far described its own evidence alone.
                current.summary, current.observable_impact = summary, impact
                current.assumptions = set(assumptions)
                current.yielding = False
            elif yields == current.yielding:
                current.assumptions.update(assumptions)
            current.evidence.append(evidence)
            current.confidence_baseline = max(current.confidence_baseline, baseline)
            current.rule_ids.append(rule_id)
            current.related_calls.update(related_calls)
            current.related_paths.update(related_paths)
            current.assigned_names.update(assigned_names)
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
                assigned_names=assigned_names,
                yielding=yields,
            )
    candidates = [
        accumulator.build(path, symbol, category)
        for (path, symbol, category), accumulator in grouped.items()
    ]
    return sorted(candidates, key=lambda item: (item.path, item.symbol, item.category.value))
