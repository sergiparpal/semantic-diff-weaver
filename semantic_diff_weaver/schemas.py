"""Hermes tool schemas and structured-LLM response schema."""

from __future__ import annotations

from typing import Any

from .models import LlmBatchResponse

# Annotated rather than inferred: without it the nested literal's value type collapses to
# a union of everything present, and a consumer indexing into `parameters` is rejected.
ANALYZE_SEMANTIC_DIFF_SCHEMA: dict[str, Any] = {
    "name": "analyze_semantic_diff",
    "description": (
        "Analyze the behavioral meaning of a bounded local Git diff and return evidence-backed, "
        "risk-ranked test obligations with unverified candidate existing tests. Call this when "
        "reviewing a change to Python code, to an OpenAPI, Swagger, or JSON Schema document, or "
        "to a configuration file the repository names, or planning regression tests. The tool is "
        "advisory and read-only; it does not execute, import, test, or modify repository code."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["repo_path", "base_ref"],
        "properties": {
            "repo_path": {"type": "string", "minLength": 1},
            "base_ref": {"type": "string", "minLength": 1},
            "head_ref": {"type": "string", "minLength": 1, "default": "HEAD"},
            "risk_profile": {"type": "string", "minLength": 1},
            "coverage_report": {"type": "string", "minLength": 1},
            "coverage_data": {"type": "string", "minLength": 1},
            "baseline_report": {"type": "string", "minLength": 1},
            "include": {"type": "array", "items": {"type": "string", "minLength": 1}},
            "exclude": {"type": "array", "items": {"type": "string", "minLength": 1}},
            "output_format": {
                "type": "string",
                "enum": ["json", "markdown", "both"],
                "default": "both",
            },
        },
    },
}

GENERATE_TEST_SKELETONS_SCHEMA: dict[str, Any] = {
    "name": "generate_test_skeletons",
    "description": (
        "Return a patch adding a failing test skeleton, pytest or unittest, for each test "
        "obligation the user approved by ID: at the end of the changed module's test file, or in "
        "that file created beside the obligation's first candidate test, or in the first test "
        "root without one. Name the analysis the IDs come from: pass the "
        "base_ref, head_ref, include, exclude, risk_profile, coverage_report, and coverage_data "
        "analyze_semantic_diff was given, to analyze again without inference, or analysis_path "
        "for an analysis saved as JSON. An analysis made with inference may number its "
        "obligations otherwise than one without, so pass analysis_path for such an analysis. "
        "The tool is read-only: it writes no file and runs nothing, and the user reviews and "
        "applies the patch."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["repo_path", "obligation_ids"],
        "properties": {
            "repo_path": {"type": "string", "minLength": 1},
            "obligation_ids": {
                "type": "array",
                "items": {"type": "string", "pattern": "^to-[0-9]{3,}$"},
                "minItems": 1,
                "maxItems": 100,
            },
            "framework": {"type": "string", "enum": ["pytest", "unittest"], "default": "pytest"},
            "analysis_path": {"type": "string", "minLength": 1},
            "base_ref": {"type": "string", "minLength": 1},
            # No default: a host that fills defaults in would send it beside an analysis_path.
            "head_ref": {"type": "string", "minLength": 1},
            "risk_profile": {"type": "string", "minLength": 1},
            "coverage_report": {"type": "string", "minLength": 1},
            "coverage_data": {"type": "string", "minLength": 1},
            "include": {"type": "array", "items": {"type": "string", "minLength": 1}},
            "exclude": {"type": "array", "items": {"type": "string", "minLength": 1}},
        },
    },
}

LLM_RESPONSE_SCHEMA = LlmBatchResponse.model_json_schema()
LLM_SCHEMA_NAME = "semantic_diff_batch_v1"
