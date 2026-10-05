"""Stdio framing and start-up of the MCP server, without a subprocess."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from semantic_diff_weaver import mcp_server
from semantic_diff_weaver.mcp_server import (
    INVALID_REQUEST,
    MAX_MESSAGE_BYTES,
    PARSE_ERROR,
    PROJECT_DIR_ENV,
    StartupError,
    serve,
)


def _serve(raw: bytes) -> list[dict[str, Any]]:
    outbound = io.BytesIO()
    serve(io.BytesIO(raw), outbound)
    return [json.loads(line) for line in outbound.getvalue().splitlines()]


def _ping(request_id: int) -> bytes:
    return json.dumps({"jsonrpc": "2.0", "id": request_id, "method": "ping"}).encode() + b"\n"


def test_each_request_line_gets_one_response_line_in_order() -> None:
    responses = _serve(_ping(1) + _ping(2) + _ping(3))
    assert [item["id"] for item in responses] == [1, 2, 3]


def test_crlf_terminators_and_a_missing_final_newline_are_accepted() -> None:
    responses = _serve(_ping(1).replace(b"\n", b"\r\n") + _ping(2).rstrip(b"\n"))
    assert [item["id"] for item in responses] == [1, 2]


def test_a_notification_produces_no_output_line() -> None:
    notification = b'{"jsonrpc": "2.0", "method": "notifications/initialized"}\n'
    assert [item["id"] for item in _serve(notification + _ping(3))] == [3]


def test_blank_lines_are_skipped() -> None:
    assert [item["id"] for item in _serve(b"\n  \n" + _ping(4))] == [4]


@pytest.mark.parametrize("line", [b"{not json\n", b"\xff\xfe\n"])
def test_an_undecodable_line_is_a_parse_error_and_serving_continues(line: bytes) -> None:
    responses = _serve(line + _ping(5))
    assert responses[0] == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": PARSE_ERROR, "message": "Message is not valid UTF-8 JSON."},
    }
    assert responses[1]["id"] == 5


def test_an_oversized_line_is_rejected_and_the_next_message_is_still_read() -> None:
    oversized = b"x" * (MAX_MESSAGE_BYTES * 2 + 10) + b"\n"
    responses = _serve(oversized + _ping(6))
    assert responses[0]["error"]["code"] == INVALID_REQUEST
    assert responses[1]["id"] == 6


def test_an_oversized_unterminated_line_ends_cleanly() -> None:
    responses = _serve(b"x" * (MAX_MESSAGE_BYTES + 1))
    assert len(responses) == 1
    assert responses[0]["error"]["code"] == INVALID_REQUEST


def test_output_is_ascii_one_message_per_line() -> None:
    def handler(arguments: dict[str, Any]) -> str:
        return json.dumps({"success": True, "markdown": "a\nb ∅ —"}, ensure_ascii=False)

    outbound = io.BytesIO()
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call"}
    request["params"] = {"name": "analyze_semantic_diff", "arguments": {}}
    serve(io.BytesIO(json.dumps(request).encode()), outbound, mcp_server.McpServer(handler))
    raw = outbound.getvalue()
    assert raw.count(b"\n") == 1
    raw.decode("ascii")
    text = json.loads(raw)["result"]["content"][0]["text"]
    assert "∅" in json.loads(text)["markdown"]


def test_end_of_input_with_no_messages_writes_nothing() -> None:
    assert _serve(b"") == []


def test_main_rejects_arguments(capsys) -> None:
    assert mcp_server.main(["--port", "8080"]) == 2
    assert "takes no arguments" in capsys.readouterr().err


def test_main_refuses_an_inaccessible_project_dir(capsys, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(PROJECT_DIR_ENV, str(tmp_path / "missing"))
    assert mcp_server.main([]) == 2
    assert PROJECT_DIR_ENV in capsys.readouterr().err


def test_main_serves_stdio_and_keeps_stray_prints_off_stdout(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv(PROJECT_DIR_ENV, raising=False)
    stdout_bytes = io.BytesIO()
    stderr = io.StringIO()

    class _Stdin:
        buffer = io.BytesIO(_ping(7))

    class _Stdout(io.StringIO):
        buffer = stdout_bytes

    def noisy_serve(inbound: Any, outbound: Any) -> None:
        print("stray diagnostic")
        serve(inbound, outbound)

    monkeypatch.setattr(mcp_server.sys, "stdin", _Stdin())
    fake_stdout = _Stdout()
    monkeypatch.setattr(mcp_server.sys, "stdout", fake_stdout)
    monkeypatch.setattr(mcp_server.sys, "stderr", stderr)
    monkeypatch.setattr(mcp_server, "serve", noisy_serve)
    assert mcp_server.main([]) == 0
    assert json.loads(stdout_bytes.getvalue())["id"] == 7
    assert "stray diagnostic" in stderr.getvalue()
    assert fake_stdout.getvalue() == ""
    assert mcp_server.sys.stdout is fake_stdout


def test_entering_the_project_changes_the_working_directory(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(Path.cwd())
    mcp_server._enter_project({PROJECT_DIR_ENV: str(tmp_path)})
    assert Path.cwd().resolve() == tmp_path.resolve()


@pytest.mark.parametrize("environ", [{}, {PROJECT_DIR_ENV: ""}])
def test_without_a_project_dir_the_working_directory_is_kept(
    environ: dict[str, str], monkeypatch
) -> None:
    before = Path.cwd()
    monkeypatch.chdir(before)
    mcp_server._enter_project(environ)
    assert Path.cwd() == before


def test_a_project_dir_that_is_a_file_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("x", encoding="utf-8")
    before = Path.cwd()
    with pytest.raises(StartupError):
        mcp_server._enter_project({PROJECT_DIR_ENV: str(target)})
    assert Path.cwd() == before
