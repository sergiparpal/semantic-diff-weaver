from __future__ import annotations

from semantic_diff_weaver.models import BehaviorCategory, WeaverConfig
from semantic_diff_weaver.semantic_interpreter import interpret_candidates
from tests.contract.test_llm_call import FakeLlm, Result, candidate, valid_payload


def test_fabricated_evidence_is_discarded() -> None:
    payload = valid_payload()
    payload["behaviors"][0]["evidence_ids"] = ["ev-999"]
    result = interpret_candidates([candidate()], FakeLlm([Result("json", payload)]), WeaverConfig())
    assert len(result.candidates) == 1
    assert any("fabricated evidence" in item for item in result.warnings)


def test_unsupported_category_is_downgraded() -> None:
    payload = valid_payload()
    payload["behaviors"][0]["category"] = "authorization_change"
    result = interpret_candidates([candidate()], FakeLlm([Result("json", payload)]), WeaverConfig())
    assert any(item.category is BehaviorCategory.UNKNOWN for item in result.candidates)
    assert any("Downgraded" in item for item in result.warnings)


def test_unknown_taxonomy_fails_local_validation_and_preserves_deterministic() -> None:
    payload = valid_payload()
    payload["behaviors"][0]["category"] = "invented_change"
    result = interpret_candidates([candidate()], FakeLlm([Result("json", payload)]), WeaverConfig())
    assert len(result.candidates) == 1
    assert result.status.failures == 1
    assert result.candidates[0].category is BehaviorCategory.BOUNDARY


def test_evidence_from_another_batch_is_rejected_for_that_batch() -> None:
    first = candidate()
    second = candidate()
    second.evidence[0].id = "ev-002"
    second.evidence[0].path = "src/second.py"
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
