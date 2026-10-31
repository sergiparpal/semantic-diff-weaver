"""Emit structural deltas between matched symbol snapshots."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Any

from ..models import LineRange
from ..path_policy import redact_text
from ..source import SourceHunk
from .limits import (
    CHANGED_VALUE_FEATURES,
    FEATURE_DELTA_KINDS,
    MAX_DELTA_TEXT_CHARS,
    MAX_PROBE_CHARS_PER_SIDE,
)
from .match import symbol_similarity
from .types import CategoryProbe, StructuralDelta, SymbolSnapshot

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


def is_reordering(old_order: Sequence[str], new_order: Sequence[str]) -> bool:
    """Report whether two statement sequences are a permutation of one another.

    ``statement_order`` entries embed unparsed expression content, not just sequence
    identity, so plain inequality also fires on a pure content edit. Ordering means the
    same statements occur in a different sequence, which is exactly a permutation — the
    same test ``_append_call_delta`` already applies to call names.
    """
    return old_order != new_order and sorted(old_order) == sorted(new_order)


def ranges_overlap(start: int, end: int, hunk_start: int, hunk_count: int) -> bool:
    if hunk_count == 0:
        return start <= hunk_start <= end + 1
    return start <= hunk_start + hunk_count - 1 and hunk_start <= end


def matching_hunk(
    old: SymbolSnapshot | None,
    new: SymbolSnapshot | None,
    *,
    old_hunks: Sequence[SourceHunk],
    new_hunks: Sequence[SourceHunk],
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
    return redact_text("; ".join(material), max_chars=MAX_DELTA_TEXT_CHARS) if material else None


def _values_in_display_window(
    snapshot: SymbolSnapshot, feature: str
) -> list[tuple[str, CategoryProbe]]:
    """The values a delta's display text shows, each with its probe.

    The display text joins every value with "; " and stops at `MAX_DELTA_TEXT_CHARS`, so a long
    symbol shows — and has always been classified by — only its first values. Covering the same
    values keeps what a probe changes about classification to prose literals and nothing else;
    a denser probe that reached further was finding new keywords in calls the text never showed.
    """
    kept: list[tuple[str, CategoryProbe]] = []
    offset = 0
    pairs = zip(snapshot.features[feature], snapshot.probes[feature], strict=True)
    for (text, _), probe in pairs:
        if offset >= MAX_DELTA_TEXT_CHARS:
            break
        kept.append((text, probe))
        offset += len(text) + len("; ")
    return kept


def _changed_probes(
    shown: list[tuple[str, CategoryProbe]], other: SymbolSnapshot, feature: str
) -> list[CategoryProbe]:
    """The probes of the shown values the other side does not hold as many times."""
    remaining = Counter(feature_texts(other, feature))
    changed: list[CategoryProbe] = []
    for text, probe in shown:
        if remaining[text]:
            remaining[text] -= 1
        else:
            changed.append(probe)
    return changed


def join_probes(old: SymbolSnapshot, new: SymbolSnapshot, feature: str) -> CategoryProbe | None:
    """Both sides' category probes for `feature`, or None when either side recorded none.

    None sends the category rules back to the display text, which is what a feature without
    probes — or a snapshot built by hand — has always been classified by. Each side's words are
    bounded on their own, so a long old side can never crowd out the new one.

    Whether the delta compares a boundary is read from the values it changed: an unchanged
    `len(items) > 3` made every condition changed beside it, a `parent is None` among them, a
    boundary. So are the words of a comparison or condition (`CHANGED_VALUE_FEATURES`); those of
    a call or an assignment are still the words of every value shown.
    """
    if feature not in old.probes or feature not in new.probes:
        return None
    old_shown, new_shown = (_values_in_display_window(snapshot, feature) for snapshot in (old, new))
    old_changed = _changed_probes(old_shown, new, feature)
    new_changed = _changed_probes(new_shown, old, feature)
    sides = (
        (old_changed, new_changed)
        if feature in CHANGED_VALUE_FEATURES
        else ([probe for _, probe in old_shown], [probe for _, probe in new_shown])
    )
    return CategoryProbe(
        words=" ".join(
            " ".join(probe.words for probe in side)[:MAX_PROBE_CHARS_PER_SIDE] for side in sides
        ),
        compares=any(probe.compares for probe in (*old_changed, *new_changed)),
    )


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
    *,
    probe: CategoryProbe | None = None,
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
        old=redact_text(old_value, max_chars=MAX_DELTA_TEXT_CHARS) if old_value else None,
        new=redact_text(new_value, max_chars=MAX_DELTA_TEXT_CHARS) if new_value else None,
        old_lines=old_lines,
        new_lines=new_lines,
        hunk_id=hunk_id,
        metadata=metadata,
        probe=probe,
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
            probe=join_probes(old, new, "calls"),
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


def _changed_assignment_targets(old: SymbolSnapshot, new: SymbolSnapshot) -> list[str]:
    """The variables written by the assignments that differ between the two sides.

    A module's assignment delta shows every module-level assignment, so only this tells which
    changed: `CODE_ROOTS` when that tuple alone gained an entry. A snapshot built without the
    targets it writes to names none.
    """
    sides: list[Counter[tuple[str, frozenset[str]]]] = []
    for snapshot in (old, new):
        values = snapshot.features["assignments"]
        if len(snapshot.assigned) != len(values):
            return []
        sides.append(Counter(zip((text for text, _ in values), snapshot.assigned, strict=True)))
    old_side, new_side = sides
    changed = (old_side - new_side) + (new_side - old_side)
    return sorted({name for _, names in changed for name in names})


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
        metadata = (
            {"assigned_names": _changed_assignment_targets(old, new)}
            if feature == "assignments"
            else {}
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
                probe=join_probes(old, new, feature),
                **metadata,
            )
        )


def compare_symbol(
    path: str,
    old: SymbolSnapshot | None,
    new: SymbolSnapshot | None,
    hunks: Sequence[SourceHunk],
    *,
    old_path: str | None = None,
    new_path: str | None = None,
    old_hunks: Sequence[SourceHunk] | None = None,
    new_hunks: Sequence[SourceHunk] | None = None,
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
        if new.kind == "module" and new.docstring_only:
            # Top-level code that is only a docstring runs nothing when the module is imported,
            # like an empty `__init__.py`, whose diff has no hunk to report. Its definitions
            # are symbols of their own, and a removed module still breaks what imported it.
            return []
        return [
            make_structural_delta(
                path,
                old,
                new,
                hunk_id,
                "symbol_added",
                None,
                new.display or new.signature,
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
                old.display or old.signature,
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
    if is_reordering(old.statement_order, new.statement_order) and not any(
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
    # ``not result`` is load-bearing, not redundant with the branch above: that branch's own
    # condition is a conjunction, so this one is reached whenever ``result`` is non-empty too.
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
