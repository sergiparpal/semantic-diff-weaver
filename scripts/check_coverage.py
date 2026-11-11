"""Enforce the repository's overall and critical-module branch coverage policy.

    python scripts/check_coverage.py coverage.json
    python scripts/check_coverage.py coverage.json --per-module evidence_scenarios.py

`--per-module` first prints each critical module's branch coverage, lowest first, and that of each
module it names, as `docs/evaluation.md` reports them for a release.
"""

from __future__ import annotations

import argparse
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
    # Parses an untrusted earlier analysis, whose validated fields reach the brief.
    "baseline.py",
    # Decides which findings an analysis says are connected, and through which entry points.
    "change_groups.py",
    # The command line parses untrusted argument input and decides authorization roots,
    # which is the same boundary rationale that puts path_policy and plugin on this list.
    "cli.py",
    "config.py",
    # Parses an untrusted third-party artifact and decides what the tool may claim
    # about test coverage; both halves of that make it a boundary module.
    "coverage_map.py",
    "errors.py",
    "git_diff/",
    # The MCP transport decodes untrusted JSON-RPC from the host and fixes the working
    # directory that authorization defaults to — the plugin.py boundary, over stdio.
    "mcp_server.py",
    "obligations.py",
    "path_policy.py",
    "plugin.py",
    "renderer.py",
    # Parses untrusted YAML and JSON under budgets of its own, and decides from their content
    # which of them are API schemas.
    "schema_diff/",
    "scoring.py",
    # Decides which tests a change lets a pipeline skip, from an untrusted head revision, and
    # hands their paths to a shell.
    "selection.py",
    "semantic_interpreter.py",
    "service.py",
    # Parses an untrusted earlier analysis and writes code from it, so what it lets through
    # reaches a test file once the patch is applied.
    "skeletons.py",
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


def per_module(files: dict[str, Any], modules: tuple[str, ...]) -> list[str]:
    """Each module's branch coverage, lowest first, and the modules the report lacks last."""
    found: list[tuple[float, str]] = []
    missing: list[str] = []
    for module in modules:
        matches = _matching_summaries(files, module)
        if matches:
            found.append((_branch_coverage(matches), module))
        else:
            missing.append(module)
    lines = [f"{value:6.2f}% {module}" for value, module in sorted(found)]
    return lines + [f"missing {module}" for module in missing]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("report", type=Path, help="coverage.py's JSON report")
    parser.add_argument(
        "--per-module",
        nargs="*",
        metavar="MODULE",
        help="also print each critical module's branch coverage, and each MODULE's",
    )
    arguments = parser.parse_args(argv)
    report = json.loads(arguments.report.read_text(encoding="utf-8"))
    if arguments.per_module is not None:
        named = tuple(m for m in arguments.per_module if m not in CRITICAL_MODULES)
        print("\n".join(per_module(report["files"], (*CRITICAL_MODULES, *named))))
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
