"""The interleaved timing run of the performance tests that each release records."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import time_performance

REPORT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="3">
<testcase classname="tests.performance.test_performance" name="test_fast" time="{fast}"/>
<testcase classname="tests.performance.test_performance" name="test_slow" time="{slow}"/>
<testcase classname="tests.performance.test_performance" name="test_skipped" time="0.001">
<skipped message="no fixture"/></testcase>
</testsuite></testsuites>
"""


def _tree(tmp_path: Path, name: str) -> Path:
    tree = tmp_path / name
    (tree / "semantic_diff_weaver").mkdir(parents=True)
    (tree / "tests" / "performance").mkdir(parents=True)
    return tree


class _Runner:
    """pytest as the script sees it: a report per run, from a queue of call durations."""

    def __init__(self, durations: dict[Path, list[tuple[float, float]]], code: int = 0) -> None:
        self.durations = durations
        self.code = code
        self.order: list[Path] = []

    def __call__(self, tree: Path, report: Path) -> tuple[int, str]:
        self.order.append(tree)
        fast, slow = self.durations[tree].pop(0)
        report.write_text(REPORT.format(fast=fast, slow=slow), encoding="utf-8")
        return self.code, "1 failed" if self.code else ""


def test_the_trees_take_turns_and_each_gets_its_medians(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    new, old = _tree(tmp_path, "new"), _tree(tmp_path, "old")
    runner = _Runner({new: [(0.02, 0.6), (0.03, 0.62), (0.02, 0.7)], old: [(0.02, 0.5)] * 3})
    compiled: list[Path] = []
    out = tmp_path / "timing.json"

    code = time_performance.main(
        ["--runs", "3", "--json", str(out), f"new={new}", f"old={old}"],
        runner=runner,
        compiler=compiled.append,
    )

    assert code == 0
    assert compiled == [new, old]
    assert runner.order == [new, old, new, old, new, old]
    printed = capsys.readouterr().out
    assert "  new  0.62  (0.60 to 0.70)" in printed
    assert "new medians: 0.02, 0.62" in printed
    assert "old medians: 0.02, 0.50" in printed
    assert "test_skipped" not in printed
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["runs"] == 3
    assert written["results"]["new"][1] == {"test_fast": 0.03, "test_slow": 0.62}


def test_a_failed_run_stops_the_timing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tree = _tree(tmp_path, "new")
    runner = _Runner({tree: [(0.02, 0.6)]}, code=1)

    assert time_performance.main([f"new={tree}"], runner=runner, compiler=lambda path: None) == 1
    assert "run 1 of new failed (exit 1)" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        pytest.param(["new"], "expected NAME=TREE", id="no-separator"),
        pytest.param(["new=."], "holds no semantic_diff_weaver/", id="not-a-tree"),
        pytest.param(["--runs", "0", "new=."], "--runs must be at least 1", id="no-runs"),
    ],
)
def test_bad_arguments_are_refused(
    arguments: list[str],
    message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as raised:
        time_performance.main(arguments, runner=_Runner({}), compiler=lambda path: None)
    assert raised.value.code == 2
    assert message in capsys.readouterr().err


def test_a_name_given_twice_is_refused(tmp_path: Path) -> None:
    tree = _tree(tmp_path, "new")
    with pytest.raises(time_performance.TimingError, match="given twice"):
        time_performance.trees([f"new={tree}", f"new={tree}"])


def test_a_test_one_tree_lacks_is_shown_as_not_run() -> None:
    lines = time_performance.summary(
        {"new": [{"test_a": 0.1, "test_b": 0.2}], "old": [{"test_a": 0.1}]}
    )
    assert "  old  not run" in lines
    assert "old medians: 0.10, -" in lines
