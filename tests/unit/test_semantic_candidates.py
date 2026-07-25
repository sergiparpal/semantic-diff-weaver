from __future__ import annotations

from dataclasses import replace

import pytest

from semantic_diff_weaver.ast_diff import StructuralDelta
from semantic_diff_weaver.models import BehaviorCategory, Evidence, LineRange, Origin
from semantic_diff_weaver.scoring import confidence_score
from semantic_diff_weaver.semantic_candidates import SemanticCandidate, build_candidates
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
        (delta("loop_change", "range(2)", "range(3)"), BehaviorCategory.RETRY_TIMEOUT),
        (delta("return_change", "{'old': 1}", "{'new': 1}"), BehaviorCategory.OUTPUT_CONTRACT),
        (
            delta("assignment_change", "state = old", "state = new"),
            BehaviorCategory.STATE_TRANSITION,
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
