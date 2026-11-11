"""Time the performance tests in several trees, in turn, as each release's timing run does.

`docs/evaluation.md` gives, for each release, the call phase of each test in `tests/performance`,
beside the previous release's code timed in the same session. The trees take turns, run after run,
so that what else the machine is doing weighs on all of them alike:

    git worktree add --detach ../previous v0.19.4
    python scripts/time_performance.py --runs 6 new=. old=../previous

Each tree's package is byte-compiled first, since with PYTHONDONTWRITEBYTECODE set a fresh worktree
would compile it on every run. Each run is `python -m pytest tests/performance` from the tree's own
directory, which puts that tree's package first on the path, and its JUnit report gives each test's
call phase, the duration `--durations` prints, to the millisecond. The script prints each test's
median and range per tree, then each tree's medians in test order, as `docs/evaluation.md` quotes
them. `--json PATH` also writes every run. Exit codes: 0 timed, 1 a run failed, 2 bad arguments.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from xml.etree import ElementTree

TESTS = "tests/performance"
PACKAGE = "semantic_diff_weaver"

# Runs the tests in a tree, writing their JUnit report to the path; returns the exit code and the
# end of the output.
Runner = Callable[[Path, Path], tuple[int, str]]
# Byte-compiles a tree's package.
Compiler = Callable[[Path], None]


class TimingError(Exception):
    """The trees cannot be timed as given."""


def run_tests(tree: Path, report: Path) -> tuple[int, str]:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            TESTS,
            "-q",
            "-p",
            "no:cacheprovider",
            "-o",
            "junit_duration_report=call",
            f"--junitxml={report}",
        ],
        cwd=tree,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return completed.returncode, (completed.stdout + completed.stderr)[-2000:]


def compile_package(tree: Path) -> None:
    subprocess.run(
        [sys.executable, "-m", "compileall", "-q", PACKAGE],
        cwd=tree,
        capture_output=True,
        check=True,
    )


def trees(values: list[str]) -> dict[str, Path]:
    """Each `NAME=TREE` argument, by name, after checking the tree holds the package and tests."""
    found: dict[str, Path] = {}
    for value in values:
        name, separator, path = value.partition("=")
        if not separator or not name or not path:
            raise TimingError(f"expected NAME=TREE, got {value!r}")
        if name in found:
            raise TimingError(f"the name {name!r} is given twice")
        tree = Path(path).resolve()
        if not (tree / PACKAGE).is_dir() or not (tree / TESTS).is_dir():
            raise TimingError(f"{tree} holds no {PACKAGE}/ and {TESTS}/")
        found[name] = tree
    if not found:
        raise TimingError("name at least one tree")
    return found


def call_durations(report: str) -> dict[str, float]:
    """Each test's call phase, in seconds, from a JUnit report pytest wrote, in the order it ran."""
    root = ElementTree.fromstring(report)  # noqa: S314 - pytest's own report, written just now
    return {
        str(case.get("name")): float(case.get("time") or 0.0)
        for case in root.iter("testcase")
        if case.find("skipped") is None
    }


def summary(results: dict[str, list[dict[str, float]]]) -> list[str]:
    """Each test's median and range per tree, then each tree's medians in test order."""
    order: list[str] = []
    for runs in results.values():
        for run in runs:
            order += [test for test in run if test not in order]
    lines = []
    medians: dict[str, list[str]] = {name: [] for name in results}
    for test in order:
        lines.append(test)
        for name, runs in results.items():
            times = [run[test] for run in runs if test in run]
            if not times:
                lines.append(f"  {name}  not run")
                medians[name].append("-")
                continue
            median = statistics.median(times)
            medians[name].append(f"{median:.2f}")
            lines.append(f"  {name}  {median:.2f}  ({min(times):.2f} to {max(times):.2f})")
    lines.append("")
    lines += [f"{name} medians: {', '.join(values)}" for name, values in medians.items()]
    return lines


def main(
    argv: list[str] | None = None,
    runner: Runner = run_tests,
    compiler: Compiler = compile_package,
) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("trees", nargs="+", metavar="NAME=TREE")
    parser.add_argument("--runs", type=int, default=6, help="runs per tree (default: 6)")
    parser.add_argument("--json", type=Path, help="also write every run's durations here")
    arguments = parser.parse_args(argv)
    if arguments.runs < 1:
        parser.error("--runs must be at least 1")
    try:
        named = trees(arguments.trees)
    except TimingError as error:
        parser.error(str(error))
    for tree in named.values():
        compiler(tree)
    results: dict[str, list[dict[str, float]]] = {name: [] for name in named}
    with tempfile.TemporaryDirectory(prefix="time-performance-") as temporary:
        for run in range(1, arguments.runs + 1):
            for name, tree in named.items():
                report = Path(temporary) / f"{name}-{run}.xml"
                code, output = runner(tree, report)
                if code != 0 or not report.is_file():
                    print(f"run {run} of {name} failed (exit {code}):\n{output}", file=sys.stderr)
                    return 1
                results[name].append(call_durations(report.read_text(encoding="utf-8")))
                print(f"run {run}/{arguments.runs} {name} done", file=sys.stderr, flush=True)
    print(f"Each test's call phase in seconds, median and range over {arguments.runs} runs:")
    print("\n".join(summary(results)))
    if arguments.json is not None:
        payload = {
            "runs": arguments.runs,
            "trees": {name: str(tree) for name, tree in named.items()},
            "results": results,
        }
        arguments.json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
