"""The deltas a document's comparison makes, as `compare` and `settings` both make them: bounded in
number, each named by one of the document's own keys, cut to length, and shown redacted."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..ast_diff import StructuralDelta
from ..path_policy import redact_text

MAX_DOCUMENT_DELTAS = 500
MAX_SHOWN_CHARS = 300
# A symbol is one of the document's own keys, `GET /orders/{id}`, `Order`, or `database.port`,
# and every delta of it repeats it in the output, so a long one is cut.
MAX_SYMBOL_CHARS = 200


class _Absent:
    """A key a mapping does not have, which is not the same as one holding `null`."""


ABSENT: Any = _Absent()


def shown(location: str, value: Any) -> str | None:
    """The value as evidence text: where it is, then its JSON, bounded and redacted."""
    if value is ABSENT:
        return None
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(", ", ": "))
    # Cut well past what is shown before redacting: a large subtree then costs no more to redact
    # than a small one, and a credential that starts within what is shown stays whole enough for
    # its pattern to match.
    return redact_text(f"{location}: {text}"[: MAX_SHOWN_CHARS * 4], max_chars=MAX_SHOWN_CHARS)


@dataclass
class Deltas:
    """One document's deltas, up to the budget, and whether one past it was left out."""

    path: str
    items: list[StructuralDelta] = field(default_factory=list)
    truncated: bool = False

    def add(
        self,
        symbol: str,
        kind: str,
        old: Any,
        new: Any,
        *,
        location: str,
        context: str,
        **metadata: Any,
    ) -> None:
        if len(self.items) >= MAX_DOCUMENT_DELTAS:
            self.truncated = True
            return
        if len(symbol) > MAX_SYMBOL_CHARS:
            symbol = symbol[: MAX_SYMBOL_CHARS - 3] + "..."
        self.items.append(
            StructuralDelta(
                path=self.path,
                symbol=symbol,
                kind=kind,
                old=shown(location, old),
                new=shown(location, new),
                old_lines=None,
                new_lines=None,
                hunk_id=None,
                metadata={"location": location, "context": context, **metadata},
            )
        )
