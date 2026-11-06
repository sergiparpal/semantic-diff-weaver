from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from scripts import openai_compatible
from scripts.openai_compatible import (
    BudgetExhausted,
    CallRecord,
    OpenAICompatibleClient,
    ProviderUnavailable,
    Usage,
    endpoint,
)
from semantic_diff_weaver.models import Origin, WeaverConfig
from semantic_diff_weaver.schemas import LLM_RESPONSE_SCHEMA, LLM_SCHEMA_NAME
from semantic_diff_weaver.semantic_interpreter import interpret_candidates
from tests.contract.test_llm_call import candidate

KEY = "sk-or-test-0000"
REPLY: dict[str, Any] = {"behaviors": [], "obligations": []}
USAGE: dict[str, Any] = {
    "prompt_tokens": 1200,
    "completion_tokens": 80,
    "cost": 0.00042,
    "completion_tokens_details": {"reasoning_tokens": 0},
}


REFUSAL = {
    "choices": [
        {
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": None, "refusal": "I cannot help."},
        }
    ],
    "usage": USAGE,
}


class Stub:
    """A chat completions endpoint on a loopback port, answering from a script."""

    def __init__(self) -> None:
        self.url = ""
        self.requests: list[dict[str, Any]] = []
        self.answers: list[tuple[int, dict[str, str], bytes]] = []
        self.delay = 0.0

    def reply(self, body: Any = None, *, status: int = 200, **headers: str) -> None:
        payload = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.answers.append((status, headers, payload))


class _Server(ThreadingHTTPServer):
    def handle_error(self, request: Any, client_address: Any) -> None:
        """A client that gave up on a slow reply is expected here."""


@pytest.fixture
def stub() -> Iterator[Stub]:
    state = Stub()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            state.requests.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "headers": dict(self.headers),
                    "body": json.loads(self.rfile.read(length)) if length else None,
                }
            )
            if state.delay:
                time.sleep(state.delay)
            status, headers, body = state.answers.pop(0) if state.answers else (500, {}, b"{}")
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST

        def log_message(self, format: str, *arguments: Any) -> None:
            """Keep the test output quiet."""

    server = _Server(("127.0.0.1", 0), Handler)
    # A short poll keeps shutdown from waiting out the default half second.
    thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_address[1]}/api/v1"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _client(stub: Stub, **options: Any) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(model="vendor/model", api_key=KEY, base_url=stub.url, **options)


def _call(client: OpenAICompatibleClient, *, timeout: float = 5) -> Any:
    return client.complete_structured(
        instructions="Interpret the evidence.",
        input=[{"type": "text", "text": "the evidence"}],
        json_schema=LLM_RESPONSE_SCHEMA,
        schema_name=LLM_SCHEMA_NAME,
        temperature=0.1,
        max_tokens=2100,
        timeout=timeout,
        purpose="semantic-diff-interpretation",
    )


def _completion(
    content: str | None = None, *, finish_reason: str = "stop", usage: Any = USAGE
) -> dict[str, Any]:
    return {
        "id": "gen-1",
        "provider": "Stub",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {
                    "role": "assistant",
                    "content": json.dumps(REPLY) if content is None else content,
                },
            }
        ],
        "usage": usage,
    }


def test_a_call_sends_the_interpreter_s_request_and_reads_the_reply(stub: Stub) -> None:
    stub.reply(_completion())
    client = _client(stub)
    result = _call(client)
    assert (result.content_type, result.parsed) == ("json", REPLY)
    assert result.usage == Usage(input_tokens=1200, output_tokens=80, cost=0.00042)
    request = stub.requests[0]
    assert (request["method"], request["path"]) == ("POST", "/api/v1/chat/completions")
    assert request["headers"]["Authorization"] == f"Bearer {KEY}"
    assert request["body"] == {
        "model": "vendor/model",
        "messages": [
            {"role": "system", "content": "Interpret the evidence."},
            {"role": "user", "content": "the evidence"},
        ],
        "max_completion_tokens": 2100,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": LLM_SCHEMA_NAME,
                "strict": False,
                "schema": LLM_RESPONSE_SCHEMA,
            },
        },
        "provider": {"require_parameters": True},
        "temperature": 0.1,
    }
    [record] = client.calls
    assert record.seconds > 0
    assert asdict(record) | {"seconds": 0} == asdict(
        CallRecord(
            outcome="ok",
            status=200,
            input_tokens=1200,
            output_tokens=80,
            reasoning_tokens=0,
            cost=0.00042,
            finish_reason="stop",
            provider="Stub",
        )
    )
    assert client.spent == pytest.approx(0.00042)


def test_temperature_and_reasoning_are_sent_as_asked(stub: Stub) -> None:
    stub.reply(_completion())
    _call(_client(stub, send_temperature=False, reasoning_effort="low"))
    body = stub.requests[0]["body"]
    assert "temperature" not in body
    assert body["reasoning"] == {"effort": "low"}
    with pytest.raises(ValueError, match="reasoning effort"):
        _client(stub, reasoning_effort="extreme")


def test_the_interpreter_reads_the_reply_as_it_reads_any_model_s(stub: Stub) -> None:
    item = candidate()
    behavior = {
        "category": item.category.value,
        "summary": "The comparison becomes inclusive.",
        "observable_impact": "Five is now allowed.",
        "evidence_ids": [evidence.id for evidence in item.evidence],
        "confidence": 0.9,
    }
    stub.reply(_completion(json.dumps({"behaviors": [behavior], "obligations": []})))
    result = interpret_candidates([item], _client(stub), WeaverConfig())
    assert result.status.available and (result.status.calls, result.status.failures) == (1, 0)
    assert result.status.usage is not None and result.status.usage.cost == pytest.approx(0.00042)
    assert [entry.origin for entry in result.candidates] == [Origin.LLM_SUPPORTED]


def test_a_fenced_reply_is_read_and_its_fence_recorded(stub: Stub) -> None:
    stub.reply(_completion(f"```json\n{json.dumps(REPLY)}\n```"))
    client = _client(stub)
    assert _call(client).parsed == REPLY
    assert client.calls[0].fenced


def test_a_reply_off_the_schema_is_handed_on_and_recorded(stub: Stub) -> None:
    stub.reply(_completion(json.dumps({"behaviors": "none"})))
    client = _client(stub)
    assert _call(client).parsed == {"behaviors": "none"}
    assert client.calls[0].outcome == "schema_invalid"


@pytest.mark.parametrize(
    ("completion", "outcome"),
    [
        (_completion("", finish_reason="length"), "length"),
        (_completion("not JSON"), "invalid_json"),
        (_completion("  "), "no_content"),
    ],
)
def test_a_reply_cut_off_or_unreadable_is_a_schema_failure(
    stub: Stub, completion: dict[str, Any], outcome: str
) -> None:
    stub.reply(completion)
    client = _client(stub)
    with pytest.raises(ValueError):
        _call(client)
    assert client.calls[0].outcome == outcome
    # A reply was billed, and the provider said what it cost.
    assert not client.calls[0].unaccounted


@pytest.mark.parametrize(
    ("body", "outcome"),
    [
        (REFUSAL, "refusal"),
        ({"error": {"code": 502, "message": f"Upstream failed for {KEY}"}}, "error_body"),
        (b"<html>busy</html>", "invalid_body"),
        ({"choices": []}, "invalid_body"),
        ([1, 2], "invalid_body"),
    ],
)
def test_a_refusal_or_an_error_in_the_body_is_a_provider_failure(
    stub: Stub, body: Any, outcome: str
) -> None:
    stub.reply(body)
    client = _client(stub)
    with pytest.raises(ProviderUnavailable):
        _call(client)
    assert client.calls[0].outcome == outcome
    if outcome == "error_body":
        assert client.calls[0].error == "Upstream failed for [key]"


def test_an_http_error_is_a_provider_failure_that_never_carries_the_key(stub: Stub) -> None:
    stub.reply({"error": {"code": 402, "message": f"Insufficient credits on {KEY}"}}, status=402)
    client = _client(stub)
    with pytest.raises(ProviderUnavailable) as raised:
        _call(client)
    assert str(raised.value) == "the provider answered HTTP 402"
    assert raised.value.__cause__ is None and raised.value.__suppress_context__
    record = client.calls[0]
    assert (record.outcome, record.status) == ("http_402", 402)
    assert record.error == "Insufficient credits on [key]"
    assert KEY not in json.dumps([asdict(call) for call in client.calls])
    assert not record.unaccounted


def test_a_redirect_is_refused_so_the_key_never_follows_it(stub: Stub) -> None:
    stub.reply({}, status=307, Location=f"{stub.url}/elsewhere")
    client = _client(stub)
    with pytest.raises(ProviderUnavailable):
        _call(client)
    assert len(stub.requests) == 1
    assert client.calls[0].outcome == "http_307"


def test_a_slow_reply_times_out_for_the_interpreter_to_retry(stub: Stub) -> None:
    stub.delay = 1.0
    stub.reply(_completion())
    client = _client(stub)
    with pytest.raises(TimeoutError):
        _call(client, timeout=0.2)
    assert client.calls[0].outcome == "timeout"


def test_an_oversized_reply_is_refused(stub: Stub, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_compatible, "MAX_RESPONSE_BYTES", 64)
    stub.reply(_completion())
    client = _client(stub)
    with pytest.raises(ProviderUnavailable, match="bound"):
        _call(client)
    assert client.calls[0].outcome == "oversized"


def test_an_unreachable_endpoint_is_a_provider_failure() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    client = OpenAICompatibleClient(
        model="vendor/model", api_key=KEY, base_url=f"http://127.0.0.1:{port}/v1"
    )
    with pytest.raises(ProviderUnavailable, match="could not be reached"):
        _call(client)
    assert client.calls[0].outcome == "transport"


def test_the_spending_cap_stops_a_call_before_it_is_made(stub: Stub) -> None:
    for _ in range(3):
        stub.reply(_completion())
    client = _client(stub, max_cost_usd=0.0005)
    _call(client)
    _call(client)
    with pytest.raises(BudgetExhausted, match=r"cap of 0\.0005 USD"):
        _call(client)
    assert len(stub.requests) == 2
    assert [call.outcome for call in client.calls] == ["ok", "ok", "refused_budget"]
    # The interpreter ends the batch on it rather than retrying it as a schema failure.
    assert not isinstance(BudgetExhausted("cap"), ValueError)


def test_a_reply_of_unknown_cost_stops_every_later_call(stub: Stub) -> None:
    stub.reply(_completion(usage={"prompt_tokens": 10, "completion_tokens": 5}))
    client = _client(stub, max_cost_usd=1.0)
    _call(client)
    assert client.calls[0].unaccounted
    with pytest.raises(BudgetExhausted, match="cost is unknown"):
        _call(client)
    assert len(stub.requests) == 1


def test_a_cost_is_estimated_from_the_tokens_at_the_prices_given(stub: Stub) -> None:
    stub.reply(_completion(usage={"prompt_tokens": 10, "completion_tokens": 5}))
    stub.reply(_completion())
    client = _client(stub, max_cost_usd=1.0, usd_per_million_tokens=(1.0, 2.0))
    _call(client)
    first = client.calls[0]
    assert (first.cost, first.estimated, first.unaccounted) == (pytest.approx(2e-5), True, False)
    _call(client)
    assert (client.calls[1].cost, client.calls[1].estimated) == (pytest.approx(0.00042), False)


def test_a_call_that_failed_before_a_reply_does_not_stop_later_ones(stub: Stub) -> None:
    stub.reply({"error": {"code": 503, "message": "No provider"}}, status=503)
    stub.reply(_completion())
    client = _client(stub, max_cost_usd=1.0)
    with pytest.raises(ProviderUnavailable):
        _call(client)
    assert _call(client).parsed == REPLY


def test_the_endpoint_is_https_or_loopback_http() -> None:
    assert (
        endpoint("https://openrouter.ai/api/v1/") == "https://openrouter.ai/api/v1/chat/completions"
    )
    for allowed in ("http://127.0.0.1:8080/v1", "http://localhost:8080/v1", "http://[::1]:8080"):
        assert endpoint(allowed).endswith("/chat/completions")
    for refused in (
        "http://openrouter.ai/api/v1",
        "ftp://openrouter.ai/api/v1",
        "https://user:secret@openrouter.ai/api/v1",
        "https://openrouter.ai/api/v1?key=secret",
        "https:///api/v1",
    ):
        with pytest.raises(ValueError, match="endpoint"):
            endpoint(refused)


def test_the_key_comes_from_the_environment_and_is_never_shown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="no OPENROUTER_API_KEY in the environment"):
        OpenAICompatibleClient.from_environment(model="vendor/model")
    monkeypatch.setenv("SDW_TEST_KEY", KEY)
    client = OpenAICompatibleClient.from_environment(api_key_env="SDW_TEST_KEY", model="m")
    assert KEY not in repr(client)
    with pytest.raises(ValueError, match="model"):
        OpenAICompatibleClient(model="", api_key=KEY)
    with pytest.raises(ValueError, match="API key"):
        OpenAICompatibleClient(model="m", api_key="")
