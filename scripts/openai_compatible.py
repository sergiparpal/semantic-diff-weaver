"""An `LlmClient` over an OpenAI-compatible chat completions endpoint, such as OpenRouter's.

A developer tool for measuring the inference layer with real models (`run_benchmark.py llm`), not
part of the package: the package's own standalone provider is the Anthropic adapter, and Hermes
supplies its host's model. Like that adapter, it maps every provider failure onto the
interpreter's two failure modes: `TimeoutError`, retried once, and anything else, which ends the
batch in deterministic fallback. Unlike it, it keeps a record of every call, what it cost and how
it ended, because the measurement needs to know why a batch fell back.

The request:
- `response_format` is `json_schema` with the interpreter's own schema, not strict, since OpenAI's
  strict mode rejects a schema with optional properties; the interpreter validates the reply
  itself.
- `provider.require_parameters` keeps OpenRouter from routing to a provider that would ignore the
  schema, or any other parameter sent.
- `temperature` is forwarded as the interpreter asks unless the caller drops it, since some
  reasoning models reject it.
- `reasoning` is sent only when asked for. Reasoning tokens share `max_completion_tokens` with the
  reply, so a model that reasons by default can spend the reply's budget: the call then ends with
  `finish_reason` "length", which its record names.

Spending: with `max_cost_usd`, no call is made once the calls so far cost that much, counting each
call's cost as the provider reports it (OpenRouter always does), or as estimated from its tokens at
the prices given. A call that got a reply of unknown cost stops every later one, since the cap can
no longer be kept. A call that ended without a reply, answered with an HTTP error or timed out, is
taken as unbilled, though a provider may charge for a timed-out one. The cap can also be passed by
the cost of the one call that crosses it. A credit limit on the key itself is the hard bound.

Security:
- The key comes from an environment variable and goes only to the configured endpoint, over HTTPS,
  or over HTTP to a loopback address for tests. It is never printed or recorded.
- Redirects are refused, since urllib would carry the `Authorization` header to the new location.
- A response is read up to a bound, and a provider's error message is kept, shortened and with the
  key removed, only in the call record.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

from semantic_diff_weaver.models import LlmBatchResponse

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_API_KEY_ENV = "OPENROUTER_API_KEY"
CONTENT_TYPE_JSON = "json"
MAX_RESPONSE_BYTES = 4_000_000
MAX_ERROR_CHARS = 300
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high")
# The outcomes of a call that got a reply from a model, and was therefore billed. The others end
# before generation, where a provider does not charge.
REPLIED = frozenset({"ok", "schema_invalid", "length", "invalid_json", "no_content", "refusal"})
# A reply some models wrap in a Markdown fence despite the schema.
FENCED = re.compile(r"\A\s*```(?:json)?[ \t]*\n(?P<body>.*?)\n\s*```\s*\Z", re.DOTALL)


class ProviderUnavailable(RuntimeError):
    """The provider produced no usable reply. Deliberately not a `ValueError`, which the
    interpreter reads as a schema failure."""


class BudgetExhausted(ProviderUnavailable):
    """The spending cap was reached, or a call's cost could not be known, so no call was made."""


@dataclass(frozen=True)
class Usage:
    """The token accounting `semantic_interpreter._accumulate_usage` reads."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cost: float | None = None


@dataclass(frozen=True)
class StructuredResult:
    """The response shape `semantic_interpreter._parse_structured_result` expects."""

    content_type: str
    parsed: Any
    usage: Usage | None = None


@dataclass
class CallRecord:
    """How one request ended and what it cost; never its content."""

    seconds: float = 0.0
    # ok, schema_invalid, length, invalid_json, no_content, refusal, error_body, invalid_body,
    # oversized, http_<status>, timeout, transport, or refused_budget.
    outcome: str = "ok"
    status: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cost: float | None = None
    # Whether `cost` was estimated from the token counts rather than reported by the provider.
    estimated: bool = False
    # A reply came back, so the call was billed, but its cost is unknown.
    unaccounted: bool = False
    finish_reason: str | None = None
    provider: str | None = None
    fenced: bool = False
    error: str | None = None


def endpoint(base_url: str) -> str:
    """The chat completions URL under `base_url`, which must be HTTPS or loopback HTTP."""
    parts = urlsplit(base_url)
    host = parts.hostname or ""
    if parts.scheme != "https" and not (parts.scheme == "http" and _loopback(host)):
        raise ValueError("the endpoint must use HTTPS, or HTTP on a loopback address")
    if not host or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("the endpoint must name a host and carry no credentials or query")
    return base_url.rstrip("/") + "/chat/completions"


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *arguments: Any, **keywords: Any) -> None:
        # Returning None turns the redirect into an HTTPError instead of following it.
        return None


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return float(value)
    return None


class OpenAICompatibleClient:
    """A bounded structured-inference client. Satisfies `llm_client.LlmClient`."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        send_temperature: bool = True,
        reasoning_effort: str | None = None,
        max_cost_usd: float | None = None,
        usd_per_million_tokens: tuple[float, float] | None = None,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        if not model:
            raise ValueError("a model must be named")
        if not api_key:
            raise ValueError("an API key is required")
        if reasoning_effort is not None and reasoning_effort not in REASONING_EFFORTS:
            raise ValueError(f"reasoning effort must be one of {', '.join(REASONING_EFFORTS)}")
        self.model = model
        self.url = endpoint(base_url)
        self.send_temperature = send_temperature
        self.reasoning_effort = reasoning_effort
        self.max_cost_usd = max_cost_usd
        self.usd_per_million_tokens = usd_per_million_tokens
        self.calls: list[CallRecord] = []
        self._api_key = api_key
        self._open = opener or urllib.request.build_opener(_NoRedirect).open

    @classmethod
    def from_environment(
        cls, *, api_key_env: str = DEFAULT_API_KEY_ENV, **options: Any
    ) -> OpenAICompatibleClient:
        """Read the key from `api_key_env`; the error names the variable, never a value."""
        api_key = os.environ.get(api_key_env, "")
        if not api_key:
            raise ValueError(f"no {api_key_env} in the environment")
        return cls(api_key=api_key, **options)

    def __repr__(self) -> str:
        return f"OpenAICompatibleClient(model={self.model!r}, url={self.url!r})"

    @property
    def spent(self) -> float:
        """What the calls so far cost, as reported, or as estimated from their tokens."""
        return sum(call.cost or 0.0 for call in self.calls)

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
        del purpose  # The request has no slot for it.
        refusal = self._budget_refusal()
        if refusal is not None:
            self.calls.append(CallRecord(outcome="refused_budget", error=refusal))
            raise BudgetExhausted(refusal)
        text = "\n\n".join(
            str(item.get("text", "")) for item in input if item.get("type") == "text"
        )
        if not text:
            raise ValueError("structured request carried no text content")
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": text},
            ],
            "max_completion_tokens": max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": False, "schema": json_schema},
            },
            "provider": {"require_parameters": True},
        }
        if self.send_temperature:
            body["temperature"] = temperature
        if self.reasoning_effort is not None:
            body["reasoning"] = {"effort": self.reasoning_effort}
        request = urllib.request.Request(  # noqa: S310 - endpoint() admits HTTPS or loopback HTTP
            self.url,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "User-Agent": "semantic-diff-weaver-benchmark",
            },
        )
        record = CallRecord()
        started = time.perf_counter()
        try:
            raw = self._send(request, timeout, record)
            return self._read(raw, record)
        finally:
            record.seconds = time.perf_counter() - started
            if record.cost is None:
                record.cost = self._estimate(record)
                record.estimated = record.cost is not None
            record.unaccounted = record.cost is None and record.outcome in REPLIED
            self.calls.append(record)

    def _budget_refusal(self) -> str | None:
        if self.max_cost_usd is None:
            return None
        if self.spent >= self.max_cost_usd:
            return f"the spending cap of {self.max_cost_usd} USD was reached"
        if any(call.unaccounted for call in self.calls):
            # A billed call of unknown cost leaves the cap unenforceable from then on.
            return "a previous call's cost is unknown, so the spending cap cannot be kept"
        return None

    def _estimate(self, record: CallRecord) -> float | None:
        if self.usd_per_million_tokens is None:
            return None
        if record.input_tokens is None and record.output_tokens is None:
            return None
        input_price, output_price = self.usd_per_million_tokens
        return (
            (record.input_tokens or 0) * input_price + (record.output_tokens or 0) * output_price
        ) / 1_000_000

    def _send(self, request: urllib.request.Request, timeout: int, record: CallRecord) -> bytes:
        try:
            with self._open(request, timeout=timeout) as response:
                record.status = getattr(response, "status", None)
                return bytes(response.read(MAX_RESPONSE_BYTES + 1))
        except urllib.error.HTTPError as exc:
            record.status = exc.code
            record.outcome = f"http_{exc.code}"
            record.error = self._error_message(exc)
            raise ProviderUnavailable(f"the provider answered HTTP {exc.code}") from None
        except TimeoutError:
            record.outcome = "timeout"
            raise TimeoutError("the structured inference request timed out") from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                record.outcome = "timeout"
                raise TimeoutError("the structured inference request timed out") from None
            record.outcome = "transport"
            raise ProviderUnavailable("the provider could not be reached") from None
        except OSError:
            record.outcome = "transport"
            raise ProviderUnavailable("the provider could not be reached") from None

    def _error_message(self, exc: urllib.error.HTTPError) -> str | None:
        try:
            document = json.loads(exc.read(MAX_RESPONSE_BYTES))
        except (AttributeError, OSError, ValueError):
            return None
        error = document.get("error") if isinstance(document, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        return self._redacted(message) if isinstance(message, str) else None

    def _redacted(self, message: str) -> str:
        return message.replace(self._api_key, "[key]")[:MAX_ERROR_CHARS]

    def _read(self, raw: bytes, record: CallRecord) -> StructuredResult:
        if len(raw) > MAX_RESPONSE_BYTES:
            record.outcome = "oversized"
            raise ProviderUnavailable("the response exceeded its bound")
        try:
            document = json.loads(raw)
        except ValueError:
            record.outcome = "invalid_body"
            raise ProviderUnavailable("the provider's response was not JSON") from None
        if not isinstance(document, dict):
            record.outcome = "invalid_body"
            raise ProviderUnavailable("the provider's response was not a JSON object")
        usage = self._usage(document, record)
        provider = document.get("provider")
        record.provider = provider[:100] if isinstance(provider, str) else None
        error = document.get("error")
        if error is not None:
            # A failure during generation can arrive with HTTP 200.
            record.outcome = "error_body"
            message = error.get("message") if isinstance(error, dict) else None
            record.error = self._redacted(message) if isinstance(message, str) else None
            raise ProviderUnavailable("the provider reported an error")
        choices = document.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else None
        message = choice.get("message") if isinstance(choice, dict) else None
        if not isinstance(choice, dict) or not isinstance(message, dict):
            record.outcome = "invalid_body"
            raise ProviderUnavailable("the response carried no message")
        finish_reason = choice.get("finish_reason")
        record.finish_reason = finish_reason[:40] if isinstance(finish_reason, str) else None
        if record.finish_reason == "length":
            # Cut off at the token budget, possibly by reasoning: a schema failure, retried once
            # with twice the budget.
            record.outcome = "length"
            raise ValueError("the reply was cut off at its token budget")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            if message.get("refusal"):
                record.outcome = "refusal"
                raise ProviderUnavailable("the model declined the request")
            record.outcome = "no_content"
            raise ValueError("the reply carried no content")
        fenced = FENCED.match(content)
        if fenced is not None:
            record.fenced = True
            content = fenced.group("body")
        try:
            parsed = json.loads(content)
        except ValueError:
            record.outcome = "invalid_json"
            raise ValueError("the reply was not valid JSON") from None
        try:
            LlmBatchResponse.model_validate(parsed)
        except ValidationError:
            # The interpreter validates it again and falls back; the record says why.
            record.outcome = "schema_invalid"
        return StructuredResult(content_type=CONTENT_TYPE_JSON, parsed=parsed, usage=usage)

    @staticmethod
    def _usage(document: dict[str, Any], record: CallRecord) -> Usage | None:
        usage = document.get("usage")
        if not isinstance(usage, dict):
            return None
        record.input_tokens = _integer(usage.get("prompt_tokens"))
        record.output_tokens = _integer(usage.get("completion_tokens"))
        record.cost = _number(usage.get("cost"))
        details = usage.get("completion_tokens_details")
        if isinstance(details, dict):
            record.reasoning_tokens = _integer(details.get("reasoning_tokens"))
        if record.input_tokens is None and record.output_tokens is None and record.cost is None:
            return None
        return Usage(
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cost=record.cost,
        )
