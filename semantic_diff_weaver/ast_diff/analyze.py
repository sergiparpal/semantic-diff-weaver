"""Orchestrate bounded AST extraction, matching, and structural delta emission."""

from __future__ import annotations

import time

from ..models import LineRange
from ..source import SourceHunk, SourceRevisionPair
from .compare import compare_symbol
from .extract import AstResourceLimit, extract_symbols
from .limits import AstBudget
from .match import match_cross_file_symbols, match_symbols
from .types import AstAnalysis, StructuralDelta, SymbolSnapshot


def _hunk_line_range(hunk: SourceHunk | None, *, side: str) -> LineRange | None:
    if hunk is None:
        return None
    if side == "old":
        start = max(1, hunk.old_start)
        count = max(1, hunk.old_count)
    else:
        start = max(1, hunk.new_start)
        count = max(1, hunk.new_count)
    return LineRange(start=start, end=max(1, start + count - 1))


def _parse_incomplete_delta(
    path: str, changed: SourceRevisionPair, *, resource_limited: bool
) -> StructuralDelta:
    hunk = changed.hunks[0] if changed.hunks else None
    message = (
        "Committed Python structure exceeded a safety budget"
        if resource_limited
        else "Committed Python syntax could not be parsed"
    )
    return StructuralDelta(
        path=path,
        symbol="<unparsed>",
        kind="parse_incomplete",
        old=message,
        new=message,
        old_lines=_hunk_line_range(hunk, side="old"),
        new_lines=_hunk_line_range(hunk, side="new"),
        hunk_id=f"{path}#{hunk.id}" if hunk else None,
        parser_complete=False,
        metadata={
            "parse_failure": not resource_limited,
            "resource_limited": resource_limited,
        },
    )


def _parse_changed_file(
    changed: SourceRevisionPair,
    *,
    retained_source_bytes: int,
    extracted_symbols: int,
    deadline: float,
    budget: AstBudget,
) -> tuple[list[SymbolSnapshot], list[SymbolSnapshot], int, int]:
    sources = [source for source in (changed.old_text, changed.new_text) if source is not None]
    source_sizes = [len(source.encode("utf-8")) for source in sources]
    source_bytes = sum(source_sizes)
    if time.monotonic() > deadline:
        raise AstResourceLimit("AST analysis deadline exceeded")
    if any(size > budget.max_source_bytes_per_version for size in source_sizes):
        raise AstResourceLimit("AST source byte budget exceeded")
    if retained_source_bytes + source_bytes > budget.max_source_bytes_total:
        raise AstResourceLimit("aggregate AST source byte budget exceeded")
    old_symbols = (
        extract_symbols(changed.old_text, budget)
        if changed.old_text is not None and changed.has_old_side
        else []
    )
    new_symbols = extract_symbols(changed.new_text, budget) if changed.new_text is not None else []
    if extracted_symbols + len(old_symbols) + len(new_symbols) > budget.max_extracted_symbols_total:
        raise AstResourceLimit("aggregate symbol budget exceeded")
    return old_symbols, new_symbols, source_bytes, len(old_symbols) + len(new_symbols)


def _drop_redundant_module_deltas(
    deltas: list[StructuralDelta], changed_symbol_keys: set[tuple[str, str]]
) -> tuple[list[StructuralDelta], set[tuple[str, str]]]:
    # A module snapshot is always recorded, but its generic add/remove/refactor evidence is
    # redundant when a more precise symbol in the same file already explains the hunk.
    detailed_paths: set[str] = set()
    for item in deltas:
        if item.symbol == "<module>":
            continue
        detailed_paths.add(item.path)
        for key in ("old_path", "new_path"):
            metadata_path = item.metadata.get(key)
            if isinstance(metadata_path, str):
                detailed_paths.add(metadata_path)
    filtered = [
        item
        for item in deltas
        if not (
            item.symbol == "<module>"
            and item.path in detailed_paths
            and item.kind in {"symbol_added", "symbol_removed", "structural_refactor"}
        )
    ]
    filtered_keys = {
        key
        for key in changed_symbol_keys
        if not (key[1] == "<module>" and key[0] in detailed_paths)
    }
    return filtered, filtered_keys


def analyze_ast(files: list[SourceRevisionPair], budget: AstBudget | None = None) -> AstAnalysis:
    effective_budget = budget or AstBudget.default()
    deltas: list[StructuralDelta] = []
    warnings: list[str] = []
    parsed_files = 0
    failed_files = 0
    resource_limited_files = 0
    retained_source_bytes = 0
    extracted_symbols = 0
    deadline = time.monotonic() + effective_budget.analysis_timeout_seconds
    changed_symbol_keys: set[tuple[str, str]] = set()
    parsed: list[tuple[SourceRevisionPair, list[SymbolSnapshot], list[SymbolSnapshot]]] = []
    for changed in files:
        path = changed.path
        try:
            old_symbols, new_symbols, source_bytes, symbol_count = _parse_changed_file(
                changed,
                retained_source_bytes=retained_source_bytes,
                extracted_symbols=extracted_symbols,
                deadline=deadline,
                budget=effective_budget,
            )
            retained_source_bytes += source_bytes
            extracted_symbols += symbol_count
        except (
            AstResourceLimit,
            MemoryError,
            RecursionError,
            SyntaxError,
            TypeError,
            ValueError,
        ) as exc:
            failed_files += 1
            resource_limited = isinstance(exc, (AstResourceLimit, MemoryError, RecursionError))
            if resource_limited:
                resource_limited_files += 1
                warnings.append(
                    f"Changed Python source {path!r} exceeded an immutable AST safety budget; "
                    "analysis is incomplete."
                )
            else:
                warnings.append(
                    f"Could not parse changed Python source {path!r}; analysis is incomplete."
                )
            deltas.append(_parse_incomplete_delta(path, changed, resource_limited=resource_limited))
            changed_symbol_keys.add((path, "<unparsed>"))
            continue
        parsed_files += 1
        parsed.append((changed, old_symbols, new_symbols))

    removed: list[tuple[SymbolSnapshot, SourceRevisionPair]] = []
    added: list[tuple[SymbolSnapshot, SourceRevisionPair]] = []
    for changed, old_symbols, new_symbols in parsed:
        path = changed.path
        pairs, match_warnings = match_symbols(old_symbols, new_symbols)
        warnings.extend(match_warnings)
        for pair in pairs:
            if pair.old is None:
                if pair.new is not None:
                    added.append((pair.new, changed))
                continue
            if pair.new is None:
                removed.append((pair.old, changed))
                continue
            symbol_deltas = compare_symbol(
                path,
                pair.old,
                pair.new,
                changed.hunks,
                old_path=changed.old_path or path,
                new_path=changed.new_path or path,
            )
            if symbol_deltas:
                changed_symbol_keys.add((path, pair.new.qualified_name))
                deltas.extend(symbol_deltas)

    cross_pairs, removed, added, cross_warnings = match_cross_file_symbols(removed, added)
    warnings.extend(cross_warnings)
    for old, old_file, new, new_file in cross_pairs:
        path = new_file.path
        symbol_deltas = compare_symbol(
            path,
            old,
            new,
            new_file.hunks,
            old_path=old_file.old_path or old_file.path,
            new_path=new_file.new_path or new_file.path,
            old_hunks=old_file.hunks,
            new_hunks=new_file.hunks,
        )
        if symbol_deltas:
            changed_symbol_keys.add((path, new.qualified_name))
            deltas.extend(symbol_deltas)
    for old, changed in removed:
        path = changed.old_path or changed.path
        symbol_deltas = compare_symbol(
            path,
            old,
            None,
            changed.hunks,
            old_path=path,
            new_path=changed.new_path or path,
        )
        if symbol_deltas:
            changed_symbol_keys.add((path, old.qualified_name))
            deltas.extend(symbol_deltas)
    for new, changed in added:
        path = changed.new_path or changed.path
        symbol_deltas = compare_symbol(
            path,
            None,
            new,
            changed.hunks,
            old_path=changed.old_path or path,
            new_path=path,
        )
        if symbol_deltas:
            changed_symbol_keys.add((path, new.qualified_name))
            deltas.extend(symbol_deltas)

    deltas, changed_symbol_keys = _drop_redundant_module_deltas(deltas, changed_symbol_keys)
    return AstAnalysis(
        deltas=deltas,
        warnings=warnings,
        parsed_files=parsed_files,
        failed_files=failed_files,
        changed_symbols=len(changed_symbol_keys),
        resource_limited_files=resource_limited_files,
    )
