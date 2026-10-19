from __future__ import annotations

import semantic_diff_weaver.ast_diff.limits as ast_limits
from semantic_diff_weaver.ast_diff import analyze_ast
from semantic_diff_weaver.source import SourceHunk, SourceRevisionPair


def changed(old: str, new: str) -> SourceRevisionPair:
    return SourceRevisionPair(
        path="src/sample.py",
        old_path="src/sample.py",
        new_path="src/sample.py",
        hunks=(SourceHunk(id="hunk-001", old_start=1, old_count=100, new_start=1, new_count=100),),
        old_text=old,
        new_text=new,
    )


def kinds(old: str, new: str) -> set[str]:
    return {item.kind for item in analyze_ast([changed(old, new)]).deltas}


def test_ast_symbol_budget_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(ast_limits, "MAX_SYMBOLS_PER_FILE", 3)
    source = "\n".join(f"def function_{index}():\n    return {index}\n" for index in range(4))
    result = analyze_ast([changed(source, source.replace("return", "return +"))])
    assert result.resource_limited_files == 1
    assert result.failed_files == 1
    assert result.deltas[0].kind == "parse_incomplete"
    assert result.deltas[0].metadata["resource_limited"] is True


def test_extracts_required_structural_delta_classes() -> None:
    assert "comparison_change" in kinds(
        "def allowed(x):\n    return x < 5\n", "def allowed(x):\n    return x <= 5\n"
    )
    assert "signature_change" in kinds(
        "def run(limit=2):\n    return limit\n", "def run(limit=3):\n    return limit\n"
    )
    assert "signature_change" in kinds(
        "def run(value: int) -> int:\n    return value\n",
        "def run(value: int) -> str:\n    return value\n",
    )
    assert "raise_change" in kinds(
        "def run():\n    raise ValueError()\n", "def run():\n    raise TypeError()\n"
    )
    assert "return_change" in kinds(
        "def run():\n    return {'old': 1}\n", "def run():\n    return {'new': 1}\n"
    )
    assert "assignment_change" in kinds(
        "def run(x):\n    state = x\n    return state\n",
        "def run(x):\n    state = x + 1\n    return state\n",
    )
    assert "loop_change" in kinds(
        "def run():\n    for x in range(2):\n        pass\n",
        "def run():\n    for x in range(3):\n        pass\n",
    )


def test_unchanged_symbol_outside_hunk_is_ignored() -> None:
    file = SourceRevisionPair(
        path="src/sample.py",
        old_path="src/sample.py",
        new_path="src/sample.py",
        hunks=(SourceHunk(id="hunk-001", old_start=5, old_count=1, new_start=5, new_count=1),),
        old_text="def unchanged(x):\n    return x < 5\n\nvalue = 1\n",
        new_text="def unchanged(x):\n    return x < 5\n\nvalue = 2\n",
    )
    analysis = analyze_ast([file])
    assert all(item.symbol != "unchanged" for item in analysis.deltas)


def test_partial_parse_failure_preserves_other_file() -> None:
    good = changed("def f(x):\n    return x < 2\n", "def f(x):\n    return x <= 2\n")
    bad = SourceRevisionPair(
        path="src/bad.py",
        old_path="src/bad.py",
        new_path="src/bad.py",
        hunks=(SourceHunk(id="hunk-001", old_start=1, old_count=1, new_start=1, new_count=1),),
        old_text="def broken(:\n",
        new_text="def still_broken(:\n",
    )
    analysis = analyze_ast([good, bad])
    assert analysis.parsed_files == 1
    assert analysis.failed_files == 1
    assert analysis.deltas
    assert analysis.warnings


def test_copy_is_reported_as_added_surface_not_an_unchanged_rename() -> None:
    copied = SourceRevisionPair(
        path="src/copied.py",
        has_old_side=False,
        old_path="src/original.py",
        new_path="src/copied.py",
        hunks=(SourceHunk(id="hunk-001", old_start=0, old_count=0, new_start=1, new_count=2),),
        old_text="def copied():\n    return 1\n",
        new_text="def copied():\n    return 1\n",
    )
    deltas = analyze_ast([copied]).deltas
    assert any(item.kind == "symbol_added" and item.symbol == "copied" for item in deltas)


def test_an_added_or_removed_symbol_shows_what_it_was_rather_than_its_signature() -> None:
    added = SourceRevisionPair(
        path="pkg/__main__.py",
        old_path=None,
        new_path="pkg/__main__.py",
        hunks=(SourceHunk(id="hunk-001", old_start=0, old_count=0, new_start=1, new_count=2),),
        old_text=None,
        new_text="from pkg import cli\nraise SystemExit(cli.main())\n",
    )
    removed = SourceRevisionPair(
        path="pkg/args.py",
        old_path="pkg/args.py",
        new_path=None,
        hunks=(SourceHunk(id="hunk-001", old_start=1, old_count=2, new_start=0, new_count=0),),
        old_text="class Args:\n    pr: int\n",
        new_text=None,
    )
    deltas = {(item.symbol, item.kind): item for item in analyze_ast([added, removed]).deltas}
    assert deltas[("<module>", "symbol_added")].new == (
        "from pkg import cli\nraise SystemExit(cli.main())"
    )
    assert deltas[("Args", "symbol_removed")].old == "class Args: pr: int"


def _added(path: str, text: str) -> SourceRevisionPair:
    lines = len(text.splitlines())
    return SourceRevisionPair(
        path=path,
        old_path=None,
        new_path=path,
        hunks=(SourceHunk(id="hunk-001", old_start=0, old_count=0, new_start=1, new_count=lines),),
        old_text=None,
        new_text=text,
    )


def test_an_added_module_of_only_a_docstring_is_no_change() -> None:
    """It runs nothing when imported, like an empty `__init__.py`, whose diff has no hunk.

    A module that runs anything is still added, and a removed one still breaks what imported it.
    """
    docstring = '"""What the package is for."""\n'
    result = analyze_ast(
        [
            _added("pkg/__init__.py", docstring),
            _added("pkg/notes.py", "# Nothing runs here.\n"),
            _added("pkg/exports.py", docstring + "from pkg.core import run\n"),
        ]
    )
    assert [(item.path, item.symbol, item.kind) for item in result.deltas] == [
        ("pkg/exports.py", "<module>", "symbol_added")
    ]
    assert result.changed_symbols == 1
    removed = SourceRevisionPair(
        path="pkg/__init__.py",
        old_path="pkg/__init__.py",
        new_path=None,
        hunks=(SourceHunk(id="hunk-001", old_start=1, old_count=1, new_start=0, new_count=0),),
        old_text=docstring,
        new_text=None,
    )
    assert [(item.symbol, item.kind) for item in analyze_ast([removed]).deltas] == [
        ("<module>", "symbol_removed")
    ]
