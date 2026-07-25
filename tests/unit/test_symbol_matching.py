from __future__ import annotations

from semantic_diff_weaver.ast_diff import analyze_ast
from semantic_diff_weaver.semantic_candidates import build_candidates
from semantic_diff_weaver.source import SourceHunk, SourceRevisionPair


def file(old: str, new: str) -> SourceRevisionPair:
    return SourceRevisionPair(
        path="src/a.py",
        old_path="src/a.py",
        new_path="src/a.py",
        old_text=old,
        new_text=new,
        hunks=(SourceHunk(id="hunk-001", old_start=1, old_count=20, new_start=1, new_count=20),),
    )


def test_conservative_rename_uses_fingerprint_and_signature() -> None:
    result = analyze_ast(
        [file("def old_name(x):\n    return x + 1\n", "def new_name(x):\n    return x + 1\n")]
    )
    assert any(item.kind == "structural_refactor" for item in result.deltas)
    assert not any(item.kind in {"symbol_added", "symbol_removed"} for item in result.deltas)


def test_ambiguous_rename_is_not_forced() -> None:
    result = analyze_ast(
        [
            file(
                "def old(x):\n    return x + 1\n",
                "def first(x):\n    return x + 1\n\ndef second(x):\n    return x + 1\n",
            )
        ]
    )
    assert result.warnings
    assert any(item.kind == "symbol_removed" for item in result.deltas)
    assert sum(item.kind == "symbol_added" for item in result.deltas) == 2
    assert all(item.metadata["ambiguous_match"] for item in result.deltas)
    assert all(
        candidate.confidence_baseline == 0.42 for candidate in build_candidates(result.deltas)
    )


def test_conservative_similarity_matches_a_rename_with_a_small_body_change() -> None:
    result = analyze_ast(
        [file("def old_name(x):\n    return x + 1\n", "def new_name(x):\n    return x + 2\n")]
    )
    assert any(item.kind == "return_change" for item in result.deltas)
    assert not any(item.kind in {"symbol_added", "symbol_removed"} for item in result.deltas)


def test_cross_file_function_move_is_correlated_without_add_remove_claims() -> None:
    removed = SourceRevisionPair(
        path="src/old.py",
        old_path="src/old.py",
        new_path=None,
        old_text="def moved(x):\n    return x + 1\n",
        new_text=None,
        hunks=(SourceHunk(id="hunk-001", old_start=1, old_count=2, new_start=0, new_count=0),),
    )
    added = SourceRevisionPair(
        path="src/new.py",
        old_path=None,
        new_path="src/new.py",
        old_text=None,
        new_text="def moved(x):\n    return x + 1\n",
        hunks=(SourceHunk(id="hunk-001", old_start=0, old_count=0, new_start=1, new_count=2),),
    )
    result = analyze_ast([removed, added])
    assert [(item.path, item.symbol, item.kind) for item in result.deltas] == [
        ("src/new.py", "moved", "structural_refactor")
    ]
    assert result.deltas[0].metadata["old_path"] == "src/old.py"


def test_overload_style_duplicate_names_are_all_preserved() -> None:
    old = """\
@overload
def parse(value: int) -> int: ...
@overload
def parse(value: str) -> str: ...
def parse(value):
    return value
"""
    new = old.replace("value: str) -> str", "value: bytes) -> bytes")
    result = analyze_ast([file(old, new)])
    signature_deltas = [item for item in result.deltas if item.kind == "signature_change"]
    assert len(signature_deltas) == 1
    assert "str" in (signature_deltas[0].old or "")
    assert "bytes" in (signature_deltas[0].new or "")
