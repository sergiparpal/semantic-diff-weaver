"""Hermes registration and JSON transport adapter."""

from __future__ import annotations

import json
from typing import Any

from .errors import as_public_error
from .llm_client import LlmClient
from .schemas import ANALYZE_SEMANTIC_DIFF_SCHEMA, GENERATE_TEST_SKELETONS_SCHEMA

DESCRIPTION = (
    "Analyze the behavioral meaning of a local Git diff and return evidence-backed, "
    "risk-ranked test obligations. This advisory, read-only tool never executes or modifies code."
)
SKELETONS_DESCRIPTION = (
    "Scaffold a failing test for each obligation the user approved from an analysis, as a patch "
    "the user reviews and applies. This read-only tool never writes, executes, or modifies code."
)


def handle_analyze_semantic_diff(
    args: dict[str, Any], *, llm: LlmClient | None = None, **kwargs: Any
) -> str:
    """Run analysis and convert every result or expected failure to valid JSON."""
    del kwargs
    try:
        from .service import analyze

        result = analyze(args, llm=llm)
        return json.dumps(result, ensure_ascii=False, sort_keys=True)
    except Exception as exc:
        return json.dumps(as_public_error(exc), ensure_ascii=False, sort_keys=True)


def handle_generate_test_skeletons(args: dict[str, Any], **kwargs: Any) -> str:
    """Generate the skeleton patch and convert every result or expected failure to valid JSON.

    No inference: an analysis run again is the deterministic one, whose obligation IDs are the
    same on every run, as the tool's description says.
    """
    del kwargs
    try:
        from .skeletons import generate_skeletons

        result = generate_skeletons(args)
        return json.dumps(result, ensure_ascii=False, sort_keys=True)
    except Exception as exc:
        return json.dumps(as_public_error(exc), ensure_ascii=False, sort_keys=True)


def register(ctx: Any) -> None:
    """Register the plugin's two side-effect-free Hermes tools."""

    def handler(args: dict[str, Any], **kwargs: Any) -> str:
        return handle_analyze_semantic_diff(args, llm=ctx.llm, **kwargs)

    ctx.register_tool(
        name="analyze_semantic_diff",
        toolset="semantic_diff_weaver",
        schema=ANALYZE_SEMANTIC_DIFF_SCHEMA,
        handler=handler,
        description=DESCRIPTION,
        override=False,
    )
    ctx.register_tool(
        name="generate_test_skeletons",
        toolset="semantic_diff_weaver",
        schema=GENERATE_TEST_SKELETONS_SCHEMA,
        handler=handle_generate_test_skeletons,
        description=SKELETONS_DESCRIPTION,
        override=False,
    )
