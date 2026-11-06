"""Hermes registration and JSON transport adapter."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .errors import as_public_error
from .llm_client import LlmClient
from .schemas import ANALYZE_SEMANTIC_DIFF_SCHEMA, GENERATE_TEST_SKELETONS_SCHEMA

logger = logging.getLogger(__name__)

DESCRIPTION = (
    "Analyze the behavioral meaning of a local Git diff and return evidence-backed, "
    "risk-ranked test obligations. This advisory, read-only tool never executes or modifies code."
)
SKELETONS_DESCRIPTION = (
    "Scaffold a failing test for each obligation the user approved from an analysis, as a patch "
    "the user reviews and applies. This read-only tool never writes, executes, or modifies code."
)

# Hermes 0.22 and later hide plugin tools until the model looks for them. Asked what a commit
# changes, gpt-6.1-terra never looked: it read the diff and executed both revisions instead. A
# plugin skill is listed in the system prompt, as `semantic-diff-weaver:review`, under this
# description, which recent releases clip to 60 characters.
SKILL_NAME = "review"
SKILL_DESCRIPTION = "What a commit or branch changes, and what to test"
SKILL_PATH = Path(__file__).with_name("hermes_skill.md")
# A model reads a tool's description before calling it, so each one names the skill exactly.
SKILL_POINTER = (
    " When and how to use the semantic-diff-weaver tools is in the skill "
    f"semantic-diff-weaver:{SKILL_NAME}: if it is not loaded in this conversation yet, load it "
    "first, by exactly that name."
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


def _register_skill(ctx: Any) -> bool:
    """Register the review skill where the host supports plugin skills; True when it did.

    Every Hermes release the plugin supports has `register_skill`, from 0.14.0 on. A host
    without it, or one that refuses the skill, gets the tools alone: they work without it, and
    their descriptions then name no skill that is not there.
    """
    register_skill = getattr(ctx, "register_skill", None)
    if register_skill is None:
        return False
    try:
        register_skill(SKILL_NAME, SKILL_PATH, description=SKILL_DESCRIPTION)
    except (OSError, ValueError) as exc:
        logger.warning("semantic-diff-weaver registered no skill: %s", exc)
        return False
    return True


def _pointed(schema: dict[str, Any], pointer: str) -> dict[str, Any]:
    """A copy of `schema` whose description ends with `pointer`; the shared schema is untouched."""
    if not pointer:
        return schema
    return {**schema, "description": str(schema.get("description", "")) + pointer}


def register(ctx: Any) -> None:
    """Register the plugin's two side-effect-free Hermes tools, and its skill where supported."""
    pointer = SKILL_POINTER if _register_skill(ctx) else ""

    def handler(args: dict[str, Any], **kwargs: Any) -> str:
        return handle_analyze_semantic_diff(args, llm=ctx.llm, **kwargs)

    ctx.register_tool(
        name="analyze_semantic_diff",
        toolset="semantic_diff_weaver",
        schema=_pointed(ANALYZE_SEMANTIC_DIFF_SCHEMA, pointer),
        handler=handler,
        description=DESCRIPTION + pointer,
        override=False,
    )
    ctx.register_tool(
        name="generate_test_skeletons",
        toolset="semantic_diff_weaver",
        schema=_pointed(GENERATE_TEST_SKELETONS_SCHEMA, pointer),
        handler=handle_generate_test_skeletons,
        description=SKELETONS_DESCRIPTION + pointer,
        override=False,
    )
