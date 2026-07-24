"""Hermes registration and JSON transport adapter."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from .errors import ErrorCode, WeaverError, internal_error
from .schemas import ANALYZE_SEMANTIC_DIFF_SCHEMA

DESCRIPTION = (
    "Analyze the behavioral meaning of a local Git diff and return evidence-backed, "
    "risk-ranked test obligations. This advisory, read-only tool never executes or modifies code."
)


def handle_analyze_semantic_diff(args: dict[str, Any], *, llm: Any = None, **kwargs: Any) -> str:
    """Run analysis and convert every result or expected failure to valid JSON."""
    del kwargs
    try:
        from .service import analyze

        result = analyze(args, llm=llm)
        return json.dumps(result, ensure_ascii=False, sort_keys=True)
    except WeaverError as exc:
        return json.dumps(exc.as_dict(), ensure_ascii=False, sort_keys=True)
    except ValidationError:
        error = WeaverError(
            ErrorCode.CONFIGURATION_ERROR,
            "The tool arguments or generated result failed schema validation.",
            "Check required fields, output_format, include/exclude patterns, and configuration values.",
        )
        return json.dumps(error.as_dict(), ensure_ascii=False, sort_keys=True)
    except Exception:
        return json.dumps(internal_error().as_dict(), ensure_ascii=False, sort_keys=True)


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
