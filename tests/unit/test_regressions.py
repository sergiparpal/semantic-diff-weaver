"""Regressions for defects found by review, each pinned to the behavior that was wrong."""

from __future__ import annotations

import ast
import os
import shutil
import sys
import warnings
from pathlib import Path

import pytest

from semantic_diff_weaver.ast_diff import analyze as analyze_module
from semantic_diff_weaver.ast_diff import analyze_ast
from semantic_diff_weaver.ast_diff.extract import extract_symbols, type_parameters
from semantic_diff_weaver.ast_diff.limits import AstBudget
from semantic_diff_weaver.ast_diff.types import StructuralDelta
from semantic_diff_weaver.config import load_config
from semantic_diff_weaver.errors import ErrorCode, WeaverError
from semantic_diff_weaver.git_diff.parse import parse_name_status, parse_numstat
from semantic_diff_weaver.git_diff.process import run_bounded_process
from semantic_diff_weaver.models import AnalyzeRequest, BehaviorCategory, LineRange, RulesConfig
from semantic_diff_weaver.path_policy import exclusion_reason
from semantic_diff_weaver.semantic_candidates import classify_delta
from semantic_diff_weaver.source import SourceHunk, SourceRevisionPair
from semantic_diff_weaver.taxonomy import CATEGORY_PROFILES

REQUIRES_PEP695 = pytest.mark.skipif(
    sys.version_info < (3, 12), reason="PEP 695 type parameter syntax requires Python 3.12"
)


def _condition_delta(symbol: str, old: str, new: str) -> StructuralDelta:
    return StructuralDelta(
        path="src/change.py",
        symbol=symbol,
        kind="condition_change",
        old=old,
        new=new,
        old_lines=LineRange(start=1, end=1),
        new_lines=LineRange(start=1, end=1),
        hunk_id="src/change.py#hunk-001",
    )


@pytest.mark.parametrize("symbol", ["<module>", "<unparsed>", "check_access"])
def test_synthetic_symbol_names_do_not_force_a_comparison_classification(symbol: str) -> None:
    """Angle brackets in ``<module>``/``<unparsed>`` used to read as comparison operators.

    That shadowed every name-based rule, so a module-scope authorization guard was reported as
    a boundary change at impact 64 instead of an authorization change at impact 92.
    """
    result = classify_delta(
        _condition_delta(symbol, "is_admin_user(request)", "has_role(request)"), RulesConfig()
    )
    assert result[0] is BehaviorCategory.AUTHORIZATION


def test_real_comparison_operators_still_classify_as_boundary() -> None:
    result = classify_delta(_condition_delta("<module>", "count < 3", "count < 5"), RulesConfig())
    assert result[0] is BehaviorCategory.BOUNDARY


def test_scalar_language_section_is_a_configuration_error(tmp_path: Path) -> None:
    """``language: python`` used to raise AttributeError and surface as an opaque internal error."""
    (tmp_path / ".semantic-diff-weaver.yaml").write_text("language: python\n", encoding="utf-8")
    request = AnalyzeRequest(repo_path=str(tmp_path), base_ref="HEAD")
    with pytest.raises(WeaverError) as caught:
        load_config(tmp_path, request)
    assert caught.value.code is ErrorCode.CONFIGURATION_ERROR


def test_bounded_process_closes_its_pipes(tmp_path: Path) -> None:
    """Every Git child used to leak a ResourceWarning per stream until refcounting collected it."""
    executable = shutil.which("git")
    if executable is None:  # pragma: no cover - Git is a documented requirement
        pytest.skip("Git is unavailable")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        for _ in range(5):
            run_bounded_process(
                [executable, "--version"],
                cwd=tmp_path,
                env={"PATH": os.environ.get("PATH", "")},
                input_data=None,
                max_bytes=4096,
            )
    assert [item for item in caught if item.category is ResourceWarning] == []


def test_malformed_numstat_counts_are_skipped_not_raised() -> None:
    """Untrusted Git metadata must not escape as a bare ValueError past the error contract."""
    stats, total = parse_numstat(b"x\ty\tbad.py\x001\t2\tgood.py\x00")
    assert stats == {"good.py": (1, 2, False)}
    assert total == 3


def test_truncated_rename_record_is_dropped_not_indexed_past_the_end() -> None:
    files = parse_name_status(b"M\x00kept.py\x00R100\x00only_old_path")
    assert [item.path for item in files] == ["kept.py"]


def test_exclusion_reason_tolerates_an_empty_path() -> None:
    assert exclusion_reason("") is None


@REQUIRES_PEP695
@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("def f[T](x: T) -> T:\n    return x\n", "def f[T, U](x: T) -> T:\n    return x\n"),
        ("def f[T](x: T) -> T:\n    return x\n", "def f[T: int](x: T) -> T:\n    return x\n"),
        ("class C[T]:\n    v = 1\n", "class C[T, U]:\n    v = 1\n"),
    ],
)
def test_type_parameter_changes_are_reported_as_signature_changes(old: str, new: str) -> None:
    """These used to fingerprint identically and be dropped as low-risk refactors."""
    pair = SourceRevisionPair(
        path="m.py",
        old_path="m.py",
        new_path="m.py",
        old_text=old,
        new_text=new,
        hunks=(SourceHunk(id="hunk-001", old_start=1, old_count=20, new_start=1, new_count=20),),
    )
    assert [item.kind for item in analyze_ast([pair]).deltas] == ["signature_change"]


@REQUIRES_PEP695
def test_type_parameters_survive_the_class_shell_rebuild() -> None:
    symbols = {item.qualified_name: item for item in extract_symbols("class C[T]:\n    v = 1\n")}
    assert symbols["C"].signature.startswith("[T]")


def test_type_parameters_are_empty_without_the_field() -> None:
    assert type_parameters(ast.parse("x = 1").body[0]) == ""


def _deadline_files(count: int = 3) -> list[SourceRevisionPair]:
    return [
        SourceRevisionPair(
            path=f"m{index}.py",
            old_path=f"m{index}.py",
            new_path=f"m{index}.py",
            old_text="def f(x):\n    return x\n",
            new_text="def f(x):\n    return x + 1\n",
            hunks=(SourceHunk(id="hunk-001", old_start=1, old_count=5, new_start=1, new_count=5),),
        )
        for index in range(count)
    ]


def _expired_budget() -> AstBudget:
    base = AstBudget.default()
    return AstBudget(
        max_nodes_per_file=base.max_nodes_per_file,
        max_depth=base.max_depth,
        max_symbols_per_file=base.max_symbols_per_file,
        max_source_bytes_per_version=base.max_source_bytes_per_version,
        max_source_bytes_total=base.max_source_bytes_total,
        max_extracted_symbols_total=base.max_extracted_symbols_total,
        analysis_timeout_seconds=0.0,
    )


def test_a_spent_budget_halts_regardless_of_platform_clock_resolution() -> None:
    """A zero timeout must halt even when two clock reads fall inside one tick.

    ``time.monotonic()`` is only as fine as the platform clock — about 15.6ms on Windows before
    Python 3.13 — so a strict ``>`` let an already-spent budget read as live there.
    """
    files = _deadline_files()
    result = analyze_ast(files, _expired_budget())
    assert result.resource_limited_files == len(files)
    assert result.parsed_files == 0
    assert {item.kind for item in result.deltas} == {"parse_incomplete"}


def test_matching_phase_respects_the_analysis_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deadline used to cover parsing only, leaving matching and comparison unbounded.

    Both phases report an incomplete file identically, so the budget is spent deterministically
    after the parse phase rather than by wall clock — otherwise this would silently re-test the
    parse-phase check it is meant to complement.
    """
    files = _deadline_files()
    remaining_parse_checks = len(files)

    def fake_deadline_reached(deadline: float) -> bool:
        nonlocal remaining_parse_checks
        if remaining_parse_checks > 0:
            remaining_parse_checks -= 1
            return False
        return True

    monkeypatch.setattr(analyze_module, "_deadline_reached", fake_deadline_reached)
    result = analyze_ast(files)
    assert remaining_parse_checks == 0
    assert result.resource_limited_files == len(files)
    assert result.parsed_files == 0
    assert {item.kind for item in result.deltas} == {"parse_incomplete"}


def test_every_category_has_a_complete_profile() -> None:
    """The registry is built from three tables; a missing entry must fail loudly at import."""
    for category in BehaviorCategory:
        profile = CATEGORY_PROFILES[category]
        assert profile.scenarios and profile.test_terms and profile.impact >= 0


def test_git_subprocess_boundary_never_uses_a_shell() -> None:
    source = Path("semantic_diff_weaver/git_diff/process.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    popen_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Popen"
    ]
    assert popen_calls
    for call in popen_calls:
        shell = next(item for item in call.keywords if item.arg == "shell")
        assert isinstance(shell.value, ast.Constant) and shell.value.value is False


def test_no_production_module_enables_a_shell() -> None:
    for path in Path("semantic_diff_weaver").rglob("*.py"):
        assert "shell=True" not in path.read_text(encoding="utf-8")
