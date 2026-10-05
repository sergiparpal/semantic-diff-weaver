"""An oversized brief is shortened to fit the host instead of being saved where it cannot be read.

Claude Code saves a tool result over its threshold as a JSON file with the brief on one line,
which the model cannot read in parts. The server re-renders such a brief with shorter evidence
excerpts, keeping every finding, obligation, and ID.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from semantic_diff_weaver import mcp_server
from semantic_diff_weaver.mcp_server import TOOL_NAME, McpServer
from semantic_diff_weaver.service import analyze

LONG = " and ".join(f"value_{index} < limit_{index}" for index in range(40))
OLD = {"api.py": f"def check(x):\n    return {LONG} and x < 1\n"}
NEW = {"api.py": f"def check(x):\n    return {LONG} and x <= 1\n"}


def _call(server: McpServer, arguments: dict[str, Any]) -> str:
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": TOOL_NAME, "arguments": arguments},
        }
    )
    assert response is not None
    assert response["result"]["isError"] is False
    return response["result"]["content"][0]["text"]


@pytest.fixture
def change(repo_factory) -> dict[str, str]:
    repo, base, head = repo_factory(OLD, NEW)
    return {"repo_path": str(repo), "base_ref": base, "head_ref": head}


def test_an_oversized_brief_is_cut_to_fit_and_keeps_every_id(change, monkeypatch) -> None:
    full = analyze({**change, "output_format": "both"})
    budget = len(full["markdown"].encode()) - 50
    monkeypatch.setattr(mcp_server, "BRIEF_BUDGET_BYTES", budget)
    text = _call(McpServer(), change)
    brief = text.rsplit("\n\n<!-- analysis_id=", 1)[0]
    assert len(brief.encode()) <= budget
    assert "**Evidence excerpts:** cut to" in brief
    for obligation in full["analysis"]["test_obligations"]:
        assert f"(`{obligation['id']}`; covers" in brief
    assert text.splitlines()[-1].startswith("<!-- analysis_id=sdw_")


def test_a_brief_that_fits_is_returned_whole(change) -> None:
    text = _call(McpServer(), change)
    assert "**Evidence excerpts:**" not in text
    assert "````text" in text


def test_when_nothing_fits_the_smallest_brief_is_returned(change, monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "BRIEF_BUDGET_BYTES", 10)
    text = _call(McpServer(), change)
    assert "**Evidence excerpts:** omitted" in text
    assert "````text" not in text
    assert "- [ ] **P" in text


def test_a_brief_without_its_analysis_is_returned_unchanged(monkeypatch) -> None:
    """A handler that answers with only a brief cannot be re-rendered, so nothing is cut."""
    markdown = "## Semantic Diff Test Brief\n" + "x" * 500 + "\n"
    envelope = {
        "success": True,
        "schema_version": "1.1",
        "analysis_id": "sdw_1",
        "markdown": markdown,
    }
    monkeypatch.setattr(mcp_server, "BRIEF_BUDGET_BYTES", 10)
    server = McpServer(lambda arguments: json.dumps(envelope))
    text = _call(server, {"repo_path": "."})
    assert text.startswith(markdown.rstrip())
