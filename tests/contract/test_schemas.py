from __future__ import annotations

from semantic_diff_weaver.schemas import (
    ANALYZE_SEMANTIC_DIFF_SCHEMA,
    LLM_RESPONSE_SCHEMA,
    LLM_SCHEMA_NAME,
)


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


def test_llm_schema_is_generated_from_bounded_model() -> None:
    assert LLM_SCHEMA_NAME == "semantic_diff_batch_v1"
    assert LLM_RESPONSE_SCHEMA["additionalProperties"] is False
    assert {"behaviors", "obligations"} <= LLM_RESPONSE_SCHEMA["properties"].keys()
