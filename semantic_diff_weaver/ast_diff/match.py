"""Conservative same-file and cross-file symbol matching."""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from collections.abc import Iterable, Sequence
from difflib import SequenceMatcher
from typing import TypeVar

from ..git_diff import ChangedFile
from . import limits
from .types import SymbolPair, SymbolSnapshot

T = TypeVar("T")


def multiset_similarity(
    left: tuple[tuple[str, int], ...], right: tuple[tuple[str, int], ...]
) -> float:
    left_counts = dict(left)
    right_counts = dict(right)
    keys = left_counts.keys() | right_counts.keys()
    total = sum(max(left_counts.get(key, 0), right_counts.get(key, 0)) for key in keys)
    if total == 0:
        return 1.0
    overlap = sum(min(left_counts.get(key, 0), right_counts.get(key, 0)) for key in keys)
    return overlap / total


def set_similarity(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    return len(left & right) / len(left | right)


def symbol_similarity(old: SymbolSnapshot, new: SymbolSnapshot) -> float:
    if old.kind != new.kind:
        return 0.0
    old_parent = old.qualified_name.rpartition(".")[0]
    new_parent = new.qualified_name.rpartition(".")[0]
    signature = SequenceMatcher(None, old.signature_shape, new.signature_shape).ratio()
    calls = set_similarity(
        {value.split("(", 1)[0] for value, _ in old.features["calls"]},
        {value.split("(", 1)[0] for value, _ in new.features["calls"]},
    )
    old_feature_counts = tuple(sorted((name, len(values)) for name, values in old.features.items()))
    new_feature_counts = tuple(sorted((name, len(values)) for name, values in new.features.items()))
    order = set_similarity(
        {item.split(":", 1)[0] for item in old.statement_order},
        {item.split(":", 1)[0] for item in new.statement_order},
    )
    return min(
        1.0,
        signature * 0.20
        + (0.10 if old_parent == new_parent else 0.0)
        + multiset_similarity(old.node_inventory, new.node_inventory) * 0.25
        + calls * 0.15
        + multiset_similarity(old_feature_counts, new_feature_counts) * 0.15
        + order * 0.10
        + (0.05 if old.fingerprint == new.fingerprint else 0.0),
    )


def mark_ambiguous(
    primary: SymbolSnapshot,
    peers: Iterable[SymbolSnapshot],
    warnings: list[str],
    message: str,
) -> None:
    primary.match_ambiguous = True
    for peer in peers:
        peer.match_ambiguous = True
    warnings.append(message)


def pick_unique_match(
    scored: Sequence[tuple[float, T]],
    *,
    accept: float = limits.SIMILARITY_ACCEPT_THRESHOLD,
    tie_margin: float = limits.SIMILARITY_TIE_MARGIN,
) -> tuple[T | None, bool]:
    """Return (winner, is_ambiguous) for ranked similarity candidates."""
    plausible = [item for item in scored if item[0] >= accept]
    if not plausible:
        return None, False
    if len(plausible) > 1 and plausible[0][0] - plausible[1][0] <= tie_margin:
        return None, True
    return plausible[0][1], False


def _available(
    items: list[SymbolSnapshot], matched: set[int], *, cap: int = limits.MAX_SIMILARITY_CANDIDATES
) -> list[SymbolSnapshot]:
    result: list[SymbolSnapshot] = []
    for item in items:
        if id(item) not in matched:
            result.append(item)
        if len(result) > cap:
            break
    return result


def match_symbols(
    old_symbols: list[SymbolSnapshot], new_symbols: list[SymbolSnapshot]
) -> tuple[list[SymbolPair], list[str]]:
    """Match symbols within one file while preserving overload-style duplicate names."""
    pairs: list[SymbolPair] = []
    warnings: list[str] = []
    old_by_name: dict[str, list[SymbolSnapshot]] = defaultdict(list)
    new_by_name: dict[str, list[SymbolSnapshot]] = defaultdict(list)
    for item in old_symbols:
        old_by_name[item.qualified_name].append(item)
    for item in new_symbols:
        new_by_name[item.qualified_name].append(item)
    matched_old: set[int] = set()
    matched_new: set[int] = set()
    for name in sorted(old_by_name.keys() & new_by_name.keys()):
        old_group = sorted(old_by_name[name], key=lambda item: item.start)
        new_group = sorted(new_by_name[name], key=lambda item: item.start)
        # Exact definition shapes preserve overloads even when their order changes.
        if len(old_group) * len(new_group) <= limits.MAX_EXACT_GROUP_COMPARISONS:
            for old in old_group:
                exact_candidates = [
                    new
                    for new in new_group
                    if id(new) not in matched_new
                    and old.kind == new.kind
                    and old.signature == new.signature
                    and old.fingerprint == new.fingerprint
                ]
                if len(exact_candidates) == 1:
                    new = exact_candidates[0]
                    pairs.append(SymbolPair(old, new))
                    matched_old.add(id(old))
                    matched_new.add(id(new))
        else:
            warnings.append(
                f"Large overload-like group for {name!r} was matched conservatively by source order."
            )
        remaining_old_same = [item for item in old_group if id(item) not in matched_old]
        remaining_new_same = [item for item in new_group if id(item) not in matched_new]
        # Exact qualified names are the authoritative next pass. Source order disambiguates
        # overload-like duplicates without discarding either definition.
        for old, new in zip(remaining_old_same, remaining_new_same, strict=False):
            pairs.append(SymbolPair(old, new))
            matched_old.add(id(old))
            matched_new.add(id(new))
        if len(old_group) != len(new_group) and max(len(old_group), len(new_group)) > 1:
            warnings.append(
                f"Overload-like definition count changed for {name!r}; unmatched definitions "
                "were retained explicitly."
            )
    remaining_old = sorted(
        (item for item in old_symbols if id(item) not in matched_old),
        key=lambda item: (item.qualified_name, item.start),
    )
    remaining_new = sorted(
        (item for item in new_symbols if id(item) not in matched_new),
        key=lambda item: (item.qualified_name, item.start),
    )
    candidates: dict[str, list[SymbolSnapshot]] = defaultdict(list)
    for item in remaining_new:
        candidates[f"{item.kind}:{item.signature_shape}:{item.fingerprint}"].append(item)
    unresolved: list[SymbolSnapshot] = []
    for old in remaining_old:
        key = f"{old.kind}:{old.signature_shape}:{old.fingerprint}"
        possible = _available(candidates.get(key, []), matched_new)
        if len(possible) == 1:
            new = possible[0]
            pairs.append(SymbolPair(old, new))
            matched_old.add(id(old))
            matched_new.add(id(new))
        elif len(possible) > 1:
            mark_ambiguous(
                old,
                possible[: limits.MAX_SIMILARITY_CANDIDATES],
                warnings,
                f"Ambiguous symbol match for {old.qualified_name!r}; treated conservatively.",
            )
        else:
            unresolved.append(old)
    new_by_kind: dict[str, list[SymbolSnapshot]] = defaultdict(list)
    starts_by_kind: dict[str, list[int]] = {}
    for item in remaining_new:
        new_by_kind[item.kind].append(item)
    for kind, items in new_by_kind.items():
        items.sort(key=lambda item: (item.start, item.qualified_name))
        starts_by_kind[kind] = [item.start for item in items]

    def nearby_candidates(old: SymbolSnapshot) -> list[SymbolSnapshot]:
        items = new_by_kind.get(old.kind, [])
        if len(items) <= limits.MAX_SIMILARITY_CANDIDATES:
            return [item for item in items if id(item) not in matched_new]
        insertion = bisect_left(starts_by_kind[old.kind], old.start)
        left = max(0, insertion - limits.MAX_SIMILARITY_CANDIDATES // 2)
        right = min(len(items), left + limits.MAX_SIMILARITY_CANDIDATES)
        left = max(0, right - limits.MAX_SIMILARITY_CANDIDATES)
        return [item for item in items[left:right] if id(item) not in matched_new]

    for old in unresolved:
        scored = sorted(
            ((symbol_similarity(old, new), new) for new in nearby_candidates(old)),
            key=lambda item: (-item[0], item[1].qualified_name, item[1].start),
        )
        winner, ambiguous = pick_unique_match(scored)
        if ambiguous:
            plausible_peers = [
                peer for score, peer in scored if score >= limits.SIMILARITY_ACCEPT_THRESHOLD
            ]
            mark_ambiguous(
                old,
                plausible_peers,
                warnings,
                f"Ambiguous similarity match for {old.qualified_name!r}; treated conservatively.",
            )
            continue
        if winner is None:
            continue
        pairs.append(SymbolPair(old, winner))
        matched_old.add(id(old))
        matched_new.add(id(winner))
    for old in remaining_old:
        if id(old) not in matched_old:
            pairs.append(SymbolPair(old, None))
    for new in remaining_new:
        if id(new) not in matched_new:
            pairs.append(SymbolPair(None, new))
    return pairs, warnings


def match_cross_file_symbols(
    removed: list[tuple[SymbolSnapshot, ChangedFile]],
    added: list[tuple[SymbolSnapshot, ChangedFile]],
) -> tuple[
    list[tuple[SymbolSnapshot, ChangedFile, SymbolSnapshot, ChangedFile]],
    list[tuple[SymbolSnapshot, ChangedFile]],
    list[tuple[SymbolSnapshot, ChangedFile]],
    list[str],
]:
    """Correlate conservative symbol moves between distinct changed files."""
    pairs: list[tuple[SymbolSnapshot, ChangedFile, SymbolSnapshot, ChangedFile]] = []
    warnings: list[str] = []
    matched_old: set[int] = set()
    matched_new: set[int] = set()
    by_name: dict[tuple[str, str], list[tuple[SymbolSnapshot, ChangedFile]]] = defaultdict(list)
    by_fingerprint: dict[tuple[str, str, str], list[tuple[SymbolSnapshot, ChangedFile]]] = (
        defaultdict(list)
    )
    by_shape: dict[tuple[str, str], list[tuple[SymbolSnapshot, ChangedFile]]] = defaultdict(list)
    for new, new_file in added:
        by_name[(new.kind, new.qualified_name)].append((new, new_file))
        by_fingerprint[(new.kind, new.signature_shape, new.fingerprint)].append((new, new_file))
        by_shape[(new.kind, new.signature_shape)].append((new, new_file))

    def available(
        items: list[tuple[SymbolSnapshot, ChangedFile]],
    ) -> list[tuple[SymbolSnapshot, ChangedFile]]:
        result: list[tuple[SymbolSnapshot, ChangedFile]] = []
        for item in items:
            if id(item[0]) not in matched_new:
                result.append(item)
            if len(result) > limits.MAX_SIMILARITY_CANDIDATES:
                break
        return result

    # Preserve exact qualified names across files first, but never force duplicate near-ties.
    for old, old_file in removed:
        if old.kind == "module":
            continue
        exact = available(by_name[(old.kind, old.qualified_name)])
        if len(exact) == 1:
            new, new_file = exact[0]
            pairs.append((old, old_file, new, new_file))
            matched_old.add(id(old))
            matched_new.add(id(new))
        elif len(exact) > 1:
            mark_ambiguous(
                old,
                [new for new, _ in exact],
                warnings,
                f"Ambiguous cross-file move for {old.qualified_name!r}; treated conservatively.",
            )

    # An exact signature and behavior fingerprint is strong move evidence even after a rename.
    for old, old_file in removed:
        if id(old) in matched_old or old.kind == "module":
            continue
        exact = available(by_fingerprint[(old.kind, old.signature_shape, old.fingerprint)])
        if len(exact) == 1:
            new, new_file = exact[0]
            pairs.append((old, old_file, new, new_file))
            matched_old.add(id(old))
            matched_new.add(id(new))
        elif len(exact) > 1:
            mark_ambiguous(
                old,
                [new for new, _ in exact],
                warnings,
                f"Ambiguous cross-file fingerprint match for {old.qualified_name!r}; "
                "treated conservatively.",
            )

    for old, old_file in removed:
        if id(old) in matched_old or old.kind == "module":
            continue
        candidates = available(by_shape[(old.kind, old.signature_shape)])
        if len(candidates) > limits.MAX_SIMILARITY_CANDIDATES:
            mark_ambiguous(
                old,
                [new for new, _ in candidates[: limits.MAX_SIMILARITY_CANDIDATES]],
                warnings,
                f"Cross-file similarity candidates for {old.qualified_name!r} exceeded the "
                "safety cap; treated conservatively.",
            )
            continue
        scored = sorted(
            (
                (symbol_similarity(old, new), (new, new_file))
                for new, new_file in candidates
                if new.kind != "module"
            ),
            key=lambda item: (
                -item[0],
                item[1][1].path,
                item[1][0].qualified_name,
                item[1][0].start,
            ),
        )
        winner, ambiguous = pick_unique_match(scored)
        if ambiguous:
            plausible_peers = [
                new for score, (new, _) in scored if score >= limits.SIMILARITY_ACCEPT_THRESHOLD
            ]
            mark_ambiguous(
                old,
                plausible_peers,
                warnings,
                f"Ambiguous cross-file similarity match for {old.qualified_name!r}; "
                "treated conservatively.",
            )
            continue
        if winner is None:
            continue
        new, new_file = winner
        pairs.append((old, old_file, new, new_file))
        matched_old.add(id(old))
        matched_new.add(id(new))

    unmatched_removed = [item for item in removed if id(item[0]) not in matched_old]
    unmatched_added = [item for item in added if id(item[0]) not in matched_new]
    return pairs, unmatched_removed, unmatched_added, warnings


# Backward-compatible private aliases.
_match_symbols = match_symbols
_match_cross_file_symbols = match_cross_file_symbols
_symbol_similarity = symbol_similarity
