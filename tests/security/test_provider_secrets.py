"""The API key never reaches output, and a broken provider never breaks the analysis.

Both properties are end-to-end: the analysis runs through `service.analyze` with the real
adapter in front of a stubbed transport, so what is asserted is the rendered brief and the
public error payload, not an internal helper's return value.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from semantic_diff_weaver.errors import WeaverError, as_public_error
from semantic_diff_weaver.plugin import handle_analyze_semantic_diff
from semantic_diff_weaver.providers.anthropic_client import (
    API_KEY_ENV,
    DEFAULT_MODEL,
    AnthropicClient,
)
from semantic_diff_weaver.service import analyze

SECRET = "sk-ant-api03-DO-NOT-LEAK-abcdefghijklmnopqrstuvwxyz0123456789"

BOUNDARY_OLD = {"api.py": "def allowed(x):\n    return x < 5\n"}
BOUNDARY_NEW = {"api.py": "def allowed(x):\n    return x <= 5\n"}


class Failing:
    """A transport whose every failure mode embeds the key in its message."""

    class _Messages:
        def __init__(self, error: Exception) -> None:
            self.error = error

        def create(self, **kwargs: Any) -> Any:
            raise self.error

    def __init__(self, error: Exception) -> None:
        self.messages = self._Messages(error)

    def with_options(self, *, timeout: int) -> Failing:
        del timeout
        return self


@dataclass
class Block:
    type: str
    text: str


@dataclass
class Response:
    content: list[Block]
    usage: Any = None
    stop_reason: str | None = "end_turn"


class Answering:
    """A transport that answers with a caller-supplied response object."""

    class _Messages:
        def __init__(self, response: Any) -> None:
            self.response = response

        def create(self, **kwargs: Any) -> Any:
            return self.response

    def __init__(self, response: Any) -> None:
        self.messages = self._Messages(response)

    def with_options(self, *, timeout: int) -> Answering:
        del timeout
        return self


def malformed() -> Answering:
    """Output the schema cannot accept."""
    return Answering(Response(content=[Block(type="text", text="{not valid json")]))


def _run(repo_factory, transport: Any) -> dict[str, Any]:
    repo, base, head = repo_factory(BOUNDARY_OLD, BOUNDARY_NEW)
    client = AnthropicClient(model=DEFAULT_MODEL, transport=transport)
    return analyze(
        {
            "repo_path": str(repo),
            "base_ref": base,
            "head_ref": head,
            "output_format": "both",
        },
        llm=client,
    )


def test_a_provider_error_carrying_the_key_never_reaches_the_brief(repo_factory) -> None:
    envelope = _run(repo_factory, Failing(RuntimeError(f"401 unauthorized: x-api-key={SECRET}")))
    rendered = json.dumps(envelope, ensure_ascii=False)
    assert SECRET not in rendered
    assert "sk-ant" not in rendered
    assert envelope["analysis"]["success"] is True
    assert "## Semantic Diff Test Brief" in envelope["markdown"]


def test_a_provider_error_carrying_the_key_never_reaches_an_error_payload() -> None:
    """`WeaverError` payloads are the other public surface the key must not reach."""
    exception = RuntimeError(f"connection refused (key {SECRET})")
    payload = as_public_error(exception)
    assert SECRET not in json.dumps(payload)
    assert payload["error"] == "internal_error"

    wrapped = AnthropicClient._transport_failure(exception)
    assert SECRET not in str(wrapped)
    assert SECRET not in json.dumps(as_public_error(wrapped))


def test_the_environment_key_is_never_echoed_by_provider_resolution(monkeypatch) -> None:
    monkeypatch.setenv(API_KEY_ENV, SECRET)

    class Module:
        @staticmethod
        def Anthropic() -> Any:
            raise RuntimeError(f"bad credential {SECRET}")

    monkeypatch.setitem(__import__("sys").modules, "anthropic", Module())
    client, notice = AnthropicClient.from_environment()
    assert client is None
    assert notice is not None
    assert SECRET not in notice


def test_a_schema_failure_degrades_to_deterministic_findings(repo_factory) -> None:
    envelope = _run(repo_factory, malformed())
    analysis = envelope["analysis"]
    assert analysis["success"] is True
    assert analysis["deterministic_mode"] is True
    assert analysis["behavior_changes"], "structural findings must survive a provider failure"
    assert all(item["origin"] == "deterministic_fallback" for item in analysis["behavior_changes"])


def test_a_provider_failure_degrades_to_deterministic_findings(repo_factory) -> None:
    envelope = _run(repo_factory, Failing(RuntimeError("service unavailable")))
    analysis = envelope["analysis"]
    assert analysis["success"] is True
    assert analysis["deterministic_mode"] is True
    assert analysis["behavior_changes"]


def test_a_missing_key_analyzes_successfully_in_deterministic_mode(
    repo_factory, monkeypatch, capsys
) -> None:
    """The plan's requirement: missing credentials must never be a hard failure."""
    from semantic_diff_weaver.cli import EXIT_SUCCESS, main
    from semantic_diff_weaver.path_policy import ALLOWED_ROOTS_ENV

    monkeypatch.delenv(API_KEY_ENV, raising=False)
    monkeypatch.delenv(ALLOWED_ROOTS_ENV, raising=False)
    repo, base, head = repo_factory(BOUNDARY_OLD, BOUNDARY_NEW)
    code = main(["--repo", str(repo), "--base", base, "--head", head, "--format", "json"])
    captured = capsys.readouterr()
    assert code == EXIT_SUCCESS
    assert f"no {API_KEY_ENV}" in captured.err
    payload = json.loads(captured.out)
    assert payload["success"] is True
    assert payload["deterministic_mode"] is True


def test_no_test_in_this_module_requires_a_real_credential(monkeypatch) -> None:
    """Guard the offline invariant itself."""
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    client, notice = AnthropicClient.from_environment()
    assert client is None and notice is not None


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_unusable_provider_responses_do_not_fail_the_analysis(
    repo_factory, stop_reason: str
) -> None:
    envelope = _run(repo_factory, Answering(Response(content=[], stop_reason=stop_reason)))
    assert envelope["analysis"]["success"] is True
    assert envelope["analysis"]["deterministic_mode"] is True


def test_a_weaver_error_from_the_provider_path_stays_public(repo_factory) -> None:
    """A provider problem must never surface as an unmapped exception to a caller."""
    repo, base, head = repo_factory(BOUNDARY_OLD, BOUNDARY_NEW)
    client = AnthropicClient(
        model=DEFAULT_MODEL, transport=Failing(WeaverError.__new__(WeaverError))
    )
    rendered = handle_analyze_semantic_diff(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head}, llm=client
    )
    payload = json.loads(rendered)
    assert payload["success"] is True
    assert SECRET not in rendered
