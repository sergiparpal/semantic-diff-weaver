from __future__ import annotations

import pytest
from pydantic import ValidationError

from semantic_diff_weaver.models import (
    MAX_PATH_PATTERNS,
    AnalysisResult,
    AnalyzeRequest,
    BehaviorCategory,
    CandidateTest,
    ChangeGroup,
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
    assert AnalysisResult.model_json_schema()["properties"]["schema_version"]["const"] == "1.4"
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
            "verdict": "pass",
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


@pytest.mark.parametrize(
    ("change", "verdict", "valid"),
    [
        pytest.param({}, "pass", True, id="complete-and-passing"),
        pytest.param({"scope": {"truncated": True}}, "pass", False, id="unread-scope-cannot-pass"),
        pytest.param({"scope": {"truncated": True}}, "warn", True, id="unread-scope-can-warn"),
        pytest.param(
            {"repository": {"head_commit": "a" * 40}}, "pass", False, id="nothing-compared"
        ),
    ],
)
def test_an_analysis_that_left_scope_unread_never_passes(
    change: dict, verdict: str, valid: bool
) -> None:
    """A truncated scope, or a commit compared with itself, is not a review that found nothing."""
    payload = {
        "analysis_id": "sdw_test",
        "repository": {
            "base_ref": "base",
            "head_ref": "head",
            "base_commit": "a" * 40,
            "head_commit": "b" * 40,
            **change.get("repository", {}),
        },
        "summary": {
            "changed_files": 1,
            "changed_symbols": 0,
            "behavior_changes": 0,
            "test_obligations": 0,
            "overall_risk": "low",
            "risk_score": 0,
            "overall_confidence": 1,
            "risk_counts": {"low": 0, "medium": 0, "high": 0, "critical": 0},
            "verdict": verdict,
        },
        "scope": {
            "changed_files_total": 1,
            "analyzed_files": ["src/a.py"],
            "changed_lines": 1,
            "changed_symbols": 0,
            **change.get("scope", {}),
        },
        "behavior_changes": [],
        "test_obligations": [],
        "warnings": [],
        "limitations": [],
        "llm": {},
        "deterministic_mode": True,
    }
    if valid:
        assert AnalysisResult.model_validate(payload)
    else:
        with pytest.raises(ValidationError, match="cannot pass"):
            AnalysisResult.model_validate(payload)


def _written(members: list[str], *variables: dict) -> dict:
    """A group led by `checkout`, `bc-001`, reaching `members` and naming `variables`."""
    return {
        "id": "cg-001",
        "behavior_change_ids": members,
        "entry_points": ["bc-001"],
        "variables": list(variables),
    }


def _grouped(groups: list[dict]) -> dict:
    behaviors = [
        {
            "id": f"bc-00{index}",
            "category": "boundary_change",
            "summary": "Changed behavior.",
            "observable_impact": "An outcome appears to differ.",
            "risk": "medium",
            "risk_score": 50,
            "confidence": 0.8,
            "evidence": [
                {"id": f"ev-00{index}", "path": "src/a.py", "symbol": symbol, "kind": "x"}
            ],
            "origin": "deterministic",
            "score_explanation": {
                "behavioral_impact": 50,
                "critical_path_weight": 10,
                "test_gap_weight": 90,
                "change_surface_weight": 30,
            },
        }
        for index, symbol in ((1, "checkout"), (2, "parse_amount"), (3, "<module>"))
    ]
    return {
        "analysis_id": "sdw_test",
        "repository": {
            "base_ref": "base",
            "head_ref": "head",
            "base_commit": "a" * 40,
            "head_commit": "b" * 40,
        },
        "summary": {
            "changed_files": 1,
            "changed_symbols": 2,
            "behavior_changes": 3,
            "test_obligations": 0,
            "overall_risk": "medium",
            "risk_score": 50,
            "overall_confidence": 0.8,
            "risk_counts": {"low": 0, "medium": 3, "high": 0, "critical": 0},
            "verdict": "warn",
        },
        "scope": {
            "changed_files_total": 1,
            "analyzed_files": ["src/a.py"],
            "changed_lines": 2,
            "changed_symbols": 2,
        },
        "behavior_changes": behaviors,
        "test_obligations": [],
        "warnings": [],
        "limitations": [],
        "llm": {},
        "deterministic_mode": True,
        "change_groups": groups,
    }


@pytest.mark.parametrize(
    ("groups", "error"),
    [
        pytest.param([], None, id="none"),
        pytest.param(
            [
                {
                    "id": "cg-001",
                    "behavior_change_ids": ["bc-001", "bc-002"],
                    "entry_points": ["bc-001"],
                }
            ],
            None,
            id="one",
        ),
        pytest.param(
            [
                {
                    "id": "cg-001",
                    "behavior_change_ids": ["bc-001", "bc-009"],
                    "entry_points": ["bc-001"],
                }
            ],
            "distinct behavior changes of this result",
            id="unknown-member",
        ),
        pytest.param(
            [
                {
                    "id": "cg-001",
                    "behavior_change_ids": ["bc-001", "bc-001"],
                    "entry_points": ["bc-001"],
                }
            ],
            "distinct behavior changes of this result",
            id="repeated-member",
        ),
        pytest.param(
            [
                {
                    "id": "cg-001",
                    "behavior_change_ids": ["bc-001", "bc-002"],
                    "entry_points": ["bc-003"],
                }
            ],
            "entry points must be distinct members",
            id="entry-outside",
        ),
        pytest.param(
            [
                {
                    "id": "cg-001",
                    "behavior_change_ids": ["bc-001", "bc-002"],
                    "entry_points": ["bc-001"],
                },
                {
                    "id": "cg-001",
                    "behavior_change_ids": ["bc-001", "bc-002"],
                    "entry_points": ["bc-002"],
                },
            ],
            "change group IDs must be unique",
            id="repeated-id",
        ),
        pytest.param(
            [{"id": "cg-001", "behavior_change_ids": ["bc-001"], "entry_points": ["bc-001"]}],
            "at least 2 items",
            id="one-member",
        ),
        pytest.param(
            [
                {
                    "id": "group-1",
                    "behavior_change_ids": ["bc-001", "bc-002"],
                    "entry_points": ["bc-001"],
                }
            ],
            "pattern",
            id="malformed-id",
        ),
        pytest.param(
            [_written(["bc-001", "bc-003"], {"behavior_change_id": "bc-003", "names": ["LIMIT"]})],
            None,
            id="variables",
        ),
        pytest.param(
            [_written(["bc-001", "bc-003"], {"behavior_change_id": "bc-001", "names": ["LIMIT"]})],
            "distinct members that are no entry point",
            id="variables-of-an-entry-point",
        ),
        pytest.param(
            [_written(["bc-001", "bc-002"], {"behavior_change_id": "bc-003", "names": ["LIMIT"]})],
            "distinct members that are no entry point",
            id="variables-of-no-member",
        ),
        pytest.param(
            [
                _written(
                    ["bc-001", "bc-003"],
                    {"behavior_change_id": "bc-003", "names": ["LIMIT"]},
                    {"behavior_change_id": "bc-003", "names": ["RATE"]},
                )
            ],
            "distinct members that are no entry point",
            id="variables-repeated",
        ),
        pytest.param(
            [_written(["bc-001", "bc-002"], {"behavior_change_id": "bc-002", "names": ["LIMIT"]})],
            "variables must be of top-level code",
            id="variables-of-a-symbol",
        ),
        pytest.param(
            [_written(["bc-001", "bc-003"], {"behavior_change_id": "bc-003", "names": ["a b"]})],
            "distinct identifiers",
            id="variable-no-identifier",
        ),
        pytest.param(
            [_written(["bc-001", "bc-003"], {"behavior_change_id": "bc-003", "names": ["A", "A"]})],
            "distinct identifiers",
            id="variable-repeated",
        ),
        pytest.param(
            [_written(["bc-001", "bc-003"], {"behavior_change_id": "bc-003", "names": []})],
            "at least 1 item",
            id="no-variable",
        ),
    ],
)
def test_a_change_group_names_findings_of_its_own_analysis(
    groups: list[dict], error: str | None
) -> None:
    if error is None:
        assert AnalysisResult.model_validate(_grouped(groups)).change_groups == [
            ChangeGroup.model_validate(item) for item in groups
        ]
    else:
        with pytest.raises(ValidationError, match=error):
            AnalysisResult.model_validate(_grouped(groups))
