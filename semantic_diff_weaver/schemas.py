"""Hermes tool schema and structured-LLM response schema."""

from __future__ import annotations

from .models import LlmBatchResponse

ANALYZE_SEMANTIC_DIFF_SCHEMA = {
    "name": "analyze_semantic_diff",
    "description": (
        "Analyze the behavioral meaning of a bounded local Git diff and return evidence-backed, "
        "risk-ranked test obligations with unverified candidate existing tests. Call this when "
        "reviewing a Python code change or planning regression tests. The tool is advisory and "
        "read-only; it does not execute, import, test, or modify repository code."
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

LLM_RESPONSE_SCHEMA = LlmBatchResponse.model_json_schema()
LLM_SCHEMA_NAME = "semantic_diff_batch_v1"
