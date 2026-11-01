"""Model Context Protocol (stdio) transport: the Hermes tools, served to Claude Code.

These are the same two tools `plugin.py` registers with Hermes, spoken over MCP's stdio
transport — newline-delimited JSON-RPC 2.0 on stdin/stdout — so any MCP host, Claude Code
first among them, can call them. Only the subset a tool-only server needs is implemented:
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
`output_format` to the Markdown brief, returns the brief as plain Markdown, declares a higher
threshold for its tool, and shortens a brief's evidence excerpts when it would still not fit.
Everything else — error payloads, explicit `json` or `both` results, and the skeleton tool's
result — is exactly what the Hermes handler returns.

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
from .models import AnalysisResult, OutputFormat
from .plugin import (
    DESCRIPTION,
    SKELETONS_DESCRIPTION,
    handle_analyze_semantic_diff,
    handle_generate_test_skeletons,
)
from .renderer import render_markdown
from .schemas import ANALYZE_SEMANTIC_DIFF_SCHEMA, GENERATE_TEST_SKELETONS_SCHEMA

SERVER_NAME = "semantic-diff-weaver"
PROJECT_DIR_ENV = "CLAUDE_PROJECT_DIR"
TOOL_NAME = ANALYZE_SEMANTIC_DIFF_SCHEMA["name"]
SKELETONS_TOOL_NAME = GENERATE_TEST_SKELETONS_SCHEMA["name"]

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
# A brief over budget is rendered again with shorter evidence excerpts, because Claude Code saves
# an oversized result as a JSON file with the whole brief on one line: unreadable in parts, and
# reachable only through shell tools that need approval. The budget is in UTF-8 bytes, never
# fewer than characters, and under the declared threshold, so the brief stays in the conversation
# however the host counts.
BRIEF_BUDGET_BYTES = MAX_RESULT_CHARS * 95 // 100
# Tried in order until a brief fits: characters kept from each side of each evidence excerpt,
# then none. Findings, obligations, and every ID survive every step. The ladder is fine-grained
# because the largest excerpt that fits is the most useful: on this repository's 122-item
# release range, 120 characters missed the budget by about 3 KB and 80 fit.
EXCERPT_STEPS = (400, 200, 120, 80, 40, 0)

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

INSTRUCTIONS = (
    "Call analyze_semantic_diff to review a committed change to Python code or API schemas "
    "between two Git refs and plan regression tests. It is read-only and advisory. Once the user approves obligations, "
    "generate_test_skeletons returns a patch of failing tests for them, which it never applies. "
    "repo_path must lie inside the authorized workspace, which is the host project directory "
    "unless the operator set SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS."
)

OUTPUT_GUIDANCE = (
    "Without output_format it returns the Markdown brief as plain text, with every finding and "
    "obligation ID; a brief too large for the host has its evidence excerpts shortened. The "
    "canonical JSON is about twice the size, so request json or both only with include narrowed "
    "to a few files. Narrow with include, not exclude: exclude also removes files from "
    "candidate-test discovery."
)

SKELETONS_GUIDANCE = (
    "Every analysis this server runs is deterministic, so the arguments an analyze_semantic_diff "
    "call was given scaffold the obligations its brief lists, under the same IDs. Only scaffold "
    "obligations the user approved. The result is JSON: the patch, where each test went, and "
    "why any obligation got none; show the patch, and apply it only if the user asks."
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


def skeletons_tool_definition() -> dict[str, Any]:
    """The MCP form of the Hermes skeleton schema, unchanged but for the guidance it adds."""
    return {
        "name": SKELETONS_TOOL_NAME,
        "title": "Test skeletons",
        "description": " ".join(
            (
                GENERATE_TEST_SKELETONS_SCHEMA["description"],
                SKELETONS_DESCRIPTION,
                SKELETONS_GUIDANCE,
            )
        ),
        "inputSchema": copy.deepcopy(GENERATE_TEST_SKELETONS_SCHEMA["parameters"]),
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

    def __init__(
        self,
        handler: Handler = _deterministic_handler,
        skeletons_handler: Handler = handle_generate_test_skeletons,
    ) -> None:
        self._handler = handler
        self._skeletons_handler = skeletons_handler

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
            return {"tools": [tool_definition(), skeletons_tool_definition()]}
        if method == "tools/call":
            return self._call_tool(params)
        raise ProtocolError(METHOD_NOT_FOUND, f"Method not found: {method[:64]}")

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if name not in (TOOL_NAME, SKELETONS_TOOL_NAME):
            raise ProtocolError(
                INVALID_PARAMS,
                f"Unknown tool; the tools are {TOOL_NAME} and {SKELETONS_TOOL_NAME}.",
            )
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ProtocolError(INVALID_PARAMS, "Tool arguments must be an object.")
        if name == SKELETONS_TOOL_NAME:
            text = self._skeletons_handler(arguments)
            # A failure is a tool result with the stable error payload, as for an analysis.
            return _text_result(text, is_error=json.loads(text).get("success") is False)
        effective = {"output_format": DEFAULT_OUTPUT_FORMAT, **arguments}
        wants_brief = effective["output_format"] == OutputFormat.MARKDOWN.value
        # A brief is requested together with its analysis, so an oversized one can be rendered
        # again with shorter excerpts instead of analyzing twice.
        request = (
            {**effective, "output_format": OutputFormat.BOTH.value} if wants_brief else effective
        )
        text = self._handler(request)
        payload = json.loads(text)
        if payload.get("success") is False:
            # An analysis failure is a tool result, not a protocol error, so the model sees the
            # stable error code and remediation and can correct its arguments.
            return _text_result(text, is_error=True)
        if wants_brief and isinstance(payload.get("markdown"), str):
            return _text_result(_brief(payload), is_error=False)
        return _text_result(text, is_error=False)


def _text_result(text: str, *, is_error: bool) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _brief(envelope: dict[str, Any]) -> str:
    """The brief as Markdown, fitted to the host, closed by the envelope fields it hides.

    Plain Markdown rather than the JSON envelope, because it is what the model reads and it is
    smaller without the escaping. The analysis ID is pattern-validated, so the closing comment
    cannot be broken out of.
    """
    analysis = envelope.get("analysis")
    # Named for what it is, not "identity": that word is an authorization keyword to the
    # deterministic rules, and the analyzer reported this line as an authorization change.
    source = analysis if isinstance(analysis, dict) else envelope
    footer = (
        f"<!-- analysis_id={source.get('analysis_id')} "
        f"schema_version={envelope.get('schema_version')} -->"
    )
    return f"{_fitted_markdown(envelope['markdown'], analysis).rstrip()}\n\n{footer}\n"


def _size(text: str) -> int:
    return len(text.encode("utf-8"))


def _fitted_markdown(markdown: str, analysis: Any) -> str:
    """The brief, rendered again with shorter evidence excerpts while it is over budget.

    If even no excerpts are too much, the smallest rendering is returned: narrowing with
    `include` is the remedy then, and the tool description says so.
    """
    if _size(markdown) <= BRIEF_BUDGET_BYTES or not isinstance(analysis, dict):
        return markdown
    result = AnalysisResult.model_validate(analysis)
    for limit in EXCERPT_STEPS:
        markdown = render_markdown(result, excerpt_chars=limit)
        if _size(markdown) <= BRIEF_BUDGET_BYTES:
            break
    return markdown


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
