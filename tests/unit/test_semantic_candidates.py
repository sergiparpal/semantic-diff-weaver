from __future__ import annotations

from dataclasses import replace

import pytest

from semantic_diff_weaver.ast_diff import StructuralDelta
from semantic_diff_weaver.models import BehaviorCategory, Evidence, LineRange, Origin
from semantic_diff_weaver.scoring import confidence_score
from semantic_diff_weaver.semantic_candidates import (
    SemanticCandidate,
    build_candidates,
    evidence_identity,
)
from semantic_diff_weaver.service import _deduplicate_candidates


def delta(kind: str, old: str | None, new: str | None) -> StructuralDelta:
    return StructuralDelta(
        path="src/policy.py",
        symbol="Policy.apply",
        kind=kind,
        old=old,
        new=new,
        old_lines=LineRange(start=1, end=3),
        new_lines=LineRange(start=1, end=3),
        hunk_id="src/policy.py#hunk-001",
    )


@pytest.mark.parametrize(
    ("change", "category"),
    [
        (delta("comparison_change", "x < 5", "x <= 5"), BehaviorCategory.BOUNDARY),
        (
            StructuralDelta(
                **{
                    **delta("signature_change", "limit=2", "limit=3").__dict__,
                    "metadata": {"default_changed": True},
                }
            ),
            BehaviorCategory.DEFAULT_BEHAVIOR,
        ),
        (delta("raise_change", "ValueError", "TypeError"), BehaviorCategory.ERROR_HANDLING),
        (delta("condition_change", "is_valid(x)", "validate(x)"), BehaviorCategory.VALIDATION),
        (
            delta("condition_change", "user.is_owner", "user.is_admin"),
            BehaviorCategory.AUTHORIZATION,
        ),
        (
            delta("loop_change", "for attempt in range(2)", "for attempt in range(3)"),
            BehaviorCategory.RETRY_TIMEOUT,
        ),
        (delta("loop_change", "for item in items", "for item in rows"), BehaviorCategory.UNKNOWN),
        (delta("return_change", "{'old': 1}", "{'new': 1}"), BehaviorCategory.OUTPUT_CONTRACT),
        (
            delta("assignment_change", "state = old", "state = new"),
            BehaviorCategory.STATE_TRANSITION,
        ),
        (
            delta("assignment_change", "ROOTS = ('a',)", "ROOTS = ('a', 'b')"),
            BehaviorCategory.UNKNOWN,
        ),
        (delta("call_change", None, "notify(user)"), BehaviorCategory.SIDE_EFFECT),
        (delta("call_order_change", "first; second", "second; first"), BehaviorCategory.ORDERING),
        (
            delta("call_change", "client.old()", "client.new()"),
            BehaviorCategory.DEPENDENCY_INTERACTION,
        ),
        (delta("structural_refactor", "old_name", "new_name"), BehaviorCategory.REFACTOR),
        (delta("unknown_structure", "body", "body"), BehaviorCategory.UNKNOWN),
    ],
)
def test_complete_taxonomy_rules(change: StructuralDelta, category: BehaviorCategory) -> None:
    candidates = build_candidates([change])
    assert candidates[0].category is category
    assert candidates[0].evidence[0].id == "ev-001"
    complete_confidence = confidence_score(candidates[0])
    weakened = replace(
        candidates[0],
        evidence=(candidates[0].evidence[0].model_copy(update={"parser_complete": False}),),
        assumptions=(
            *candidates[0].assumptions,
            "An external runtime contract is unavailable.",
        ),
    )
    assert confidence_score(weakened, truncated=True) < complete_confidence


@pytest.mark.parametrize(
    ("kind", "ambiguous", "category", "summary", "impact"),
    [
        (
            "symbol_added",
            False,
            BehaviorCategory.UNKNOWN,
            "A callable or structural symbol was added.",
            "New behavior becomes reachable; its intended contract needs characterizing tests.",
        ),
        (
            "symbol_removed",
            False,
            BehaviorCategory.OUTPUT_CONTRACT,
            "A callable or structural symbol was removed.",
            "Code that still calls or imports it now fails instead of getting what it returned.",
        ),
        (
            "symbol_added",
            True,
            BehaviorCategory.UNKNOWN,
            "A callable or structural symbol was added without a reliable semantic match.",
            "Callers or observable behavior may change; the intended compatibility contract needs "
            "review.",
        ),
        (
            "symbol_removed",
            True,
            BehaviorCategory.UNKNOWN,
            "A callable or structural symbol was removed without a reliable semantic match.",
            "Callers or observable behavior may change; the intended compatibility contract needs "
            "review.",
        ),
    ],
)
def test_an_added_or_removed_symbol_is_worded_for_what_happened(
    kind: str, ambiguous: bool, category: BehaviorCategory, summary: str, impact: str
) -> None:
    """Every added symbol, a new file's included, read as a failed rename that broke callers.

    Only a near-equal symbol on the other side makes it "without a reliable semantic match", and
    only a removal, or a possible rename, has callers to break. What a removal's callers lose is
    what it gave them, an output contract change (`SDW-RULE-SYMBOL-REMOVED`), as a removed API
    operation is; one that may be a rename stays unknown.
    """
    change = StructuralDelta(**{**delta(kind, None, "(x)").__dict__, "metadata": {}})
    if ambiguous:
        change.metadata["ambiguous_match"] = True
    [candidate] = build_candidates([change])
    assert candidate.category is category
    assert (candidate.summary, candidate.observable_impact) == (summary, impact)
    assert len(candidate.assumptions) == (2 if ambiguous else 1)


def test_same_symbol_category_merges_evidence() -> None:
    candidates = build_candidates(
        [
            delta("comparison_change", "x < 1", "x <= 1"),
            delta("condition_change", "x < 1", "x <= 1"),
        ]
    )
    assert len(candidates) == 1
    assert [item.id for item in candidates[0].evidence] == ["ev-001", "ev-002"]


def test_overlapping_deterministic_and_llm_candidates_merge_evidence() -> None:
    deterministic = build_candidates([delta("comparison_change", "x < 1", "x <= 1")])[0]
    llm_supported = SemanticCandidate(
        path=deterministic.path,
        symbol=deterministic.symbol,
        category=deterministic.category,
        summary="The exact boundary is included.",
        observable_impact=deterministic.observable_impact,
        evidence=(
            deterministic.evidence[0],
            Evidence(id="ev-999", path="src/policy.py", symbol="Policy.apply", kind="condition"),
        ),
        confidence_baseline=0.97,
        origin=Origin.LLM_SUPPORTED,
        rule_ids=("SDW-LLM-SUPPORTED",),
        related_paths=frozenset({"src/policy.py"}),
    )
    merged = _deduplicate_candidates([deterministic, llm_supported])
    assert len(merged) == 1
    assert [item.id for item in merged[0].evidence] == ["ev-001", "ev-999"]
    assert merged[0].origin is Origin.LLM_SUPPORTED
    assert merged[0].summary == "The exact boundary is included."


def test_a_merge_keeps_the_retained_symbol_s_evidence_first() -> None:
    """The report reads a finding's symbol from its first evidence.

    In flask#5899 a model's claim on the removed `App.should_ignore_error` also cited the
    assignment that replaced it in `App`, an evidence ID lower than the method's. Merged into the
    method's deterministic finding and sorted by ID, the evidence began with the class's, and
    the finding moved to `App`, leaving the removed method with none.
    """
    removed = Evidence(
        id="ev-008", path="src/app.py", symbol="App.should_ignore_error", kind="symbol_removed"
    )
    assigned = Evidence(id="ev-007", path="src/app.py", symbol="App", kind="assignment_change")
    deterministic = SemanticCandidate(
        path="src/app.py",
        symbol="App.should_ignore_error",
        category=BehaviorCategory.UNKNOWN,
        summary="The method was removed.",
        observable_impact="Callers of the method fail.",
        evidence=(removed,),
        confidence_baseline=0.69,
    )
    claim = replace(
        deterministic,
        summary="The method was removed from App.",
        evidence=(removed, assigned),
        confidence_baseline=0.8,
        origin=Origin.LLM_SUPPORTED,
        rule_ids=("SDW-LLM-SUPPORTED",),
    )

    merged = _deduplicate_candidates([deterministic, claim])

    assert len(merged) == 1
    assert [item.id for item in merged[0].evidence] == ["ev-008", "ev-007"]
    assert evidence_identity(merged[0].evidence) == ("src/app.py", "App.should_ignore_error")
    assert merged[0].origin is Origin.LLM_SUPPORTED


def test_a_merge_still_orders_one_symbol_s_evidence_by_id() -> None:
    """Without a model all of a finding's evidence is its own symbol's, and stays in ID order."""
    existing = build_candidates([delta("comparison_change", "x < 1", "x <= 1")])[0]
    lower = existing.evidence[0].model_copy(update={"id": "ev-000"})
    incoming = replace(existing, evidence=(existing.evidence[0], lower))

    merged = _deduplicate_candidates([existing, incoming])

    assert [item.id for item in merged[0].evidence] == ["ev-000", "ev-001"]


def test_semantically_equal_candidate_impacts_deduplicate_without_shared_evidence() -> None:
    first = build_candidates([delta("comparison_change", "x < 1", "x <= 1")])[0]
    second = SemanticCandidate(
        path=first.path,
        symbol=first.symbol,
        category=first.category,
        summary=first.summary,
        observable_impact=f"  {first.observable_impact.upper()}  ",
        evidence=(Evidence(id="ev-999", path=first.path, symbol=first.symbol, kind="condition"),),
        confidence_baseline=first.confidence_baseline,
    )
    merged = _deduplicate_candidates([first, second])
    assert len(merged) == 1
    assert {item.id for item in merged[0].evidence} == {"ev-001", "ev-999"}


def test_name_signals_match_tokens_instead_of_arbitrary_substrings() -> None:
    assert (
        build_candidates([delta("call_change", None, "client.write_text(value)")])[0].category
        is BehaviorCategory.SIDE_EFFECT
    )
    assert (
        build_candidates([delta("call_change", None, "authorize_user(value)")])[0].category
        is BehaviorCategory.AUTHORIZATION
    )
    assert (
        build_candidates([delta("call_change", None, "rewrite_cache(value)")])[0].category
        is BehaviorCategory.DEPENDENCY_INTERACTION
    )
    assert (
        build_candidates([delta("call_change", None, "dispatcher(value)")])[0].category
        is BehaviorCategory.DEPENDENCY_INTERACTION
    )


def test_a_signature_change_of_typing_alone_says_so() -> None:
    """It asked callers to supply different arguments when only a return annotation changed."""
    change = StructuralDelta(
        **{
            **delta("signature_change", "(value: int) -> int", "(value: int) -> str").__dict__,
            "metadata": {"default_changed": False, "annotations_only": True},
        }
    )
    [candidate] = build_candidates([change])
    assert candidate.category is BehaviorCategory.OUTPUT_CONTRACT
    assert candidate.summary == (
        "Only the type annotations of a callable signature appear to have changed."
    )
    assert candidate.observable_impact.startswith("Callers pass the same arguments")
    assert candidate.assumptions


def _typing_only_signature() -> StructuralDelta:
    return StructuralDelta(
        **{
            **delta("signature_change", "(self) -> tuple | None", "(self) -> tuple").__dict__,
            "metadata": {"default_changed": False, "annotations_only": True},
        }
    )


@pytest.mark.parametrize("signature_first", [True, False])
def test_a_typing_only_signature_gives_way_to_the_change_beside_it(signature_first: bool) -> None:
    """Click's `Argument.get_help_record` stopped returning `None`, and narrowed its annotation.

    The signature came first, so its finding read "Only the type annotations ... appear to have
    changed" over the return change that made it, with an assumption about annotations that
    lowered its confidence.
    """
    returned = delta("return_change", "None; (name, help)", "(name, help or '')")
    changes = [_typing_only_signature(), returned]
    [candidate] = build_candidates(changes if signature_first else changes[::-1])
    assert candidate.category is BehaviorCategory.OUTPUT_CONTRACT
    assert candidate.summary == "A visible return expression or shape appears to have changed."
    assert candidate.assumptions == ()
    assert [item.kind for item in candidate.evidence] == [
        item.kind for item in (changes if signature_first else changes[::-1])
    ]
