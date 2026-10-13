from __future__ import annotations

import time

import pytest

from semantic_diff_weaver.ast_diff import StructuralDelta, analyze_ast, module_references
from semantic_diff_weaver.call_graph import MAX_CALL_GRAPH_FILES, CallGraph, package_of
from semantic_diff_weaver.models import LineRange, WeaverConfig
from semantic_diff_weaver.semantic_candidates import build_candidates
from semantic_diff_weaver.service import analyze
from semantic_diff_weaver.source import SourceHunk, SourceRevisionPair
from semantic_diff_weaver.test_mapper import TestIndex, _index_source, map_candidate_tests


@pytest.mark.performance
@pytest.mark.no_cover
def test_near_symbol_limit_preprocessing_is_under_reference_target() -> None:
    old = "\n\n".join(f"def function_{index}(x):\n    return x < {index}" for index in range(100))
    new = "\n\n".join(f"def function_{index}(x):\n    return x <= {index}" for index in range(100))
    changed = SourceRevisionPair(
        path="src/generated_fixture.py",
        old_path="src/generated_fixture.py",
        new_path="src/generated_fixture.py",
        old_text=old,
        new_text=new,
        hunks=(
            SourceHunk(id="hunk-001", old_start=1, old_count=1000, new_start=1, new_count=1000),
        ),
    )
    started = time.perf_counter()
    result = analyze_ast([changed])
    elapsed = time.perf_counter() - started
    assert result.changed_symbols == 100
    assert elapsed < 5.0


@pytest.mark.performance
@pytest.mark.no_cover
def test_mass_rename_matching_is_bounded() -> None:
    count = 500
    old = "".join(f"def old_{index}(x):\n    return x + {index}\n" for index in range(count))
    new = "".join(f"def new_{index}(x):\n    return x - {index}\n" for index in range(count))
    changed = SourceRevisionPair(
        path="src/many.py",
        old_path="src/many.py",
        new_path="src/many.py",
        old_text=old,
        new_text=new,
        hunks=(
            SourceHunk(
                id="hunk-001",
                old_start=1,
                old_count=count * 2,
                new_start=1,
                new_count=count * 2,
            ),
        ),
    )
    started = time.perf_counter()
    result = analyze_ast([changed])
    elapsed = time.perf_counter() - started
    assert result.changed_symbols == count * 2
    assert elapsed < 2.0


@pytest.mark.performance
@pytest.mark.no_cover
def test_full_deterministic_pipeline_near_default_limits(repo_factory) -> None:
    old_files: dict[str, str] = {}
    new_files: dict[str, str] = {}
    symbol_index = 0
    for file_index in range(40):
        functions = 3 if file_index < 20 else 2
        old_symbols: list[str] = []
        new_symbols: list[str] = []
        for _ in range(functions):
            assignments_old = "\n".join(f"    item_{line} = value + {line}" for line in range(15))
            assignments_new = "\n".join(f"    item_{line} = value - {line}" for line in range(15))
            old_symbols.append(
                f"def function_{symbol_index}(value):\n{assignments_old}\n    return item_14\n"
            )
            new_symbols.append(
                f"def function_{symbol_index}(value):\n{assignments_new}\n    return item_14\n"
            )
            symbol_index += 1
        path = f"src/module_{file_index:02d}.py"
        old_files[path] = "\n".join(old_symbols)
        new_files[path] = "\n".join(new_symbols)
    repo, base, head = repo_factory(old_files, new_files)
    arguments = {
        "repo_path": str(repo),
        "base_ref": base,
        "head_ref": head,
        "output_format": "json",
    }
    analyze(arguments)
    started = time.perf_counter()
    result = analyze(arguments)
    elapsed = time.perf_counter() - started
    assert result["scope"]["changed_files_total"] == 40
    assert result["scope"]["changed_lines"] == 3000
    assert result["scope"]["changed_symbols"] == 100
    assert elapsed < 5.0


@pytest.mark.performance
@pytest.mark.no_cover
def test_calls_on_instances_are_followed_at_the_graph_module_limit() -> None:
    """Each class's `step` returns the next class, so `use` in any module knows all of them.

    Recomputing every symbol once per link of such a chain took 57 seconds at the module limit.
    """
    count = MAX_CALL_GRAPH_FILES
    sources = {}
    for index in range(count):
        following = (index + 1) % count
        sources[f"src/pkg/m{index}.py"] = (
            f"from pkg.m{following} import Thing{following}\n\n\n"
            f"class Thing{index}:\n"
            f"    def step(self) -> Thing{following}:\n        return Thing{following}()\n\n"
            f"    def total(self):\n        return 0\n\n\n"
            f"def make() -> Thing{index}:\n    return Thing{index}()\n\n\n"
            f"def use():\n    return make().step().step().total()\n"
        )
    modules = {path: module_references(text, package_of(path)) for path, text in sources.items()}
    tests = [
        test
        for index in range(0, count, 5)
        for test in _index_source(
            f"tests/test_use_{index}.py",
            f"from pkg import m{index}\n\ndef test_a_made_thing_totals_nothing():\n"
            f"    assert m{index}.make().step().total() == 0\n",
        )
    ]
    candidates = build_candidates(
        [
            StructuralDelta(
                path=f"src/pkg/m{index}.py",
                symbol=f"Thing{index}.total",
                kind="symbol_added",
                old=None,
                new="(self)",
                old_lines=None,
                new_lines=LineRange(start=8, end=9),
                hunk_id=f"src/pkg/m{index}.py#hunk-001",
            )
            for index in range(0, count, 10)
        ]
    )
    started = time.perf_counter()
    graph = CallGraph(modules)
    mapped = map_candidate_tests(
        candidates, TestIndex(tests=tests, incomplete=False, warnings=[]), WeaverConfig(), graph
    )
    elapsed = time.perf_counter() - started
    assert ("src/pkg/m250.py", "use") in graph.reaching(("src/pkg/m0.py", "Thing0.total"))
    # Each test holds what `make()` returns and calls `total` on it.
    assert all(
        "changed symbol reached through calls" in items[0].match_reasons
        for items in mapped.values()
    )
    assert len(mapped) == len(candidates)
    assert elapsed < 5.0
