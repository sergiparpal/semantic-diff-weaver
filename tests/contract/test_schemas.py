from __future__ import annotations

from semantic_diff_weaver.models import BehaviorCategory
from semantic_diff_weaver.schemas import (
    ANALYZE_SEMANTIC_DIFF_SCHEMA,
    GENERATE_TEST_SKELETONS_SCHEMA,
    LLM_RESPONSE_SCHEMA,
    LLM_SCHEMA_NAME,
)
from semantic_diff_weaver.skeletons import (
    ANALYSIS_ARGUMENTS,
    MAX_SKELETONS,
    Framework,
    SkeletonRequest,
)
from semantic_diff_weaver.taxonomy import CATEGORY_PROFILES


def test_tool_schema_is_precise_and_closed() -> None:
    assert ANALYZE_SEMANTIC_DIFF_SCHEMA["name"] == "analyze_semantic_diff"
    parameters = ANALYZE_SEMANTIC_DIFF_SCHEMA["parameters"]
    assert parameters["required"] == ["repo_path", "base_ref"]
    assert parameters["additionalProperties"] is False
    assert set(parameters["properties"]["output_format"]["enum"]) == {
        "json",
        "markdown",
        "both",
    }
    assert "does not execute" in ANALYZE_SEMANTIC_DIFF_SCHEMA["description"]


def test_the_skeleton_schema_is_closed_and_is_the_request_model() -> None:
    """The schema a host sees and the model a call is validated by name the same fields."""
    assert GENERATE_TEST_SKELETONS_SCHEMA["name"] == "generate_test_skeletons"
    parameters = GENERATE_TEST_SKELETONS_SCHEMA["parameters"]
    assert parameters["additionalProperties"] is False
    assert parameters["required"] == ["repo_path", "obligation_ids"]
    assert set(parameters["properties"]) == set(SkeletonRequest.model_fields)
    assert {
        name for name, field in SkeletonRequest.model_fields.items() if field.is_required()
    } == set(parameters["required"])
    ids = parameters["properties"]["obligation_ids"]
    assert (ids["minItems"], ids["maxItems"]) == (1, MAX_SKELETONS)
    assert set(parameters["properties"]["framework"]["enum"]) == {item.value for item in Framework}
    # Every analysis argument the tool takes is one the analysis tool takes too.
    analyze = ANALYZE_SEMANTIC_DIFF_SCHEMA["parameters"]["properties"]
    for name in ANALYSIS_ARGUMENTS:
        assert parameters["properties"][name]["type"] == analyze[name]["type"]
    assert "read-only" in GENERATE_TEST_SKELETONS_SCHEMA["description"]


def test_llm_schema_is_generated_from_bounded_model() -> None:
    assert LLM_SCHEMA_NAME == "semantic_diff_batch_v1"
    assert LLM_RESPONSE_SCHEMA["additionalProperties"] is False
    assert {"behaviors", "obligations"} <= LLM_RESPONSE_SCHEMA["properties"].keys()


def test_every_behavior_category_has_a_complete_profile() -> None:
    """The taxonomy is only extensible if all three per-category tables grow together.

    Impact weight, obligation scenarios, and candidate-test terminology were three separate
    dictionaries indexed with a bare subscript; a new BehaviorCategory raised KeyError at
    analysis time in whichever module was reached first.
    """
    assert set(CATEGORY_PROFILES) == set(BehaviorCategory)
    for category, profile in CATEGORY_PROFILES.items():
        assert 0 <= profile.impact <= 100, category
        assert profile.scenarios, category
        assert profile.test_terms, category
        for scenario in profile.scenarios:
            assert scenario.title and scenario.given and scenario.when and scenario.then
            assert 0 <= scenario.relevance <= 100


def test_obligation_templates_are_reachable_for_every_category() -> None:
    """generate_obligations subscripts the registry directly, so coverage must be total."""
    for category in BehaviorCategory:
        assert CATEGORY_PROFILES[category].scenarios
