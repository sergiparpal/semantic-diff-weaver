from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from semantic_diff_weaver.plugin import DESCRIPTION, SKELETONS_DESCRIPTION, register
from semantic_diff_weaver.schemas import (
    ANALYZE_SEMANTIC_DIFF_SCHEMA,
    GENERATE_TEST_SKELETONS_SCHEMA,
)

QUALIFIED_SKILL = "semantic-diff-weaver:review"


@dataclass
class FakeLlm:
    calls: int = 0

    def complete_structured(self, **kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError(kwargs)


@dataclass
class FakeContext:
    llm: FakeLlm = field(default_factory=FakeLlm)
    tools: list[dict[str, Any]] = field(default_factory=list)

    def register_tool(self, **kwargs: Any) -> None:
        self.tools.append(kwargs)


def test_registration_is_exactly_two_tools_and_side_effect_free() -> None:
    context = FakeContext()
    register(context)
    assert context.llm.calls == 0
    assert [tool["name"] for tool in context.tools] == [
        "analyze_semantic_diff",
        "generate_test_skeletons",
    ]
    assert [tool["schema"] for tool in context.tools] == [
        ANALYZE_SEMANTIC_DIFF_SCHEMA,
        GENERATE_TEST_SKELETONS_SCHEMA,
    ]
    for tool in context.tools:
        assert tool["toolset"] == "semantic_diff_weaver"
        assert tool["override"] is False
        assert "read-only" in tool["description"]
    # A host without plugin skills gets descriptions that name none.
    assert [tool["description"] for tool in context.tools] == [DESCRIPTION, SKELETONS_DESCRIPTION]


@dataclass
class SkillContext(FakeContext):
    """A host with `register_skill`, as every supported Hermes release is, from 0.14.0 on."""

    skills: list[dict[str, Any]] = field(default_factory=list)
    refusal: Exception | None = None

    def register_skill(self, name: str, path: Any, description: str = "") -> None:
        if self.refusal is not None:
            raise self.refusal
        self.skills.append({"name": name, "path": Path(path), "description": description})


def test_a_host_with_plugin_skills_gets_the_review_skill_and_tools_that_name_it() -> None:
    context = SkillContext()
    register(context)
    (skill,) = context.skills
    assert skill["name"] == "review"
    # All the system prompt's skill index shows, clipped past 60 characters.
    assert 0 < len(skill["description"]) <= 60
    text = skill["path"].read_text(encoding="utf-8")
    assert "`analyze_semantic_diff`" in text
    assert "`generate_test_skeletons`" in text
    assert "Do not run the code under review" in text
    assert 'output_format: "markdown"' in text
    for tool in context.tools:
        assert "read-only" in tool["description"]
        assert QUALIFIED_SKILL in tool["description"]
        assert QUALIFIED_SKILL in tool["schema"]["description"]
    # The schemas are shared with the MCP server, whose host has no such skill.
    assert QUALIFIED_SKILL not in ANALYZE_SEMANTIC_DIFF_SCHEMA["description"]
    assert QUALIFIED_SKILL not in GENERATE_TEST_SKELETONS_SCHEMA["description"]


@pytest.mark.parametrize(
    "refusal",
    [ValueError("already registered by another plugin"), FileNotFoundError("SKILL.md not found")],
)
def test_a_refused_skill_leaves_working_tools_that_name_no_skill(refusal: Exception) -> None:
    context = SkillContext(refusal=refusal)
    register(context)
    assert context.skills == []
    assert [tool["description"] for tool in context.tools] == [DESCRIPTION, SKELETONS_DESCRIPTION]
    assert [tool["schema"] for tool in context.tools] == [
        ANALYZE_SEMANTIC_DIFF_SCHEMA,
        GENERATE_TEST_SKELETONS_SCHEMA,
    ]


def test_the_skeleton_handler_accepts_extra_keywords_and_returns_json() -> None:
    context = FakeContext()
    register(context)
    result = context.tools[1]["handler"](
        {"repo_path": "path-that-does-not-exist", "obligation_ids": ["to-001"]}, task_id="task"
    )
    payload = json.loads(result)
    assert payload["success"] is False
    assert payload["error"] == "configuration_error"
    # The skeleton tool never asks the host's model for anything.
    assert context.llm.calls == 0


def test_handler_accepts_extra_runtime_keywords_and_returns_json() -> None:
    context = FakeContext()
    register(context)
    result = context.tools[0]["handler"](
        {"repo_path": "path-that-does-not-exist", "base_ref": "HEAD"}, task_id="task"
    )
    assert isinstance(result, str)
    assert json.loads(result)["success"] is False
