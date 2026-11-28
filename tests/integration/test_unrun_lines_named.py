"""With a coverage report, a finding's obligation names the changed lines of its own code that no
test ran, over a real temporary repository; without one, nothing changes.

In the Action's field test, a callable the change added asked for each of its outcomes, and the
change's own tests had run every changed line of it but an error path. Those lines are what is left
to test, and a function's span holds what is nested in it, and a module's the whole file, so a
finding names only its own.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from semantic_diff_weaver.service import analyze

OLD = {"tool.py": "LIMIT = 1\n\n\ndef load(path):\n    return path.read_text()\n"}
NEW = {
    "tool.py": (
        "LIMIT = 2\n"  # 1
        "\n"
        "\n"
        "def load(path):\n"  # 4
        "    return path.read_text()\n"  # 5
        "\n"
        "\n"
        "def parse(text):\n"  # 8
        '    """Read one record."""\n'  # 9
        "    try:\n"  # 10
        "        return int(text)\n"  # 11
        "    except ValueError:\n"  # 12
        "        raise LookupError(text) from None\n"  # 13
    )
}
# The tests ran the module, `load`, and `parse` up to its `return`, never its handler.
REPORT = {
    "meta": {"version": "7.15.2"},
    "files": {
        "/ci/workspace/tool.py": {
            "executed_lines": [1, 4, 5, 8, 10, 11],
            "missing_lines": [12, 13],
        }
    },
    "totals": {},
}
CLAUSE = (
    "no test ran the changed lines 12-13 of `tool.py`: `except ValueError:` and "
    "`raise LookupError(text) from None`"
)


def _analysis(repo_factory, report: dict[str, Any] | None) -> dict[str, Any]:
    repo, base, head = repo_factory(OLD, NEW)
    request: dict[str, Any] = {
        "repo_path": str(repo),
        "base_ref": base,
        "head_ref": head,
        "output_format": "both",
    }
    if report is not None:
        path = Path(repo) / "coverage.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        request["coverage_report"] = str(path)
    return analyze(request)["analysis"]


def _obligations_of(analysis: dict[str, Any], symbol: str) -> list[dict[str, Any]]:
    ids = {
        behavior["id"]
        for behavior in analysis["behavior_changes"]
        if any(item["symbol"] == symbol for item in behavior["evidence"])
    }
    assert ids, symbol
    return [
        obligation
        for obligation in analysis["test_obligations"]
        if ids & set(obligation["behavior_change_ids"])
    ]


def test_the_obligation_names_the_changed_lines_no_test_ran(repo_factory) -> None:
    analysis = _analysis(repo_factory, REPORT)
    naming = [item for item in _obligations_of(analysis, "parse") if CLAUSE in item["given"]]
    assert len(naming) == 1
    assert naming[0]["given"].endswith(f"; {CLAUSE}")


def test_without_a_report_no_obligation_names_lines(repo_factory) -> None:
    analysis = _analysis(repo_factory, None)
    assert not any("no test ran" in item["given"] for item in analysis["test_obligations"])


def test_a_module_finding_does_not_name_the_lines_of_its_functions(repo_factory) -> None:
    """The `<module>` span is the whole file, and `parse`'s lines are `parse`'s own."""
    analysis = _analysis(repo_factory, REPORT)
    for obligation in _obligations_of(analysis, "<module>"):
        if set(obligation["behavior_change_ids"]) <= {
            behavior["id"]
            for behavior in analysis["behavior_changes"]
            if all(item["symbol"] == "<module>" for item in behavior["evidence"])
        }:
            assert "no test ran" not in obligation["given"]


def test_a_file_the_report_does_not_cover_names_no_line(repo_factory) -> None:
    """An unmatched path is a gap in the report, not in the tests."""
    report = {**REPORT, "files": {"elsewhere/other.py": REPORT["files"]["/ci/workspace/tool.py"]}}
    analysis = _analysis(repo_factory, report)
    assert not any("no test ran" in item["given"] for item in analysis["test_obligations"])
