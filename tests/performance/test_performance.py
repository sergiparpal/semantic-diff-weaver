from __future__ import annotations

import time

import pytest

from semantic_diff_weaver.ast_diff import analyze_ast
from semantic_diff_weaver.service import analyze
from semantic_diff_weaver.source import SourceHunk, SourceRevisionPair


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
