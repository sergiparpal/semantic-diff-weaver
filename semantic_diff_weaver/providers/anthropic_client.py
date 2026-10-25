"""An `LlmClient` backed by the Anthropic Messages API.

Hermes supplied the provider, model, credentials, and structured-output mechanism for free.
Standalone use has to supply them, and this module is the whole of that: one bounded,
schema-constrained request per call, mapped onto the same failure modes the interpreter
already understands.

Two request-shape decisions are load-bearing and are not arbitrary:

*Sampling parameters are never sent.* `semantic_interpreter._call` passes `temperature=0.1`,
but API version 2026-10-21, the default from SDK 2.0, returns HTTP 400 for `temperature`,
`top_p`, or `top_k` on every model, and the current model generation already did on the version
SDK 1.x sends. Forwarding the value only through older SDKs would make the same call sample
differently depending on which SDK release happens to be installed, so it is not forwarded at
all: models that still honor it sample at their default.

*Thinking is turned off where the model allows it.* `max_tokens` caps thinking and response
text together, and the interpreter's budget is deliberately small. With thinking on by default
on current models, a 2000-token budget can be consumed before the JSON is emitted, truncating
it. Turning thinking off keeps the caller's bound meaningful, and the response is
schema-constrained anyway. Most families turn it off with `{"type": "disabled"}`; Claude Sonnet
5.5 rejects that with HTTP 400 and takes `{"type": "between_tools"}`, which no other family
accepts. On families whose thinking is always on, any value that turns it off is rejected with
HTTP 400, so the parameter is omitted for them rather than sent — forwarding `disabled`
unconditionally failed *every* call on those models and silently collapsed the whole analysis
into deterministic fallback.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

MODEL_ENV = "SEMANTIC_DIFF_WEAVER_MODEL"
API_KEY_ENV = "ANTHROPIC_API_KEY"
DEFAULT_MODEL = "claude-opus-5"

# Model families whose thinking is always on: any value that turns it off returns HTTP 400 at
# every effort level, and the parameter has to be omitted entirely. Matched as prefixes so
# dated snapshots and aliases behave identically.
THINKING_ALWAYS_ON_PREFIXES = (
    "claude-fable-5",
    "claude-mythos-5",
    "claude-opus-5-5",
)

# Model families that return HTTP 400 for `{"type": "disabled"}` and turn thinking off with
# `{"type": "between_tools"}` instead, which every other family rejects. Matched as prefixes,
# so `claude-sonnet-5` itself is not one of them.
THINKING_BETWEEN_TOOLS_PREFIXES = ("claude-sonnet-5-5",)

# The interpreter reads the structured payload out of a response shaped like this.
CONTENT_TYPE_JSON = "json"

MAX_RESPONSE_CHARS = 200_000


class ProviderUnavailable(RuntimeError):
    """The provider could not produce a result. Maps onto `LLM_UNAVAILABLE`.

    Deliberately not a `ValueError`: the interpreter treats `ValueError` as a *schema*
    failure, and a transport or refusal problem is not one.
    """


@dataclass(frozen=True)
class StructuredUsage:
    """The token accounting `semantic_interpreter._accumulate_usage` reads."""

    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class StructuredResult:
    """The response shape `semantic_interpreter._parse_structured_result` expects."""

    content_type: str
    parsed: Any
    usage: StructuredUsage | None = None


def resolve_model(override: str | None = None) -> str:
    """Explicit flag, then environment, then the latest capable default."""
    if override:
        return override
    configured = os.environ.get(MODEL_ENV)
    return configured if configured else DEFAULT_MODEL


def thinking_parameter(model: str) -> dict[str, str] | None:
    """The ``thinking`` value that turns thinking off on `model`, or None to omit it."""
    if model.startswith(THINKING_ALWAYS_ON_PREFIXES):
        return None
    if model.startswith(THINKING_BETWEEN_TOOLS_PREFIXES):
        return {"type": "between_tools"}
    return {"type": "disabled"}


class AnthropicClient:
    """A bounded structured-inference client. Satisfies `llm_client.LlmClient`."""

    def __init__(self, *, model: str, transport: Any) -> None:
        """`transport` is anything exposing `messages.create`; the SDK client in production."""
        self.model = model
        self._transport = transport

    @classmethod
    def from_environment(
        cls, *, model: str | None = None
    ) -> tuple[AnthropicClient | None, str | None]:
        """Build a client, or explain in one line why deterministic mode is being used.

        Never raises and never reports the key's value — only whether one was present.
        """
        resolved = resolve_model(model)
        if not os.environ.get(API_KEY_ENV):
            return None, (
                f"no {API_KEY_ENV} in the environment; continuing in deterministic mode "
                "(structural findings only)."
            )
        try:
            import anthropic
        except ImportError:
            return None, (
                "the 'anthropic' package is not installed; continuing in deterministic mode. "
                "Install it with: pip install 'semantic-diff-weaver[anthropic]'"
            )
        try:
            transport = anthropic.Anthropic()
        except Exception:
            # Never surface the provider's message: it can echo configuration values.
            return None, (
                "the Anthropic client could not be constructed; continuing in deterministic mode."
            )
        return cls(model=resolved, transport=transport), None

    def complete_structured(
        self,
        *,
        instructions: str,
        input: list[dict[str, Any]],
        json_schema: dict[str, Any],
        schema_name: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
        purpose: str,
    ) -> StructuredResult:
        del schema_name, purpose  # Recorded by the caller; the request shape has no slot.
        del temperature  # Never sent: see the module docstring.
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": instructions,
            "messages": [{"role": "user", "content": self._content_blocks(input)}],
            "output_config": {"format": {"type": "json_schema", "schema": json_schema}},
        }
        thinking = thinking_parameter(self.model)
        if thinking is not None:
            # See the module docstring: keeps `max_tokens` a bound on the answer itself.
            request["thinking"] = thinking

        try:
            transport = self._transport.with_options(timeout=timeout)
        except AttributeError:  # pragma: no cover - exercised through the stubbed transport
            transport = self._transport

        try:
            response = transport.messages.create(**request)
        except Exception as exc:
            raise self._transport_failure(exc) from None

        return self._read(response)

    @staticmethod
    def _content_blocks(input: list[dict[str, Any]]) -> list[dict[str, Any]]:
        blocks = [
            {"type": "text", "text": str(item.get("text", ""))}
            for item in input
            if item.get("type") == "text"
        ]
        if not blocks:
            raise ValueError("structured request carried no text content")
        return blocks

    @staticmethod
    def _transport_failure(exc: Exception) -> Exception:
        """Re-raise without the provider's message, which may echo request configuration.

        A timeout becomes `TimeoutError` so the interpreter retries it once; everything else
        becomes `ProviderUnavailable`, which it does not classify as a schema failure.
        """
        name = type(exc).__name__
        if isinstance(exc, TimeoutError) or "Timeout" in name:
            return TimeoutError("the structured inference request timed out")
        return ProviderUnavailable(f"the inference provider failed ({name})")

    @classmethod
    def _read(cls, response: Any) -> StructuredResult:
        if getattr(response, "stop_reason", None) == "refusal":
            raise ProviderUnavailable("the provider declined the request")
        text = cls._first_text(response)
        if len(text) > MAX_RESPONSE_CHARS:
            raise ValueError("structured result exceeded the response limit")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            # Truncation under `max_tokens` lands here too, and is a schema failure:
            # the caller gets deterministic fallback rather than a partial finding.
            raise ValueError("structured result was not valid JSON") from exc
        return StructuredResult(
            content_type=CONTENT_TYPE_JSON, parsed=parsed, usage=cls._usage(response)
        )

    @staticmethod
    def _first_text(response: Any) -> str:
        for block in getattr(response, "content", None) or ():
            if getattr(block, "type", None) == "text":
                return str(getattr(block, "text", ""))
        raise ValueError("structured result contained no text block")

    @staticmethod
    def _usage(response: Any) -> StructuredUsage | None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        if input_tokens is None and output_tokens is None:
            return None
        return StructuredUsage(input_tokens=input_tokens, output_tokens=output_tokens)
