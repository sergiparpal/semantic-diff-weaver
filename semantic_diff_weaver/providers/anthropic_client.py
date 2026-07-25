"""An `LlmClient` backed by the Anthropic Messages API.

Hermes supplied the provider, model, credentials, and structured-output mechanism for free.
Standalone use has to supply them, and this module is the whole of that: one bounded,
schema-constrained request per call, mapped onto the same failure modes the interpreter
already understands.

Two request-shape decisions are load-bearing and are not arbitrary:

*Sampling parameters are omitted on models that reject them.* `semantic_interpreter._call`
passes `temperature=0.1`, but `temperature`, `top_p`, and `top_k` were removed on the current
model generation and a request carrying one returns HTTP 400. Forwarding the value verbatim
would make every call fail on the default model, so it is forwarded only to models that still
accept it.

*Thinking is disabled.* `max_tokens` caps thinking and response text together, and the
interpreter's budget is deliberately small. With thinking on by default on current models, a
2000-token budget can be consumed before the JSON is emitted, truncating it. Disabling
thinking keeps the caller's bound meaningful, and the response is schema-constrained anyway.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

MODEL_ENV = "SEMANTIC_DIFF_WEAVER_MODEL"
API_KEY_ENV = "ANTHROPIC_API_KEY"
DEFAULT_MODEL = "claude-opus-5"

# Model families that removed `temperature`/`top_p`/`top_k`; sending one returns HTTP 400.
# Matched as prefixes so dated snapshots and aliases behave identically.
SAMPLING_REMOVED_PREFIXES = (
    "claude-fable-5",
    "claude-mythos-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-sonnet-5",
)

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


def accepts_sampling_parameters(model: str) -> bool:
    return not model.startswith(SAMPLING_REMOVED_PREFIXES)


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
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": instructions,
            "messages": [{"role": "user", "content": self._content_blocks(input)}],
            "output_config": {"format": {"type": "json_schema", "schema": json_schema}},
            # See the module docstring: keeps `max_tokens` a bound on the answer itself.
            "thinking": {"type": "disabled"},
        }
        if accepts_sampling_parameters(self.model):
            request["temperature"] = temperature

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
