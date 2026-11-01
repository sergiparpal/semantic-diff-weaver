from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from semantic_diff_weaver.plugin import register
from semantic_diff_weaver.schemas import (
    ANALYZE_SEMANTIC_DIFF_SCHEMA,
    GENERATE_TEST_SKELETONS_SCHEMA,
)


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
