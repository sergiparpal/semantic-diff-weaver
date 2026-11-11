from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scripts import check_coverage


def _file_entry(*, branches: float = 91.0, covered: int = 91, total: int = 100) -> dict:
    return {
        "summary": {
            "percent_branches_covered": branches,
            "covered_branches": covered,
            "num_branches": total,
        }
    }


@pytest.mark.parametrize("separator", ["/", "\\"])
def test_coverage_policy_accepts_platform_path_separators(
    tmp_path: Path, monkeypatch, separator: str
) -> None:
    files: dict[str, dict] = {}
    for module in check_coverage.CRITICAL_MODULES:
        if module.endswith("/"):
            package = module.strip("/")
            files[f"semantic_diff_weaver{separator}{package}{separator}collect.py"] = _file_entry(
                covered=92, total=100
            )
            files[f"semantic_diff_weaver{separator}{package}{separator}process.py"] = _file_entry(
                covered=90, total=100
            )
        else:
            files[f"semantic_diff_weaver{separator}{module}"] = _file_entry()
    report = {"totals": {"percent_covered": 95.0}, "files": files}
    report_path = tmp_path / "coverage.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["check_coverage.py", str(report_path)])

    assert check_coverage.main() == 0


def test_coverage_policy_aggregates_package_branch_coverage(tmp_path: Path, monkeypatch) -> None:
    report = {
        "totals": {"percent_covered": 95.0},
        "files": {
            "semantic_diff_weaver/ast_diff/match.py": _file_entry(),
            "semantic_diff_weaver/baseline.py": _file_entry(),
            "semantic_diff_weaver/change_groups.py": _file_entry(),
            "semantic_diff_weaver/cli.py": _file_entry(),
            "semantic_diff_weaver/config.py": _file_entry(),
            "semantic_diff_weaver/coverage_map.py": _file_entry(),
            "semantic_diff_weaver/errors.py": _file_entry(),
            "semantic_diff_weaver/git_diff/collect.py": _file_entry(covered=95, total=100),
            "semantic_diff_weaver/git_diff/process.py": _file_entry(covered=85, total=100),
            "semantic_diff_weaver/obligations.py": _file_entry(),
            "semantic_diff_weaver/path_policy.py": _file_entry(),
            "semantic_diff_weaver/mcp_server.py": _file_entry(),
            "semantic_diff_weaver/plugin.py": _file_entry(),
            "semantic_diff_weaver/renderer.py": _file_entry(),
            "semantic_diff_weaver/schema_diff/compare.py": _file_entry(),
            "semantic_diff_weaver/scoring.py": _file_entry(),
            "semantic_diff_weaver/selection.py": _file_entry(),
            "semantic_diff_weaver/semantic_interpreter.py": _file_entry(),
            "semantic_diff_weaver/service.py": _file_entry(),
            "semantic_diff_weaver/skeletons.py": _file_entry(),
            "semantic_diff_weaver/test_mapper.py": _file_entry(),
        },
    }
    report_path = tmp_path / "coverage.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["check_coverage.py", str(report_path)])

    # Package aggregate is 90% even though process.py alone is 85%.
    assert check_coverage.main() == 0


def _report(tmp_path: Path) -> Path:
    files = {
        f"semantic_diff_weaver/{module}": _file_entry()
        for module in check_coverage.CRITICAL_MODULES
        if not module.endswith("/")
    }
    for module in check_coverage.CRITICAL_MODULES:
        if module.endswith("/"):
            files[f"semantic_diff_weaver/{module}a.py"] = _file_entry(covered=95, total=100)
    files["semantic_diff_weaver/path_policy.py"] = _file_entry(covered=92, total=100)
    files["semantic_diff_weaver/evidence_scenarios.py"] = _file_entry(covered=100, total=100)
    report_path = tmp_path / "coverage.json"
    report_path.write_text(
        json.dumps({"totals": {"percent_covered": 98.0}, "files": files}), encoding="utf-8"
    )
    return report_path


def test_per_module_lists_each_critical_module_lowest_first_and_the_ones_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = str(_report(tmp_path))

    code = check_coverage.main([report, "--per-module", "evidence_scenarios.py", "absent.py"])

    assert code == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == " 91.00% baseline.py"
    assert " 92.00% path_policy.py" in lines
    assert "100.00% evidence_scenarios.py" in lines
    assert lines[-2] == "missing absent.py"
    assert lines[-1].startswith("Coverage policy passed")
    assert len(lines) == len(check_coverage.CRITICAL_MODULES) + 3


def test_without_per_module_only_the_verdict_is_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert check_coverage.main([str(_report(tmp_path))]) == 0
    assert capsys.readouterr().out.startswith("Coverage policy passed")
