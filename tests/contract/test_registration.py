from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from semantic_diff_weaver.plugin import register


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


def test_registration_is_exactly_one_and_side_effect_free() -> None:
    context = FakeContext()
    register(context)
    assert context.llm.calls == 0
    assert len(context.tools) == 1
    tool = context.tools[0]
    assert tool["name"] == "analyze_semantic_diff"
    assert tool["toolset"] == "semantic_diff_weaver"
    assert tool["override"] is False


def test_handler_accepts_extra_runtime_keywords_and_returns_json() -> None:
    context = FakeContext()
    register(context)
    result = context.tools[0]["handler"](
        {"repo_path": "path-that-does-not-exist", "base_ref": "HEAD"}, task_id="task"
    )
    assert isinstance(result, str)
    assert json.loads(result)["success"] is False
