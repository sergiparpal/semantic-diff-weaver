from __future__ import annotations

from dataclasses import replace

import pytest

from semantic_diff_weaver.models import BehaviorCategory, Origin, WeaverConfig
from semantic_diff_weaver.semantic_candidates import SemanticCandidate
from semantic_diff_weaver.semantic_interpreter import interpret_candidates
from tests.contract.test_llm_call import (
    FakeLlm,
    Result,
    candidate,
    candidate_variant,
    valid_payload,
)


def test_fabricated_evidence_is_discarded() -> None:
    payload = valid_payload()
    payload["behaviors"][0]["evidence_ids"] = ["ev-999"]
    result = interpret_candidates([candidate()], FakeLlm([Result("json", payload)]), WeaverConfig())
    assert len(result.candidates) == 1
    assert any("fabricated evidence" in item for item in result.warnings)


@pytest.mark.parametrize("category", ["authorization_change", "unknown_semantic_change"])
def test_a_claim_in_a_category_no_finding_on_its_evidence_has_adds_no_finding(
    category: str,
) -> None:
    """click#3764: made an unknown change, such a claim was a second finding of one change."""
    payload = valid_payload()
    payload["behaviors"][0]["category"] = category
    result = interpret_candidates([candidate()], FakeLlm([Result("json", payload)]), WeaverConfig())
    # The structural rules' finding stands as they made it, and alone.
    assert result.candidates == [candidate()]
    # The claim's obligation still rests on its evidence, which that finding holds.
    assert [(item.evidence_ids, item.title) for item in result.suggestions] == [
        (("ev-001",), "Exercise five")
    ]
    assert any("Added no finding" in item for item in result.warnings)
    assert not any("Downgraded" in item for item in result.warnings)


def test_a_claim_across_findings_in_other_categories_adds_none() -> None:
    """flask#6168: one claim cited a return change and a call change, each a finding of its own."""
    beside = candidate_variant(id="ev-002", symbol="limit")
    payload = valid_payload()
    payload["behaviors"][0]["category"] = "authorization_change"
    payload["behaviors"][0]["evidence_ids"] = ["ev-001", "ev-002"]
    result = interpret_candidates(
        [candidate(), beside], FakeLlm([Result("json", payload)]), WeaverConfig()
    )
    assert result.candidates == [candidate(), beside]
    assert [item.evidence_ids for item in result.suggestions] == [("ev-001", "ev-002")]


def test_an_unsupported_category_on_an_unknown_change_s_evidence_supports_it() -> None:
    """Unknown is a category the finding on that evidence has, so the claim is made one."""
    unknown = replace(candidate(), category=BehaviorCategory.UNKNOWN)
    payload = valid_payload()
    payload["behaviors"][0]["category"] = "authorization_change"
    result = interpret_candidates([unknown], FakeLlm([Result("json", payload)]), WeaverConfig())
    assert [(item.category, item.origin) for item in result.candidates] == [
        (BehaviorCategory.UNKNOWN, Origin.LLM_SUPPORTED)
    ]
    assert any("Downgraded" in item for item in result.warnings)


def _refactor() -> SemanticCandidate:
    return replace(candidate(), category=BehaviorCategory.REFACTOR)


@pytest.mark.parametrize("category", ["boundary_change", "unknown_semantic_change"])
def test_a_finding_that_contradicts_a_refactor_is_discarded_with_its_obligation(
    category: str,
) -> None:
    payload = valid_payload()
    payload["behaviors"][0]["category"] = category
    result = interpret_candidates([_refactor()], FakeLlm([Result("json", payload)]), WeaverConfig())
    # The refactor stays as the structural rules found it, and nothing takes its place.
    assert [(item.category, item.origin) for item in result.candidates] == [
        (BehaviorCategory.REFACTOR, Origin.DETERMINISTIC)
    ]
    assert result.suggestions == []
    assert any("contradicts a finding of no behavior change" in item for item in result.warnings)
    assert result.status.available and result.status.failures == 0


def test_a_model_may_still_support_a_refactor() -> None:
    payload = valid_payload()
    payload["behaviors"][0]["category"] = "refactor_likely_no_behavior_change"
    result = interpret_candidates([_refactor()], FakeLlm([Result("json", payload)]), WeaverConfig())
    assert [(item.category, item.origin) for item in result.candidates] == [
        (BehaviorCategory.REFACTOR, Origin.LLM_SUPPORTED)
    ]


def test_a_refactor_s_evidence_is_set_aside_from_a_finding_that_also_cites_a_change() -> None:
    # Two symbols of one file share a model call: a refactor, and a boundary change beside it.
    changed = candidate_variant(id="ev-002", symbol="limit")
    payload = valid_payload()
    payload["behaviors"][0]["evidence_ids"] = ["ev-001", "ev-002"]
    result = interpret_candidates(
        [_refactor(), changed], FakeLlm([Result("json", payload)]), WeaverConfig()
    )
    assert any("Set aside evidence of no behavior change" in item for item in result.warnings)
    # The model's change rests on the changed symbol's evidence alone, so it supports that
    # finding instead of anchoring a new one on the refactor's symbol.
    assert {(item.symbol, item.category, item.origin) for item in result.candidates} == {
        ("allowed", BehaviorCategory.REFACTOR, Origin.DETERMINISTIC),
        ("limit", BehaviorCategory.BOUNDARY, Origin.LLM_SUPPORTED),
    }
    assert [item.evidence_ids for item in result.suggestions] == [("ev-002",)]


def test_unknown_taxonomy_fails_local_validation_and_preserves_deterministic() -> None:
    payload = valid_payload()
    payload["behaviors"][0]["category"] = "invented_change"
    result = interpret_candidates([candidate()], FakeLlm([Result("json", payload)]), WeaverConfig())
    assert len(result.candidates) == 1
    assert result.status.failures == 1
    assert result.candidates[0].category is BehaviorCategory.BOUNDARY


def test_evidence_from_another_batch_is_rejected_for_that_batch() -> None:
    first = candidate()
    second = candidate_variant(id="ev-002", path="src/second.py")
    payload = valid_payload()
    payload["behaviors"][0]["evidence_ids"] = ["ev-002"]
    result = interpret_candidates(
        [first, second],
        FakeLlm([Result("json", payload)]),
        WeaverConfig(),
    )
    assert any("outside its supplied batch" in item for item in result.warnings)


def test_per_symbol_behavior_cap_is_enforced() -> None:
    payload = valid_payload()
    payload["behaviors"] = payload["behaviors"] * 4
    result = interpret_candidates([candidate()], FakeLlm([Result("json", payload)]), WeaverConfig())
    assert any("per-symbol behavior cap" in item for item in result.warnings)


def test_duplicate_evidence_and_its_obligation_are_discarded_together() -> None:
    payload = valid_payload()
    payload["behaviors"][0]["evidence_ids"] = ["ev-001", "ev-001"]
    result = interpret_candidates([candidate()], FakeLlm([Result("json", payload)]), WeaverConfig())
    assert any("duplicate evidence" in item for item in result.warnings)
    assert result.suggestions == []


def test_invalid_and_excess_per_behavior_obligations_are_discarded() -> None:
    payload = valid_payload()
    payload["obligations"] = payload["obligations"] * 7
    invalid = dict(payload["obligations"][0])
    invalid["behavior_index"] = 99
    payload["obligations"].append(invalid)
    result = interpret_candidates([candidate()], FakeLlm([Result("json", payload)]), WeaverConfig())
    assert len(result.suggestions) == 6
    assert any("per-behavior cap" in item for item in result.warnings)
    assert any("invalid behavior index" in item for item in result.warnings)


def test_provider_generated_prose_is_redacted_before_output() -> None:
    payload = valid_payload()
    payload["behaviors"][0]["assumptions"] = ["CLIENT_SECRET = 'provider-secret-value-123456'"]
    payload["obligations"][0]["title"] = "Bearer provider-token-value-123456"
    result = interpret_candidates([candidate()], FakeLlm([Result("json", payload)]), WeaverConfig())
    combined = " ".join(
        [*result.candidates[0].assumptions, *(item.title for item in result.suggestions)]
    )
    assert "provider-secret-value" not in combined
    assert "provider-token-value" not in combined
    assert "[REDACTED]" in combined
