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
            "semantic_diff_weaver/config.py": _file_entry(),
            "semantic_diff_weaver/errors.py": _file_entry(),
            "semantic_diff_weaver/git_diff/collect.py": _file_entry(covered=95, total=100),
            "semantic_diff_weaver/git_diff/process.py": _file_entry(covered=85, total=100),
            "semantic_diff_weaver/obligations.py": _file_entry(),
            "semantic_diff_weaver/path_policy.py": _file_entry(),
            "semantic_diff_weaver/plugin.py": _file_entry(),
            "semantic_diff_weaver/renderer.py": _file_entry(),
            "semantic_diff_weaver/scoring.py": _file_entry(),
            "semantic_diff_weaver/semantic_interpreter.py": _file_entry(),
            "semantic_diff_weaver/service.py": _file_entry(),
            "semantic_diff_weaver/test_mapper.py": _file_entry(),
        },
    }
    report_path = tmp_path / "coverage.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["check_coverage.py", str(report_path)])

    # Package aggregate is 90% even though process.py alone is 85%.
    assert check_coverage.main() == 0
