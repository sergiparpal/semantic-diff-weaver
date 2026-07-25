from __future__ import annotations

import pytest
from pydantic import ValidationError

from semantic_diff_weaver.models import (
    MAX_PATH_PATTERNS,
    AnalysisResult,
    AnalyzeRequest,
    BehaviorCategory,
    CandidateTest,
    ErrorResponse,
    Evidence,
    LineRange,
    WeaverConfig,
)


def test_request_defaults_and_unknown_field() -> None:
    request = AnalyzeRequest(repo_path=".", base_ref="main")
    assert request.head_ref == "HEAD"
    assert request.output_format.value == "both"
    with pytest.raises(ValidationError):
        AnalyzeRequest(repo_path=".", base_ref="main", surprise=True)
    with pytest.raises(ValidationError):
        AnalyzeRequest(repo_path=".", base_ref="main", include=["a.py"] * (MAX_PATH_PATTERNS + 1))


def test_taxonomy_is_exact() -> None:
    assert len(BehaviorCategory) == 13
    assert BehaviorCategory("boundary_change") is BehaviorCategory.BOUNDARY
    with pytest.raises(ValueError):
        BehaviorCategory("business_rule_change")


def test_line_ranges_are_ordered() -> None:
    assert LineRange(start=2, end=3).end == 3
    with pytest.raises(ValidationError):
        LineRange(start=3, end=2)


def test_config_defaults_validate() -> None:
    config = WeaverConfig()
    assert config.rules.max_llm_calls == 8
    assert config.privacy.allow_network is False


def test_candidate_tests_are_explicitly_unverified() -> None:
    candidate = CandidateTest(
        path="tests/test_api.py",
        symbol="test_api",
        match_score=0.5,
        match_reasons=["direct module or symbol import"],
    )
    assert candidate.verified is False
    with pytest.raises(ValidationError):
        CandidateTest(
            path="tests/test_api.py",
            symbol="test_api",
            match_score=0.5,
            match_reasons=["name"],
            verified=True,
        )


def test_transport_schemas_are_versioned() -> None:
    assert AnalysisResult.model_json_schema()["properties"]["schema_version"]["const"] == "1.1"
    assert ErrorResponse(success=False, error="invalid_ref", message="bad", remediation="fix")


@pytest.mark.parametrize(
    "path", ["../test_api.py", "/tmp/test_api.py", "C:/test_api.py", "a\\b.py"]
)
def test_output_paths_must_be_repository_relative_posix(path: str) -> None:
    with pytest.raises(ValidationError):
        Evidence(id="ev-001", path=path, kind="change")


def test_error_response_rejects_unknown_public_code() -> None:
    with pytest.raises(ValidationError):
        ErrorResponse(success=False, error="invented", message="bad", remediation="fix")


def test_analysis_summary_and_scope_invariants_are_enforced() -> None:
    payload = {
        "analysis_id": "sdw_test",
        "repository": {
            "base_ref": "base",
            "head_ref": "head",
            "base_commit": "a" * 40,
            "head_commit": "b" * 40,
        },
        "summary": {
            "changed_files": 1,
            "changed_symbols": 0,
            "behavior_changes": 0,
            "test_obligations": 0,
            "overall_risk": "low",
            "risk_score": 0,
            "overall_confidence": 1,
            "risk_counts": {
                "low": 0,
                "medium": 0,
                "high": 0,
                "critical": 0,
            },
        },
        "scope": {
            "changed_files_total": 1,
            "analyzed_files": ["src/a.py"],
            "changed_lines": 1,
            "changed_symbols": 0,
        },
        "behavior_changes": [],
        "test_obligations": [],
        "warnings": [],
        "limitations": [],
        "llm": {},
        "deterministic_mode": True,
    }
    assert AnalysisResult.model_validate(payload)
    payload["summary"]["changed_files"] = 2
    with pytest.raises(ValidationError, match="changed-file counts"):
        AnalysisResult.model_validate(payload)
