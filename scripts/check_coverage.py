"""Enforce the repository's overall and critical-module branch coverage policy."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

OVERALL_MINIMUM = 85.0
BRANCH_MINIMUM = 90.0
# File paths end with ".py"; package paths end with "/" and aggregate member coverage.
CRITICAL_MODULES = (
    # ast_diff/ holds the densest algorithms in the project — similarity matching, the delta
    # suppression cascade, and the immutable safety budgets — so it is held to the branch bar
    # rather than only to the overall statement floor.
    "ast_diff/",
    # The command line parses untrusted argument input and decides authorization roots,
    # which is the same boundary rationale that puts path_policy and plugin on this list.
    "cli.py",
    "config.py",
    # Parses an untrusted third-party artifact and decides what the tool may claim
    # about test coverage; both halves of that make it a boundary module.
    "coverage_map.py",
    "errors.py",
    "git_diff/",
    "obligations.py",
    "path_policy.py",
    "plugin.py",
    "renderer.py",
    "scoring.py",
    "semantic_interpreter.py",
    "service.py",
    "test_mapper.py",
)


def _normalize(path: str) -> str:
    return path.replace("\\", "/")


def _matching_summaries(files: dict[str, Any], module: str) -> list[dict[str, Any]]:
    target = _normalize(module)
    matches: list[dict[str, Any]] = []
    for path, value in files.items():
        normalized = _normalize(path)
        if target.endswith("/"):
            package = target.strip("/")
            if f"/{package}/" in f"/{normalized}" or normalized.endswith(f"/{package}"):
                matches.append(value["summary"])
        elif normalized.endswith(f"/{target}"):
            matches.append(value["summary"])
    return matches


def _branch_coverage(summaries: list[dict[str, Any]]) -> float:
    covered = sum(int(item.get("covered_branches", 0)) for item in summaries)
    total = sum(int(item.get("num_branches", 0)) for item in summaries)
    if total == 0:
        # Fall back to the reported percentage when branch data is absent (synthetic tests).
        if not summaries:
            return 0.0
        return float(summaries[0].get("percent_branches_covered", 0.0))
    return 100.0 * covered / total


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/check_coverage.py COVERAGE_JSON", file=sys.stderr)
        return 2
    report_path = Path(sys.argv[1])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    overall = float(report["totals"]["percent_covered"])
    if overall < OVERALL_MINIMUM:
        failures.append(f"overall coverage {overall:.2f}% is below {OVERALL_MINIMUM:.0f}%")
    files = report["files"]
    for module in CRITICAL_MODULES:
        matches = _matching_summaries(files, module)
        if module.endswith("/"):
            if not matches:
                failures.append(f"critical package {module} is missing in the report")
                continue
        elif len(matches) != 1:
            failures.append(f"critical module {module} is missing or duplicated in the report")
            continue
        branches = _branch_coverage(matches)
        if branches < BRANCH_MINIMUM:
            failures.append(
                f"{module} branch coverage {branches:.2f}% is below {BRANCH_MINIMUM:.0f}%"
            )
    if failures:
        print("Coverage policy failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print(
        f"Coverage policy passed: {overall:.2f}% overall and at least "
        f"{BRANCH_MINIMUM:.0f}% branches in critical modules."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
