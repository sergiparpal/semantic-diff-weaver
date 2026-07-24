"""Emit structural deltas between matched symbol snapshots."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..git_diff import Hunk
from ..models import LineRange
from ..path_policy import redact_text
from .limits import FEATURE_DELTA_KINDS
from .match import symbol_similarity
from .types import StructuralDelta, SymbolSnapshot

ORDER_SENSITIVE_KINDS = {
    "call_order_change",
    "comparison_change",
    "condition_change",
    "condition_order_change",
    "raise_change",
    "return_change",
    "assignment_change",
    "loop_change",
}


def ranges_overlap(start: int, end: int, hunk_start: int, hunk_count: int) -> bool:
    if hunk_count == 0:
        return start <= hunk_start <= end + 1
    return start <= hunk_start + hunk_count - 1 and hunk_start <= end


def matching_hunk(
    old: SymbolSnapshot | None,
    new: SymbolSnapshot | None,
    *,
    old_hunks: list[Hunk],
    new_hunks: list[Hunk],
    old_path: str,
    new_path: str,
) -> str | None:
    for hunk in new_hunks:
        if new and ranges_overlap(new.start, new.end, hunk.new_start, hunk.new_count):
            return f"{new_path}#{hunk.id}"
    for hunk in old_hunks:
        if old and ranges_overlap(old.start, old.end, hunk.old_start, hunk.old_count):
            return f"{old_path}#{hunk.id}"
    return None


def join_feature_values(values: Iterable[tuple[str, int]]) -> str | None:
    material = [text for text, _ in values]
    return redact_text("; ".join(material), max_chars=1500) if material else None


def line_ranges(
    old: SymbolSnapshot | None, new: SymbolSnapshot | None
) -> tuple[LineRange | None, LineRange | None]:
    old_range = LineRange(start=old.start, end=old.end) if old else None
    new_range = LineRange(start=new.start, end=new.end) if new else None
    return old_range, new_range


def make_structural_delta(
    path: str,
    old: SymbolSnapshot | None,
    new: SymbolSnapshot | None,
    hunk_id: str | None,
    kind: str,
    old_value: str | None,
    new_value: str | None,
    **metadata: Any,
) -> StructuralDelta:
    old_lines, new_lines = line_ranges(old, new)
    if new is not None:
        symbol_name = new.qualified_name
    elif old is not None:
        symbol_name = old.qualified_name
    else:
        symbol_name = "<unknown>"
    return StructuralDelta(
        path=path,
        symbol=symbol_name,
        kind=kind,
        old=redact_text(old_value, max_chars=1500) if old_value else None,
        new=redact_text(new_value, max_chars=1500) if new_value else None,
        old_lines=old_lines,
        new_lines=new_lines,
        hunk_id=hunk_id,
        metadata=metadata,
    )


def feature_texts(snapshot: SymbolSnapshot, feature: str) -> list[str]:
    return [text for text, _ in snapshot.features[feature]]


def suppress_redundant_deltas(deltas: list[StructuralDelta]) -> list[StructuralDelta]:
    """Drop overlapping feature deltas that are explained by a more specific kind.

    Raise changes already imply the underlying call site moved. Pure call-shaped returns
    are also redundant once a call_change is present.
    """
    result = list(deltas)
    result_kinds = {item.kind for item in result}
    if "raise_change" in result_kinds and "call_change" in result_kinds:
        result = [item for item in result if item.kind != "call_change"]
        result_kinds.discard("call_change")
    if "return_change" in result_kinds and "comparison_change" in result_kinds:
        result = [item for item in result if item.kind != "return_change"]
    return result


def _append_call_delta(
    result: list[StructuralDelta],
    path: str,
    old: SymbolSnapshot,
    new: SymbolSnapshot,
    hunk_id: str | None,
) -> None:
    old_names = feature_texts(old, "calls")
    new_names = feature_texts(new, "calls")
    if old_names == new_names:
        return
    kind = "call_order_change" if sorted(old_names) == sorted(new_names) else "call_change"
    result.append(
        make_structural_delta(
            path,
            old,
            new,
            hunk_id,
            kind,
            "; ".join(old_names),
            "; ".join(new_names),
            old_calls=[item.split("(", 1)[0] for item in old_names],
            new_calls=[item.split("(", 1)[0] for item in new_names],
        )
    )


def _maybe_drop_return_when_call_explained(
    result: list[StructuralDelta], old: SymbolSnapshot, new: SymbolSnapshot
) -> list[StructuralDelta]:
    result_kinds = {item.kind for item in result}
    if "return_change" not in result_kinds or "call_change" not in result_kinds:
        return result
    return_values = [
        text.lstrip() for text in (*feature_texts(old, "returns"), *feature_texts(new, "returns"))
    ]
    if return_values and all(
        "(" in value and not value.startswith(("{", "[", "(")) for value in return_values
    ):
        return [item for item in result if item.kind != "return_change"]
    return result


def _append_feature_deltas(
    result: list[StructuralDelta],
    path: str,
    old: SymbolSnapshot,
    new: SymbolSnapshot,
    hunk_id: str | None,
) -> None:
    for feature, kind in FEATURE_DELTA_KINDS:
        old_values = feature_texts(old, feature)
        new_values = feature_texts(new, feature)
        if old_values == new_values:
            continue
        effective_kind = (
            "condition_order_change"
            if feature == "conditions" and sorted(old_values) == sorted(new_values)
            else kind
        )
        result.append(
            make_structural_delta(
                path,
                old,
                new,
                hunk_id,
                effective_kind,
                join_feature_values(old.features[feature]),
                join_feature_values(new.features[feature]),
            )
        )


def compare_symbol(
    path: str,
    old: SymbolSnapshot | None,
    new: SymbolSnapshot | None,
    hunks: list[Hunk],
    *,
    old_path: str | None = None,
    new_path: str | None = None,
    old_hunks: list[Hunk] | None = None,
    new_hunks: list[Hunk] | None = None,
) -> list[StructuralDelta]:
    effective_old_path = old_path or path
    effective_new_path = new_path or path
    hunk_id = matching_hunk(
        old,
        new,
        old_hunks=old_hunks if old_hunks is not None else hunks,
        new_hunks=new_hunks if new_hunks is not None else hunks,
        old_path=effective_old_path,
        new_path=effective_new_path,
    )
    if hunk_id is None:
        return []
    if old is None:
        if new is None:
            return []
        return [
            make_structural_delta(
                path,
                old,
                new,
                hunk_id,
                "symbol_added",
                None,
                new.signature,
                ambiguous_match=new.match_ambiguous,
            )
        ]
    if new is None:
        return [
            make_structural_delta(
                path,
                old,
                new,
                hunk_id,
                "symbol_removed",
                old.signature,
                None,
                ambiguous_match=old.match_ambiguous,
            )
        ]
    result: list[StructuralDelta] = []
    if old.signature != new.signature:
        result.append(
            make_structural_delta(
                path,
                old,
                new,
                hunk_id,
                "signature_change",
                old.signature,
                new.signature,
                default_changed=old.default_map != new.default_map,
                old_defaults=old.default_map,
                new_defaults=new.default_map,
            )
        )
    if old.decorators != new.decorators:
        result.append(
            make_structural_delta(
                path,
                old,
                new,
                hunk_id,
                "decorator_change",
                ", ".join(old.decorators),
                ", ".join(new.decorators),
            )
        )
    _append_feature_deltas(result, path, old, new, hunk_id)
    _append_call_delta(result, path, old, new, hunk_id)
    result = suppress_redundant_deltas(result)
    result = _maybe_drop_return_when_call_explained(result, old, new)
    if old.statement_order != new.statement_order and not any(
        item.kind in ORDER_SENSITIVE_KINDS for item in result
    ):
        result.append(
            make_structural_delta(
                path,
                old,
                new,
                hunk_id,
                "statement_order_change",
                "; ".join(old.statement_order),
                "; ".join(new.statement_order),
            )
        )
    if not result and (
        old.qualified_name != new.qualified_name or old.fingerprint == new.fingerprint
    ):
        result.append(
            make_structural_delta(
                path,
                old,
                new,
                hunk_id,
                "structural_refactor",
                old.qualified_name,
                new.qualified_name,
                materiality=round(max(0.0, 1.0 - symbol_similarity(old, new)), 3),
            )
        )
    elif old.fingerprint != new.fingerprint and not result:
        result.append(
            make_structural_delta(
                path, old, new, hunk_id, "unknown_structure", "body changed", "body changed"
            )
        )
    if effective_old_path != effective_new_path:
        for item in result:
            item.metadata.update(old_path=effective_old_path, new_path=effective_new_path)
    return result


# Backward-compatible private aliases.
_compare_symbol = compare_symbol
_delta = make_structural_delta
_overlaps = ranges_overlap
_matching_hunk = matching_hunk
_summary = join_feature_values
_ranges = line_ranges
