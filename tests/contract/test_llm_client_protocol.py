"""The structured-inference contract is a protocol, and the suite's doubles satisfy it.

`LlmClient` is what makes "Hermes plugin" into "engine with a Hermes adapter": the whole
coupling to a host is one method. These tests pin both halves of that claim — the doubles
used across the suite are assignable to the protocol, and the protocol's own parameter list
still matches the single call site in `semantic_interpreter._call`.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from semantic_diff_weaver.llm_client import LlmClient
from semantic_diff_weaver.plugin import handle_analyze_semantic_diff
from semantic_diff_weaver.semantic_interpreter import interpret_candidates
from semantic_diff_weaver.service import analyze

from .test_llm_call import FakeLlm, RaisingLlm, Result

# Static assignments, deliberately free of `# type: ignore`. A double whose
# `complete_structured` stopped matching the protocol would fail `mypy` here.
_FAKE: LlmClient = FakeLlm([])
_RAISING: LlmClient = RaisingLlm(TimeoutError("bounded"))
_ABSENT: LlmClient | None = None


class WellFormedClient:
    """A minimal implementation spelling every parameter out explicitly."""

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
    ) -> Result:
        del instructions, input, json_schema, schema_name, temperature, max_tokens
        del timeout, purpose
        return Result(content_type="json", parsed={"behavior_changes": []})


_EXPLICIT: LlmClient = WellFormedClient()

PROTOCOL_PARAMETERS = {
    "instructions",
    "input",
    "json_schema",
    "schema_name",
    "temperature",
    "max_tokens",
    "timeout",
    "purpose",
}


def satisfies_protocol(candidate: type) -> bool:
    """Mirror the structural rule `mypy` applies to `LlmClient` at an assignment.

    `tests/` sits outside the configured `mypy` `files`, so the static assignments above are
    only checked when someone points the type checker at this module. This predicate makes
    the same conformance rule fail the suite in CI: accept a catch-all `**kwargs`, otherwise
    require every protocol parameter as keyword-only.
    """
    method = getattr(candidate, "complete_structured", None)
    if method is None or not callable(method):
        return False
    parameters = inspect.signature(method).parameters
    if any(item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values()):
        return True
    accepted = {
        name for name, item in parameters.items() if item.kind is inspect.Parameter.KEYWORD_ONLY
    }
    return PROTOCOL_PARAMETERS <= accepted


def test_protocol_declares_exactly_the_parameters_the_call_site_passes() -> None:
    declared = inspect.signature(LlmClient.complete_structured).parameters
    assert set(declared) - {"self"} == PROTOCOL_PARAMETERS
    assert all(
        declared[name].kind is inspect.Parameter.KEYWORD_ONLY for name in PROTOCOL_PARAMETERS
    )


def test_suite_doubles_and_explicit_implementations_satisfy_the_protocol() -> None:
    for client in (_FAKE, _RAISING, _EXPLICIT):
        assert callable(client.complete_structured)
        assert satisfies_protocol(type(client))
    explicit = inspect.signature(WellFormedClient.complete_structured).parameters
    assert set(explicit) - {"self"} == PROTOCOL_PARAMETERS


def test_a_mismatched_signature_is_rejected() -> None:
    class WrongParameters:
        def complete_structured(self, *, instructions: str, oops: int) -> Any:
            del instructions, oops
            return None

    class PositionalOnly:
        def complete_structured(self, instructions: str) -> Any:
            del instructions
            return None

    class NoMethod:
        pass

    assert not satisfies_protocol(WrongParameters)
    assert not satisfies_protocol(PositionalOnly)
    assert not satisfies_protocol(NoMethod)


def test_the_protocol_is_the_only_coupling_to_a_host() -> None:
    """Every injection point accepts the protocol or None, and nothing wider."""
    for function in (analyze, interpret_candidates, handle_analyze_semantic_diff):
        annotation = inspect.signature(function).parameters["llm"].annotation
        assert annotation == "LlmClient | None", (function.__name__, annotation)


def test_a_client_missing_the_method_fails_when_inference_is_attempted() -> None:
    class NotAClient:
        pass

    with pytest.raises(AttributeError):
        NotAClient().complete_structured()  # type: ignore[attr-defined]
