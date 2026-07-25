"""Branch-level coverage for the AST extraction, matching, and comparison algorithms.

These paths are only reachable through unusual-but-real Python: async constructs, overload-like
duplicate definitions, cross-file moves, ambiguous near-ties, and the immutable safety budgets.
"""

from __future__ import annotations

import ast

import pytest

from semantic_diff_weaver.ast_diff import limits
from semantic_diff_weaver.ast_diff.analyze import analyze_ast
from semantic_diff_weaver.ast_diff.compare import compare_symbol, make_structural_delta
from semantic_diff_weaver.ast_diff.extract import (
    AstResourceLimit,
    body_without_docstring,
    default_map,
    extract_symbols,
    unparse_redacted,
)
from semantic_diff_weaver.ast_diff.limits import AstBudget
from semantic_diff_weaver.ast_diff.match import (
    match_cross_file_symbols,
    match_symbols,
    symbol_similarity,
)
from semantic_diff_weaver.source import SourceHunk, SourceRevisionPair

WIDE_HUNK = SourceHunk(id="hunk-001", old_start=1, old_count=400, new_start=1, new_count=400)


def _budget(**overrides: object) -> AstBudget:
    base = AstBudget.default()
    fields = {
        "max_nodes_per_file": base.max_nodes_per_file,
        "max_depth": base.max_depth,
        "max_symbols_per_file": base.max_symbols_per_file,
        "max_source_bytes_per_version": base.max_source_bytes_per_version,
        "max_source_bytes_total": base.max_source_bytes_total,
        "max_extracted_symbols_total": base.max_extracted_symbols_total,
        "analysis_timeout_seconds": base.analysis_timeout_seconds,
    }
    fields.update(overrides)
    return AstBudget(**fields)  # type: ignore[arg-type]


def _pair(old: str | None, new: str | None, path: str = "m.py") -> SourceRevisionPair:
    return SourceRevisionPair(
        path=path,
        old_path=path if old is not None else None,
        new_path=path if new is not None else None,
        old_text=old,
        new_text=new,
        hunks=(WIDE_HUNK,),
    )


def _symbols(source: str) -> dict[str, object]:
    return {item.qualified_name: item for item in extract_symbols(source)}


def test_unparse_redacted_renders_a_missing_node_as_none() -> None:
    assert unparse_redacted(None) == "None"


def test_unparse_redacted_falls_back_to_the_node_type_when_unparsing_fails() -> None:
    # A Call with no func cannot be unparsed; the name must still be safe to report.
    assert unparse_redacted(ast.Call()) == "Call"


def test_body_without_docstring_drops_only_a_leading_string_expression() -> None:
    with_doc = ast.parse('"""doc."""\nx = 1\n').body
    without_doc = ast.parse("x = 1\n").body
    assert len(body_without_docstring(with_doc)) == 1
    assert len(body_without_docstring(without_doc)) == 1


def test_node_budget_rejects_an_oversized_tree() -> None:
    with pytest.raises(AstResourceLimit):
        extract_symbols("x = 1\n" * 50, _budget(max_nodes_per_file=10))


def test_depth_budget_rejects_a_deeply_nested_expression() -> None:
    with pytest.raises(AstResourceLimit):
        extract_symbols("x = " + "[" * 30 + "]" * 30 + "\n", _budget(max_depth=5))


def test_symbol_budget_rejects_too_many_definitions() -> None:
    source = "".join(f"def f{index}():\n    pass\n" for index in range(10))
    with pytest.raises(AstResourceLimit):
        extract_symbols(source, _budget(max_symbols_per_file=3))


def test_aggregate_source_byte_budget_marks_the_file_incomplete() -> None:
    result = analyze_ast([_pair("x = 1\n", "x = 2\n")], _budget(max_source_bytes_total=1))
    assert result.resource_limited_files == 1


def test_aggregate_symbol_budget_marks_the_file_incomplete() -> None:
    source = "".join(f"def f{index}():\n    pass\n" for index in range(5))
    result = analyze_ast([_pair(source, source + "\n")], _budget(max_extracted_symbols_total=1))
    assert result.resource_limited_files == 1


def test_keyword_only_defaults_are_captured_and_bare_keyword_only_args_are_not() -> None:
    node = ast.parse("def f(*, a=1, b):\n    pass\n").body[0]
    assert isinstance(node, ast.FunctionDef)
    assert default_map(node) == {"a": "1"}


def test_async_and_annotated_constructs_are_extracted_as_features() -> None:
    source = (
        "async def run(flag: bool):\n"
        "    total: int = 0\n"
        "    total += 1\n"
        "    async with open('f') as handle:\n"
        "        async for item in handle:\n"
        "            assert item\n"
        "    value = 1 if flag else 2\n"
        "    return value\n"
    )
    symbol = _symbols(source)["run"]
    features = symbol.features  # type: ignore[attr-defined]
    assert symbol.kind == "async_function"  # type: ignore[attr-defined]
    assert features["contexts"] and features["loops"]
    assert len(features["conditions"]) >= 2
    assert features["assignments"]


def test_nested_definitions_do_not_leak_into_the_enclosing_symbol_body() -> None:
    source = "def outer():\n    def inner():\n        raise ValueError('inner')\n    return inner\n"
    symbols = _symbols(source)
    assert symbols["outer"].features["raises"] == ()  # type: ignore[attr-defined]
    assert symbols["outer.inner"].features["raises"]  # type: ignore[attr-defined]


def test_decorator_change_is_reported_independently_of_the_body() -> None:
    result = analyze_ast(
        [_pair("@cache\ndef f():\n    return 1\n", "@retry\ndef f():\n    return 1\n")]
    )
    assert "decorator_change" in {item.kind for item in result.deltas}


def test_comparing_two_absent_symbols_yields_no_delta() -> None:
    assert compare_symbol("m.py", None, None, (WIDE_HUNK,)) == []


def test_a_delta_without_either_side_is_labelled_unknown() -> None:
    delta = make_structural_delta("m.py", None, None, "m.py#hunk-001", "kind", None, None)
    assert delta.symbol == "<unknown>"


def test_symbols_of_different_kinds_never_correlate() -> None:
    function = _symbols("def shared():\n    return 1\n")["shared"]
    klass = _symbols("class shared:\n    value = 1\n")["shared"]
    assert symbol_similarity(function, klass) == 0.0  # type: ignore[arg-type]


def test_overload_like_duplicates_are_matched_by_source_order() -> None:
    old = extract_symbols("def f(a):\n    return 1\n\ndef f(a, b):\n    return 2\n")
    new = extract_symbols("def f(a):\n    return 1\n\ndef f(a, b):\n    return 3\n")
    pairs, _ = match_symbols(old, new)
    matched = [item for item in pairs if item.old and item.new and item.old.kind == "function"]
    assert len(matched) == 2


def test_a_changed_overload_count_is_warned_about_and_never_silently_dropped() -> None:
    old = extract_symbols("def f(a):\n    return 1\n\ndef f(a, b):\n    return 2\n")
    new = extract_symbols("def f(a):\n    return 1\n")
    pairs, warnings = match_symbols(old, new)
    assert any("Overload-like definition count changed" in item for item in warnings)
    assert any(item.old is not None and item.new is None for item in pairs)


def test_a_large_overload_group_degrades_to_conservative_source_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Past the exact-comparison cap the matcher must warn and fall back, never scan quadratically."""
    monkeypatch.setattr(limits, "MAX_EXACT_GROUP_COMPARISONS", 1)
    body = "".join("def f(a):\n    return 1\n" for _ in range(3))
    pairs, warnings = match_symbols(extract_symbols(body), extract_symbols(body))
    assert any("matched conservatively by source order" in item for item in warnings)
    assert sum(1 for item in pairs if item.old and item.new) >= 3


def test_cross_file_move_of_an_identical_symbol_is_correlated() -> None:
    source = "def moved(value):\n    if value > 3:\n        return value\n    return 0\n"
    result = analyze_ast([_pair(source, None, "a.py"), _pair(None, source, "b.py")])
    assert not any(
        item.kind == "symbol_removed" and item.symbol == "moved" for item in result.deltas
    )


def test_cross_file_matching_leaves_unrelated_symbols_unmatched() -> None:
    removed = [(item, _pair("x = 1\n", None, "a.py")) for item in extract_symbols("x = 1\n")]
    added = [(item, _pair(None, "y = 2\n", "b.py")) for item in extract_symbols("y = 2\n")]
    pairs, still_removed, still_added, _ = match_cross_file_symbols(removed, added)
    assert pairs == []
    assert still_removed and still_added


def test_ambiguous_cross_file_move_is_flagged_rather_than_guessed() -> None:
    source = "def moved(value):\n    return value + 1\n"
    result = analyze_ast(
        [
            _pair(source, None, "a.py"),
            _pair(None, source, "b.py"),
            _pair(None, source, "c.py"),
        ]
    )
    assert any("Ambiguous cross-file" in item for item in result.warnings)
