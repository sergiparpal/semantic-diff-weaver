"""The Anthropic adapter's request shape and failure mapping, entirely offline.

No test here makes a network call or requires a key. The transport is stubbed, so what is
being pinned is the contract on both sides: the request the adapter builds, and the exact
exception classes the interpreter distinguishes between.
"""

from __future__ import annotations

import builtins
import json
import sys
from dataclasses import dataclass
from typing import Any

import pytest
from _pytest.monkeypatch import MonkeyPatch

from semantic_diff_weaver.llm_client import LlmClient
from semantic_diff_weaver.providers.anthropic_client import (
    API_KEY_ENV,
    DEFAULT_MODEL,
    MODEL_ENV,
    AnthropicClient,
    ProviderUnavailable,
    accepts_sampling_parameters,
    resolve_model,
)
from semantic_diff_weaver.schemas import LLM_RESPONSE_SCHEMA, LLM_SCHEMA_NAME

VALID_PAYLOAD: dict[str, Any] = {"behavior_changes": [], "notes": []}


@dataclass
class Block:
    type: str
    text: str


@dataclass
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class Response:
    content: list[Block]
    usage: Usage | None = None
    stop_reason: str | None = "end_turn"


class Messages:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class Transport:
    """Stands in for `anthropic.Anthropic`, including its `with_options` timeout hook."""

    def __init__(self, outcome: Any) -> None:
        self.messages = Messages(outcome)
        self.timeouts: list[int] = []

    def with_options(self, *, timeout: int) -> Transport:
        self.timeouts.append(timeout)
        return self


def _json_response(payload: dict[str, Any] | str | None = None) -> Response:
    text = payload if isinstance(payload, str) else json.dumps(payload or VALID_PAYLOAD)
    return Response(content=[Block(type="text", text=text)], usage=Usage(120, 45))


def _client(outcome: Any, *, model: str = DEFAULT_MODEL) -> tuple[AnthropicClient, Transport]:
    transport = Transport(outcome)
    return AnthropicClient(model=model, transport=transport), transport


def _call(client: AnthropicClient) -> Any:
    return client.complete_structured(
        instructions="Interpret the evidence.",
        input=[{"type": "text", "text": "ev-001 boundary changed"}],
        json_schema=LLM_RESPONSE_SCHEMA,
        schema_name=LLM_SCHEMA_NAME,
        temperature=0.1,
        max_tokens=2000,
        timeout=30,
        purpose="semantic-diff-interpretation",
    )


def test_the_adapter_satisfies_the_protocol() -> None:
    client: LlmClient = AnthropicClient(model=DEFAULT_MODEL, transport=Transport(None))
    assert callable(client.complete_structured)


def test_a_successful_call_returns_the_shape_the_interpreter_reads() -> None:
    client, _ = _client(_json_response())
    result = _call(client)
    assert result.content_type == "json"
    assert result.parsed == VALID_PAYLOAD
    assert result.usage is not None
    assert (result.usage.input_tokens, result.usage.output_tokens) == (120, 45)


def test_the_request_carries_the_schema_the_caller_supplied() -> None:
    client, transport = _client(_json_response())
    _call(client)
    request = transport.messages.requests[0]
    assert request["model"] == DEFAULT_MODEL
    assert request["max_tokens"] == 2000
    assert request["system"] == "Interpret the evidence."
    assert request["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "ev-001 boundary changed"}]}
    ]
    assert request["output_config"]["format"]["schema"] is LLM_RESPONSE_SCHEMA
    assert transport.timeouts == [30]


def test_thinking_is_disabled_so_max_tokens_bounds_the_answer() -> None:
    """`max_tokens` caps thinking and text together; the caller's budget must bound the JSON."""
    client, transport = _client(_json_response())
    _call(client)
    assert transport.messages.requests[0]["thinking"] == {"type": "disabled"}


def test_temperature_is_omitted_on_models_that_reject_it() -> None:
    """Current models return HTTP 400 for `temperature`; forwarding it would fail every call."""
    client, transport = _client(_json_response())
    _call(client)
    assert "temperature" not in transport.messages.requests[0]
    assert "top_p" not in transport.messages.requests[0]


def test_temperature_is_honored_on_models_that_still_accept_it() -> None:
    client, transport = _client(_json_response(), model="claude-haiku-4-5")
    _call(client)
    assert transport.messages.requests[0]["temperature"] == 0.1


@pytest.mark.parametrize(
    ("model", "accepted"),
    [
        ("claude-opus-5", False),
        ("claude-sonnet-5", False),
        ("claude-opus-4-8", False),
        ("claude-fable-5", False),
        ("claude-haiku-4-5", True),
        ("claude-sonnet-4-6", True),
    ],
)
def test_sampling_support_is_decided_per_model(model: str, accepted: bool) -> None:
    assert accepts_sampling_parameters(model) is accepted


def test_malformed_json_is_a_schema_failure() -> None:
    """`ValueError` is what the interpreter classifies as `LLM_SCHEMA_FAILURE`."""
    client, _ = _client(_json_response("{not json"))
    with pytest.raises(ValueError):
        _call(client)


def test_a_truncated_response_is_a_schema_failure() -> None:
    client, _ = _client(_json_response('{"behavior_changes": [{"id": "bc-'))
    with pytest.raises(ValueError):
        _call(client)


def test_a_response_without_a_text_block_is_a_schema_failure() -> None:
    client, _ = _client(Response(content=[Block(type="thinking", text="")]))
    with pytest.raises(ValueError):
        _call(client)


def test_an_oversized_response_is_rejected_before_parsing() -> None:
    client, _ = _client(_json_response("0" * 200_001))
    with pytest.raises(ValueError):
        _call(client)


def test_a_refusal_is_unavailability_not_a_schema_failure() -> None:
    """A declined request is not malformed output — it must not be labelled a schema failure."""
    client, _ = _client(Response(content=[Block(type="text", text="{}")], stop_reason="refusal"))
    with pytest.raises(ProviderUnavailable):
        _call(client)


def test_a_timeout_is_raised_as_a_timeout_so_the_interpreter_retries() -> None:
    class APITimeoutError(Exception):
        pass

    client, _ = _client(APITimeoutError("connection to host timed out"))
    with pytest.raises(TimeoutError):
        _call(client)


def test_a_transport_failure_is_unavailability() -> None:
    client, _ = _client(RuntimeError("boom"))
    with pytest.raises(ProviderUnavailable):
        _call(client)


def test_an_empty_input_list_is_rejected() -> None:
    client, _ = _client(_json_response())
    with pytest.raises(ValueError):
        client.complete_structured(
            instructions="Interpret.",
            input=[],
            json_schema=LLM_RESPONSE_SCHEMA,
            schema_name=LLM_SCHEMA_NAME,
            temperature=0.1,
            max_tokens=2000,
            timeout=30,
            purpose="semantic-diff-interpretation",
        )


def test_a_transport_without_with_options_still_works() -> None:
    class Bare:
        def __init__(self) -> None:
            self.messages = Messages(_json_response())

    client = AnthropicClient(model=DEFAULT_MODEL, transport=Bare())
    assert _call(client).parsed == VALID_PAYLOAD


def test_usage_is_optional() -> None:
    client, _ = _client(Response(content=[Block(type="text", text="{}")], usage=None))
    assert _call(client).usage is None
    client, _ = _client(Response(content=[Block(type="text", text="{}")], usage=Usage()))
    assert _call(client).usage is None


def test_model_resolution_prefers_the_flag_then_the_environment(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv(MODEL_ENV, raising=False)
    assert resolve_model() == DEFAULT_MODEL
    monkeypatch.setenv(MODEL_ENV, "claude-sonnet-5")
    assert resolve_model() == "claude-sonnet-5"
    assert resolve_model("claude-haiku-4-5") == "claude-haiku-4-5"
    monkeypatch.setenv(MODEL_ENV, "")
    assert resolve_model() == DEFAULT_MODEL


def test_a_missing_key_is_a_notice_not_a_failure(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    client, notice = AnthropicClient.from_environment()
    assert client is None
    assert notice is not None
    assert "deterministic mode" in notice


def test_a_missing_package_is_a_notice_not_a_failure(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv(API_KEY_ENV, "test-key-not-real")
    real_import = builtins.__import__

    def fail(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "anthropic":
            raise ImportError("No module named 'anthropic'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fail)
    client, notice = AnthropicClient.from_environment()
    assert client is None
    assert notice is not None
    assert "not installed" in notice


def test_a_client_that_cannot_be_constructed_is_a_notice(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv(API_KEY_ENV, "test-key-not-real")

    class Module:
        @staticmethod
        def Anthropic() -> Any:
            raise RuntimeError("misconfigured")

    monkeypatch.setitem(sys.modules, "anthropic", Module())
    client, notice = AnthropicClient.from_environment()
    assert client is None
    assert notice is not None
    assert "deterministic mode" in notice


def test_a_present_key_builds_a_client(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv(API_KEY_ENV, "test-key-not-real")

    class Module:
        @staticmethod
        def Anthropic() -> Any:
            return Transport(_json_response())

    monkeypatch.setitem(sys.modules, "anthropic", Module())
    client, notice = AnthropicClient.from_environment(model="claude-sonnet-5")
    assert notice is None
    assert client is not None
    assert client.model == "claude-sonnet-5"
