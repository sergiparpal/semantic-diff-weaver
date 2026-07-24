from __future__ import annotations

from semantic_diff_weaver.models import AnalysisResult
from semantic_diff_weaver.service import analyze


def test_boundary_vertical_slice_produces_evidence_and_obligations(repo_factory) -> None:
    tests = """
from src.api import allowed

def test_allowed_boundary():
    assert not allowed(5)
"""
    repo, base, head = repo_factory(
        {
            "src/api.py": "def allowed(value):\n    return value < 5\n",
            "tests/test_api.py": tests,
        },
        {
            "src/api.py": "def allowed(value):\n    return value <= 5\n",
            "tests/test_api.py": tests,
        },
    )
    transport = analyze(
        {
            "repo_path": str(repo),
            "base_ref": base,
            "head_ref": head,
            "output_format": "json",
        }
    )
    result = AnalysisResult.model_validate(transport)
    boundary = [
        item for item in result.behavior_changes if item.category.value == "boundary_change"
    ]
    assert boundary
    assert boundary[0].evidence
    assert any(item.type.value == "boundary" for item in result.test_obligations)
    assert any(item.candidate_existing_tests for item in result.test_obligations)
    assert result.deterministic_mode is True


def test_default_and_exception_vertical_slice(repo_factory) -> None:
    repo, base, head = repo_factory(
        {
            "policy.py": (
                "def run(limit=2):\n"
                "    if limit < 0:\n"
                "        raise ValueError(limit)\n"
                "    return limit\n"
            )
        },
        {
            "policy.py": (
                "def run(limit=3):\n"
                "    if limit < 0:\n"
                "        raise TypeError(limit)\n"
                "    return limit\n"
            )
        },
    )
    result = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}
    )
    categories = {item["category"] for item in result["behavior_changes"]}
    assert "default_behavior_change" in categories
    assert "error_handling_change" in categories
