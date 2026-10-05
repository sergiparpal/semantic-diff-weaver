"""The MCP transport serves the Hermes tool contract unchanged, over JSON-RPC 2.0."""

from __future__ import annotations

import json
from typing import Any

import pytest

from semantic_diff_weaver import __version__
from semantic_diff_weaver.mcp_server import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PROTOCOL_VERSIONS,
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


def test_the_tool_definition_is_the_hermes_schema() -> None:
    tool = tool_definition()
    assert tool["name"] == ANALYZE_SEMANTIC_DIFF_SCHEMA["name"] == "analyze_semantic_diff"
    assert tool["inputSchema"] == ANALYZE_SEMANTIC_DIFF_SCHEMA["parameters"]
    assert tool["annotations"]["readOnlyHint"] is True
    assert tool["annotations"]["destructiveHint"] is False


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


def test_a_successful_call_is_not_an_error_result() -> None:
    seen: list[dict[str, Any]] = []

    def handler(arguments: dict[str, Any]) -> str:
        seen.append(arguments)
        return json.dumps({"success": True, "analysis": {}})

    response = McpServer(handler).handle(_call({"repo_path": ".", "base_ref": "main"}))
    assert response is not None
    assert seen == [{"repo_path": ".", "base_ref": "main"}]
    result = response["result"]
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"])["success"] is True


def test_an_analysis_failure_is_an_error_result_with_the_stable_payload() -> None:
    """The default handler, unfaked: a bad path comes back as a tool result, not a crash."""
    response = McpServer().handle(_call({"repo_path": "does-not-exist", "base_ref": "HEAD"}))
    assert response is not None
    result = response["result"]
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["success"] is False
    assert set(payload) == {"success", "error", "message", "remediation"}


def test_missing_arguments_default_to_an_empty_object() -> None:
    seen: list[dict[str, Any]] = []

    def handler(arguments: dict[str, Any]) -> str:
        seen.append(arguments)
        return json.dumps({"success": False})

    McpServer(handler).handle(_request("tools/call", {"name": TOOL_NAME}))
    assert seen == [{}]


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
