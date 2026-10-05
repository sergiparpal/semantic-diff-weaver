"""The MCP transport serves the Hermes tool contract over JSON-RPC 2.0.

The one deliberate difference is output shaping for the host: the brief is the default and is
returned as plain Markdown. Every other request and result is the Hermes handler's own.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from semantic_diff_weaver import __version__
from semantic_diff_weaver.mcp_server import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    MAX_RESULT_CHARS,
    METHOD_NOT_FOUND,
    PROTOCOL_VERSIONS,
    RESULT_SIZE_META,
    TOOL_NAME,
    McpServer,
    tool_definition,
)
from semantic_diff_weaver.schemas import ANALYZE_SEMANTIC_DIFF_SCHEMA


def _request(method: str, params: Any = None, request_id: Any = 1) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def _call(arguments: Any, name: str = TOOL_NAME) -> dict[str, Any]:
    return _request("tools/call", {"name": name, "arguments": arguments})


BRIEF_ENVELOPE = {
    "success": True,
    "schema_version": "1.1",
    "analysis_id": "sdw_0123abc",
    "markdown": "## Semantic Diff Test Brief\n\n**Overall risk:** medium\n",
}


def _recording_handler(seen: list[dict[str, Any]], payload: dict[str, Any]) -> Any:
    def handler(arguments: dict[str, Any]) -> str:
        seen.append(arguments)
        return json.dumps(payload)

    return handler


def test_the_tool_definition_is_the_hermes_schema_with_the_brief_as_default() -> None:
    tool = tool_definition()
    assert tool["name"] == ANALYZE_SEMANTIC_DIFF_SCHEMA["name"] == "analyze_semantic_diff"
    expected = json.loads(json.dumps(ANALYZE_SEMANTIC_DIFF_SCHEMA["parameters"]))
    expected["properties"]["output_format"]["default"] = "markdown"
    assert tool["inputSchema"] == expected
    assert tool["annotations"]["readOnlyHint"] is True
    assert tool["annotations"]["destructiveHint"] is False


def test_the_hermes_schema_keeps_its_own_default() -> None:
    """The MCP default is set on a copy; Hermes registration still advertises `both`."""
    tool_definition()
    tool_definition()["inputSchema"]["properties"]["output_format"]["default"] = "json"
    output_format = ANALYZE_SEMANTIC_DIFF_SCHEMA["parameters"]["properties"]["output_format"]
    assert output_format["default"] == "both"


def test_the_tool_declares_a_result_threshold_between_the_host_default_and_ceiling() -> None:
    assert tool_definition()["_meta"] == {RESULT_SIZE_META: MAX_RESULT_CHARS}
    assert 50_000 < MAX_RESULT_CHARS <= 500_000


def test_tools_list_returns_exactly_one_tool() -> None:
    response = McpServer().handle(_request("tools/list", {}))
    assert response is not None
    assert response["id"] == 1
    assert response["result"]["tools"] == [tool_definition()]


@pytest.mark.parametrize("version", PROTOCOL_VERSIONS)
def test_initialize_echoes_a_supported_protocol_version(version: str) -> None:
    response = McpServer().handle(_request("initialize", {"protocolVersion": version}))
    assert response is not None
    result = response["result"]
    assert result["protocolVersion"] == version
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"] == {"name": "semantic-diff-weaver", "version": __version__}


def test_initialize_answers_an_unknown_version_with_the_newest() -> None:
    response = McpServer().handle(_request("initialize", {"protocolVersion": "1999-01-01"}))
    assert response is not None
    assert response["result"]["protocolVersion"] == PROTOCOL_VERSIONS[0]


def test_ping_returns_an_empty_result() -> None:
    assert McpServer().handle(_request("ping", request_id="abc")) == {
        "jsonrpc": "2.0",
        "id": "abc",
        "result": {},
    }


def test_a_call_without_a_format_asks_for_the_brief_and_returns_it_as_markdown() -> None:
    seen: list[dict[str, Any]] = []
    server = McpServer(_recording_handler(seen, BRIEF_ENVELOPE))
    response = server.handle(_call({"repo_path": ".", "base_ref": "main"}))
    assert response is not None
    # The brief is requested with its analysis beside it, so an oversized one can be re-rendered.
    assert seen == [{"repo_path": ".", "base_ref": "main", "output_format": "both"}]
    result = response["result"]
    assert result["isError"] is False
    assert result["content"] == [
        {
            "type": "text",
            "text": "## Semantic Diff Test Brief\n\n**Overall risk:** medium\n\n"
            "<!-- analysis_id=sdw_0123abc schema_version=1.1 -->\n",
        }
    ]


def test_an_explicit_markdown_request_is_also_returned_as_markdown() -> None:
    server = McpServer(_recording_handler([], BRIEF_ENVELOPE))
    response = server.handle(_call({"repo_path": ".", "output_format": "markdown"}))
    assert response is not None
    assert response["result"]["content"][0]["text"].startswith("## Semantic Diff Test Brief")


@pytest.mark.parametrize("output_format", ["json", "both"])
def test_an_explicit_json_format_is_the_hermes_payload_unchanged(output_format: str) -> None:
    seen: list[dict[str, Any]] = []
    payload = {"success": True, "analysis": {"analysis_id": "sdw_1"}, "markdown": "# brief"}
    server = McpServer(_recording_handler(seen, payload))
    response = server.handle(_call({"repo_path": ".", "output_format": output_format}))
    assert response is not None
    assert seen == [{"repo_path": ".", "output_format": output_format}]
    result = response["result"]
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"]) == payload


def test_a_brief_request_without_a_brief_is_returned_unchanged() -> None:
    """Defensive: only a real brief is unwrapped; anything else passes through as JSON."""
    payload = {"success": True, "analysis_id": "sdw_2", "markdown": None}
    response = McpServer(_recording_handler([], payload)).handle(_call({"repo_path": "."}))
    assert response is not None
    assert json.loads(response["result"]["content"][0]["text"]) == payload


def test_an_analysis_failure_is_an_error_result_with_the_stable_payload() -> None:
    """The default handler, unfaked: a bad path comes back as a tool result, not a crash."""
    response = McpServer().handle(_call({"repo_path": "does-not-exist", "base_ref": "HEAD"}))
    assert response is not None
    result = response["result"]
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["success"] is False
    assert set(payload) == {"success", "error", "message", "remediation"}


def test_missing_arguments_get_only_the_transport_default() -> None:
    """No arguments means the default brief, requested as `both` to keep the analysis at hand."""
    seen: list[dict[str, Any]] = []
    server = McpServer(_recording_handler(seen, {"success": False}))
    server.handle(_request("tools/call", {"name": TOOL_NAME}))
    assert seen == [{"output_format": "both"}]


@pytest.mark.parametrize(
    ("message", "code"),
    [
        (_call({}, name="run_shell"), INVALID_PARAMS),
        (_call(["not", "an", "object"]), INVALID_PARAMS),
        (_request("tools/call", ["positional"]), INVALID_PARAMS),
        (_request("resources/list", {}), METHOD_NOT_FOUND),
        ({"jsonrpc": "1.0", "id": 1, "method": "ping"}, INVALID_REQUEST),
        ({"jsonrpc": "2.0", "id": 1, "method": 7}, INVALID_REQUEST),
    ],
)
def test_protocol_violations_are_json_rpc_errors(message: dict[str, Any], code: int) -> None:
    response = McpServer().handle(message)
    assert response is not None
    assert response["id"] == 1
    assert response["error"]["code"] == code
    assert "result" not in response


@pytest.mark.parametrize("request_id", [None, True, 1.5, {"nested": 1}, [1]])
def test_an_invalid_request_id_is_rejected_with_a_null_id(request_id: Any) -> None:
    response = McpServer().handle(_request("ping", request_id=request_id))
    assert response is not None
    assert response["id"] is None
    assert response["error"]["code"] == INVALID_REQUEST


@pytest.mark.parametrize("message", [["batch"], "text", 5, None])
def test_a_non_object_message_is_an_invalid_request(message: Any) -> None:
    response = McpServer().handle(message)
    assert response is not None
    assert response["error"]["code"] == INVALID_REQUEST


@pytest.mark.parametrize(
    "message",
    [
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}},
        {"jsonrpc": "1.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 9, "result": {}},
    ],
)
def test_notifications_and_client_responses_are_never_answered(message: Any) -> None:
    assert McpServer().handle(message) is None


def test_an_unexpected_failure_never_echoes_its_text() -> None:
    def handler(arguments: dict[str, Any]) -> str:
        raise RuntimeError("secret /home/operator/.ssh/id_rsa")

    response = McpServer(handler).handle(_call({}))
    assert response is not None
    assert response["error"]["code"] == INTERNAL_ERROR
    assert "secret" not in json.dumps(response)
