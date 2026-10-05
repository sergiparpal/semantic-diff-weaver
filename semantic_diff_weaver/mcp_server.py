"""Model Context Protocol (stdio) transport: the Hermes tool, served to Claude Code.

This is the same single tool `plugin.py` registers with Hermes, spoken over MCP's stdio
transport — newline-delimited JSON-RPC 2.0 on stdin/stdout — so any MCP host, Claude Code
first among them, can call it. Only the subset a tool-only server needs is implemented:
`initialize`, `ping`, `tools/list`, and `tools/call`. Writing that subset by hand keeps the
base install at two runtime dependencies; the official SDK would add an HTTP stack this
transport never uses.

Authorization follows the Hermes path, not the CLI's. Under MCP a *model* chooses
`repo_path`, so the server never derives authorized roots from tool arguments: an unset
`SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS` means the process working directory, exactly as
`path_policy` defaults it. The one thing taken from the host is where that working directory
is — see `_enter_project`.

Inference is deliberately absent. The host model already interprets the result, so the
server runs the deterministic analysis only and never reads `ANTHROPIC_API_KEY` — a key
present for the host itself must not silently start a second, separately billed model.

Output is shaped for the host. Claude Code keeps a tool result in the conversation only up to
a character threshold and saves anything longer to a file, so this transport defaults
`output_format` to the Markdown brief, returns the brief as plain Markdown, and declares a
higher threshold for its tool. Everything else — error payloads and explicit `json` or `both`
results — is exactly what the Hermes handler returns.

Stdout belongs to the protocol. While serving, `sys.stdout` is pointed at stderr so a stray
`print` anywhere in the pipeline cannot corrupt the message stream.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from collections.abc import Callable, Mapping
from typing import Any, BinaryIO

from . import __version__
from .models import OutputFormat
from .plugin import DESCRIPTION, handle_analyze_semantic_diff
from .schemas import ANALYZE_SEMANTIC_DIFF_SCHEMA

SERVER_NAME = "semantic-diff-weaver"
PROJECT_DIR_ENV = "CLAUDE_PROJECT_DIR"
TOOL_NAME = ANALYZE_SEMANTIC_DIFF_SCHEMA["name"]

# Newest first. A client asking for one of these gets it echoed back; any other request is
# answered with the newest, and the specification leaves the decision to disconnect to the
# client. The tool-only subset served here is unchanged across all four revisions.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

# Generous for a tool call whose arguments are a few paths and patterns, and small enough
# that a runaway or hostile writer cannot make the server buffer without bound.
MAX_MESSAGE_BYTES = 1024 * 1024

# Claude Code saves a result longer than 50,000 characters to a file unless the tool declares
# its own threshold. Hermes's default, `both`, carries the canonical JSON *and* the brief:
# 158,368 characters for the 19-file range that exposed this, against 57,238 for the brief
# alone. So the brief is this transport's default.
DEFAULT_OUTPUT_FORMAT = OutputFormat.MARKDOWN.value
# Twice the host default and a fifth of its 500,000-character ceiling. It keeps that brief
# inline with room to spare, while the JSON of the same range (101,171 characters) still goes
# to a file — narrowing with `include` is the answer to that, not a higher threshold.
MAX_RESULT_CHARS = 100_000
RESULT_SIZE_META = "anthropic/maxResultSizeChars"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

INSTRUCTIONS = (
    "Call analyze_semantic_diff to review a committed Python change between two Git refs and "
    "plan regression tests. It is read-only and advisory. repo_path must lie inside the "
    "authorized workspace, which is the host project directory unless the operator "
    "set SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS."
)

OUTPUT_GUIDANCE = (
    "Without output_format it returns the Markdown brief as plain text. The canonical JSON is "
    "about twice that size, so request json or both only with include narrowed to a few files. "
    "Narrow with include, not exclude: exclude also removes files from candidate-test discovery."
)

Handler = Callable[[dict[str, Any]], str]


class StartupError(Exception):
    """The server cannot start in the environment it was given."""


class ProtocolError(Exception):
    """A request the server answers with a JSON-RPC error object."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def tool_definition() -> dict[str, Any]:
    """The MCP form of the Hermes schema: same name and input contract, the brief as default."""
    # A copy: the Hermes registration shares the original and keeps its own default.
    input_schema = copy.deepcopy(ANALYZE_SEMANTIC_DIFF_SCHEMA["parameters"])
    input_schema["properties"]["output_format"]["default"] = DEFAULT_OUTPUT_FORMAT
    return {
        "name": TOOL_NAME,
        "title": "Semantic diff analysis",
        "description": " ".join(
            (ANALYZE_SEMANTIC_DIFF_SCHEMA["description"], DESCRIPTION, OUTPUT_GUIDANCE)
        ),
        "inputSchema": input_schema,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "_meta": {RESULT_SIZE_META: MAX_RESULT_CHARS},
    }


def _deterministic_handler(arguments: dict[str, Any]) -> str:
    return handle_analyze_semantic_diff(arguments, llm=None)


class McpServer:
    """Dispatches one decoded JSON-RPC message at a time. Holds no per-session state."""

    def __init__(self, handler: Handler = _deterministic_handler) -> None:
        self._handler = handler

    def handle(self, message: Any) -> dict[str, Any] | None:
        """Return the response object, or None for a notification or a client response."""
        if not isinstance(message, dict):
            # JSON-RPC batches were removed from MCP in 2025-06-18; nothing here sends one.
            return _error(None, INVALID_REQUEST, "Expected a single JSON-RPC message object.")
        if "method" not in message:
            # A response to a request this server never makes. Nothing to answer.
            return None
        request_id = message.get("id")
        is_notification = "id" not in message
        if not is_notification and not _valid_id(request_id):
            return _error(None, INVALID_REQUEST, "The request id must be a string or integer.")
        if message.get("jsonrpc") != "2.0" or not isinstance(message["method"], str):
            return None if is_notification else _error(request_id, INVALID_REQUEST, "Invalid.")
        if is_notification:
            # `notifications/initialized` and `notifications/cancelled` need no action: every
            # call completes synchronously before the next message is read.
            return None
        params = message.get("params", {})
        try:
            if not isinstance(params, dict):
                raise ProtocolError(INVALID_PARAMS, "params must be an object.")
            result = self._dispatch(message["method"], params)
        except ProtocolError as exc:
            return _error(request_id, exc.code, exc.message)
        except Exception:
            # Same rule as every other transport: never echo an unrecognized exception.
            return _error(request_id, INTERNAL_ERROR, "The server failed to handle the request.")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            return _initialize(params)
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [tool_definition()]}
        if method == "tools/call":
            return self._call_tool(params)
        raise ProtocolError(METHOD_NOT_FOUND, f"Method not found: {method[:64]}")

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        if params.get("name") != TOOL_NAME:
            raise ProtocolError(INVALID_PARAMS, f"Unknown tool; the only tool is {TOOL_NAME}.")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ProtocolError(INVALID_PARAMS, "Tool arguments must be an object.")
        effective = {"output_format": DEFAULT_OUTPUT_FORMAT, **arguments}
        text = self._handler(effective)
        payload = json.loads(text)
        if payload.get("success") is False:
            # An analysis failure is a tool result, not a protocol error, so the model sees the
            # stable error code and remediation and can correct its arguments.
            return _text_result(text, is_error=True)
        if effective["output_format"] == OutputFormat.MARKDOWN.value and isinstance(
            payload.get("markdown"), str
        ):
            return _text_result(_brief(payload), is_error=False)
        return _text_result(text, is_error=False)


def _text_result(text: str, *, is_error: bool) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _brief(envelope: dict[str, Any]) -> str:
    """The brief as multi-line Markdown, closed by the envelope fields it does not show.

    Plain Markdown rather than the one-line JSON envelope: it is what the model reads, it is
    smaller without the escaping, and if the host still saves it to a file, that file can be
    read in parts — reads page by line, and a single-line JSON file is one unpageable line.
    The analysis ID is pattern-validated, so the closing comment cannot be broken out of.
    """
    footer = (
        f"<!-- analysis_id={envelope.get('analysis_id')} "
        f"schema_version={envelope.get('schema_version')} -->"
    )
    return f"{envelope['markdown'].rstrip()}\n\n{footer}\n"


def _valid_id(value: Any) -> bool:
    return isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool))


def _initialize(params: dict[str, Any]) -> dict[str, Any]:
    requested = params.get("protocolVersion")
    version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": __version__},
        "instructions": INSTRUCTIONS,
    }


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


_OVERSIZED = object()


def _read_message(stream: BinaryIO) -> bytes | object | None:
    """Return one line without its terminator, `_OVERSIZED`, or None at end of input."""
    line = stream.readline(MAX_MESSAGE_BYTES + 1)
    if not line:
        return None
    if len(line) > MAX_MESSAGE_BYTES:
        # Discard the remainder of the oversized line in bounded chunks so the next read
        # starts on a message boundary.
        while line and not line.endswith(b"\n"):
            line = stream.readline(MAX_MESSAGE_BYTES)
        return _OVERSIZED
    return line.rstrip(b"\r\n")


def _decode(line: bytes | object) -> tuple[Any, dict[str, Any] | None]:
    """Return the decoded message, or the error response that replaces it."""
    if not isinstance(line, bytes):
        return None, _error(None, INVALID_REQUEST, "Message exceeds the size limit.")
    try:
        return json.loads(line.decode("utf-8")), None
    except (UnicodeDecodeError, ValueError):
        return None, _error(None, PARSE_ERROR, "Message is not valid UTF-8 JSON.")


def _write(stream: BinaryIO, payload: dict[str, Any]) -> None:
    # `ensure_ascii` keeps every message on one line whatever the analysis text contains.
    stream.write(json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("ascii") + b"\n")
    stream.flush()


def serve(inbound: BinaryIO, outbound: BinaryIO, server: McpServer | None = None) -> None:
    """Answer messages until the client closes the input stream."""
    active = server or McpServer()
    while (line := _read_message(inbound)) is not None:
        if isinstance(line, bytes) and not line.strip():
            continue
        message, failure = _decode(line)
        response = failure if failure is not None else active.handle(message)
        if response is not None:
            _write(outbound, response)


def _enter_project(environ: Mapping[str, str]) -> None:
    """Make the host's project the working directory the analysis resolves against.

    Claude Code does not document the working directory it starts a plugin server in, but it
    does export the project root as `CLAUDE_PROJECT_DIR`. Entering it makes a relative
    `repo_path` such as "." mean the project, and makes `path_policy`'s default authorized
    root the project rather than wherever the plugin happens to be installed. The value comes
    from the host, never from a tool argument, and an operator-set
    `SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS` still takes precedence over it.
    """
    project = environ.get(PROJECT_DIR_ENV)
    if not project:
        return
    try:
        os.chdir(project)
    except OSError as exc:
        raise StartupError(
            f"{PROJECT_DIR_ENV} {project!r} is not an accessible directory."
        ) from exc


def main(argv: list[str] | None = None) -> int:
    """Serve on the process stdio. Takes no options; configuration is the environment."""
    arguments = sys.argv[1:] if argv is None else argv
    if arguments:
        print(f"{SERVER_NAME}-mcp: takes no arguments; got {arguments[0]!r}.", file=sys.stderr)
        return 2
    try:
        _enter_project(os.environ)
    except StartupError as exc:
        print(f"{SERVER_NAME}-mcp: {exc}", file=sys.stderr)
        return 2
    inbound, outbound = sys.stdin.buffer, sys.stdout.buffer
    original_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        serve(inbound, outbound)
    except KeyboardInterrupt:  # pragma: no cover - interactive shutdown
        pass
    finally:
        sys.stdout = original_stdout
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
