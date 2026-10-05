"""AST analysis value types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import LineRange


def reparameterize(signature: str) -> str:
    """Normalize identifiers while preserving argument structure for rename matching."""
    result: list[str] = []
    in_identifier = False
    for char in signature:
        if char.isalnum() or char == "_":
            if not in_identifier:
                result.append("_")
                in_identifier = True
        else:
            result.append(char)
            in_identifier = False
    return "".join(result)


@dataclass
class SymbolSnapshot:
    qualified_name: str
    kind: str
    start: int
    end: int
    signature: str
    default_map: dict[str, str]
    decorators: tuple[str, ...]
    fingerprint: str
    features: dict[str, tuple[tuple[str, int], ...]]
    statement_order: tuple[str, ...]
    node_inventory: tuple[tuple[str, int], ...]
    match_ambiguous: bool = False
    # Per probed feature, one category probe per recorded value, in the same order.
    probes: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def signature_shape(self) -> str:
        return reparameterize(self.signature)


@dataclass
class StructuralDelta:
    path: str
    symbol: str
    kind: str
    old: str | None
    new: str | None
    old_lines: LineRange | None
    new_lines: LineRange | None
    hunk_id: str | None
    parser_complete: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    # The words the category rules may read, without prose string literals. Internal to
    # classification and never reported; None means "read `old` and `new` instead".
    probe: str | None = None


@dataclass
class AstAnalysis:
    deltas: list[StructuralDelta]
    warnings: list[str]
    parsed_files: int
    failed_files: int
    changed_symbols: int
    resource_limited_files: int = 0


@dataclass(frozen=True)
class SymbolPair:
    old: SymbolSnapshot | None
    new: SymbolSnapshot | None
    ambiguous: bool = False
