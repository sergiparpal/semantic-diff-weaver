"""Hermes registration and JSON transport adapter."""

from __future__ import annotations

import json
from typing import Any

from .errors import as_public_error
from .llm_client import LlmClient
from .schemas import ANALYZE_SEMANTIC_DIFF_SCHEMA

DESCRIPTION = (
    "Analyze the behavioral meaning of a local Git diff and return evidence-backed, "
    "risk-ranked test obligations. This advisory, read-only tool never executes or modifies code."
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


def register(ctx: Any) -> None:
    """Register exactly one side-effect-free Hermes tool."""

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
