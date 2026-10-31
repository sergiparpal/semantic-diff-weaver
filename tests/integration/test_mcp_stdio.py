"""End-to-end MCP sessions against the real server process, as Claude Code starts it.

Claude Code launches a plugin's stdio server from a working directory it does not document and
exports the project root as `CLAUDE_PROJECT_DIR`. Every session here therefore starts the
server somewhere unrelated — standing in for the plugin installation directory — with the
repository passed only through that variable.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from semantic_diff_weaver.mcp_server import PROJECT_DIR_ENV, PROTOCOL_VERSIONS, TOOL_NAME
from semantic_diff_weaver.path_policy import ALLOWED_ROOTS_ENV

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BOUNDARY_OLD = {"api.py": "def allowed(x):\n    return x < 5\n"}
BOUNDARY_NEW = {"api.py": "def allowed(x):\n    return x <= 5\n"}


def run_session(
    messages: list[dict[str, Any]],
    *,
    cwd: Path,
    project: Path | None,
    allowed_roots: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    env = {key: value for key, value in os.environ.items() if key != ALLOWED_ROOTS_ENV}
    env.pop(PROJECT_DIR_ENV, None)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(REPOSITORY_ROOT), env.get("PYTHONPATH")]))
    if project is not None:
        env[PROJECT_DIR_ENV] = str(project)
    if allowed_roots is not None:
        env[ALLOWED_ROOTS_ENV] = allowed_roots
    stdin = "".join(json.dumps(message) + "\n" for message in messages)
    completed = subprocess.run(
        [sys.executable, "-m", "semantic_diff_weaver.mcp_server"],
        input=stdin.encode("utf-8"),
        capture_output=True,
        cwd=cwd,
        env=env,
        check=True,
        timeout=60,
        shell=False,
    )
    lines = completed.stdout.decode("ascii").splitlines()
    return [json.loads(line) for line in lines], completed.stderr.decode("utf-8", "replace")


def handshake() -> list[dict[str, Any]]:
    return [
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSIONS[0],
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ]


def call(arguments: dict[str, Any], request_id: int = 2) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": TOOL_NAME, "arguments": arguments},
    }


def tool_payload(response: dict[str, Any]) -> dict[str, Any]:
    return json.loads(response["result"]["content"][0]["text"])


def test_a_full_session_analyzes_the_project_by_relative_path(repo_factory, tmp_path) -> None:
    repo, base, head = repo_factory(BOUNDARY_OLD, BOUNDARY_NEW)
    plugin_root = tmp_path / "plugin-install"
    plugin_root.mkdir()
    messages = [
        *handshake(),
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        call({"repo_path": ".", "base_ref": base, "head_ref": head, "output_format": "json"}),
    ]
    responses, _ = run_session(messages, cwd=plugin_root, project=repo)
    assert [item["id"] for item in responses] == [0, 1, 2]
    assert responses[0]["result"]["protocolVersion"] == PROTOCOL_VERSIONS[0]
    assert [tool["name"] for tool in responses[1]["result"]["tools"]] == [TOOL_NAME]
    result = responses[2]["result"]
    assert result["isError"] is False
    payload = tool_payload(responses[2])
    assert payload["success"] is True
    assert payload["summary"]["overall_risk"] in {"low", "medium", "high", "critical"}


def test_the_session_runs_deterministically_even_with_an_api_key(
    repo_factory, tmp_path, monkeypatch
) -> None:
    """A host credential in the environment must not start a second, billed model."""
    repo, base, head = repo_factory(BOUNDARY_OLD, BOUNDARY_NEW)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    arguments = {"repo_path": ".", "base_ref": base, "head_ref": head, "output_format": "json"}
    responses, _ = run_session([*handshake(), call(arguments)], cwd=tmp_path, project=repo)
    payload = tool_payload(responses[1])
    assert payload["success"] is True
    assert payload["deterministic_mode"] is True


def test_the_default_result_is_the_plain_markdown_brief(repo_factory, tmp_path) -> None:
    """What Claude Code shows the model when it calls the tool without choosing a format."""
    repo, base, head = repo_factory(BOUNDARY_OLD, BOUNDARY_NEW)
    messages = [
        *handshake(),
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        call({"repo_path": ".", "base_ref": base, "head_ref": head}),
    ]
    responses, _ = run_session(messages, cwd=tmp_path, project=repo)
    tool = responses[1]["result"]["tools"][0]
    assert tool["inputSchema"]["properties"]["output_format"]["default"] == "markdown"
    assert tool["_meta"]["anthropic/maxResultSizeChars"] > 50_000
    result = responses[2]["result"]
    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert text.startswith("## Semantic Diff Test Brief")
    assert "**Overall risk:**" in text
    # Multi-line, so a host that saves an oversized result to a file can page through it.
    assert text.count("\n") > 5
    assert re.search(r"<!-- analysis_id=sdw_[A-Za-z0-9_-]+ schema_version=1\.2 -->\n\Z", text)
