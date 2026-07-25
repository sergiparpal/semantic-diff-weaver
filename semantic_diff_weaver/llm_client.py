"""The structured-inference contract this engine depends on.

Exactly one method is ever invoked on the injected client, from
``semantic_interpreter._call``. Naming it as a protocol makes the whole coupling to a host
explicit in the type system: Hermes' ``PluginContext.llm`` satisfies it structurally without
change, and any other provider adapter is a peer rather than a special case.

Implementations are expected to return an object exposing ``content_type`` and ``parsed``,
either as attributes or as mapping keys, plus an optional ``usage``. That shape is read by
``semantic_interpreter._parse_structured_result`` and ``_accumulate_usage``, which tolerate
both spellings, so this protocol deliberately does not over-specify the return type.
"""

from __future__ import annotations

from typing import Any, Protocol


class LlmClient(Protocol):
    """A host or provider capable of one bounded, schema-constrained completion."""

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
    ) -> Any: ...
