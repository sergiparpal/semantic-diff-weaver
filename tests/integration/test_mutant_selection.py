"""The mutant measurement of `select`, end to end on small repositories: tests pytest runs,
tests that start the product as a process, and tests that are plain scripts."""

from __future__ import annotations

import json
import os
import py_compile
import sys
import time
from pathlib import Path

import pytest

from scripts import mutate_select
from scripts.mutate_select import main, run_bounded, run_environment, run_tests, summarize

# Each run clones, records coverage, and runs `select` and the tests once a mutant: a few dozen
# child processes, which a Windows runner starts slowly.
pytestmark = pytest.mark.timeout(180)

PACKAGE = {
    "pkg/__init__.py": "",
    "pkg/mod.py": "def allowed(x):\n    return x < 5\n",
    "tests/__init__.py": "",
    "tests/test_mod.py": (
        "from pkg.mod import allowed\n\n\n"
        "def test_allowed():\n    assert allowed(4)\n    assert not allowed(5)\n"
    ),
}
PROCESS = {
    "pkg/__init__.py": "",
    "pkg/__main__.py": "print(1 + 1)\n",
    "tests/__init__.py": "",
    "tests/test_cli.py": (
        "import subprocess\nimport sys\n\n\n"
        "def test_cli():\n"
        "    run = subprocess.run([sys.executable, '-m', 'pkg'], capture_output=True, text=True)\n"
        "    assert run.stdout.strip() == '2'\n"
    ),
}
# A test that passes once and then fails, without and with any mutant alike.
FLAKY = {
    **PACKAGE,
    "tests/test_flaky.py": (
        "import os\nfrom pathlib import Path\n\nfrom pkg.mod import allowed\n\n\n"
        "def test_once():\n"
        "    marker = Path(os.environ['FLAKY_MARKER'])\n"
        "    ran = marker.exists()\n"
        "    marker.touch()\n"
        "    assert allowed(1) and not ran\n"
    ),
}
SCRIPTS = {
    "src/__init__.py": "",
    "src/calc.py": "def double(x):\n    return x + x\n",
    "test_unit.py": (
        "from src.calc import double\n\n\n"
        "def test_double():\n    assert double(2) == 4\n\n\n"
        'if __name__ == "__main__":\n    test_double()\n'
    ),
}


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _arguments(repo: Path, tmp_path: Path, *extra: str) -> list[str]:
    out = tmp_path / "mutants.jsonl"
    return [
        "run",
        "--repo",
        str(repo),
        "--cov-source",
        "pkg",
        "--out",
        str(out),
        "--scratch",
        str(tmp_path / "scratch"),
        *extra,
    ]


def _selects_nothing(clone: Path, base: str, head: str, *, work: Path):
    return [], False, "", 0.0


def test_mutants_of_a_pytest_suite_are_caught_by_the_tests_select_chose(
    repo_factory, tmp_path: Path
) -> None:
    repo, _, _ = repo_factory(PACKAGE, {**PACKAGE, "README.md": "x\n"})
    out = tmp_path / "mutants.jsonl"
    assert main(_arguments(repo, tmp_path, "--mutants", "1")) == 0
    assert len(_records(out)) == 1
    # A second run appends to the same file and goes on where the first stopped: `pkg/mod.py`
    # has two sites, `<` and `5`, and no test file is ever mutated.
    assert main(_arguments(repo, tmp_path, "--mutants", "5")) == 0
    records = _records(out)
    assert sorted((item["token"], item["replacement"]) for item in records) == [
        ("5", "6"),
        ("<", "<="),
    ]
    assert [item["index"] for item in records] == [1, 2]
    for item in records:
        assert item["path"] == "pkg/mod.py"
        assert item["covering"] == ["tests/test_mod.py"]
        assert item["killed_by"] == ["tests/test_mod.py::test_allowed"]
        assert item["selected"] == ["tests/test_mod.py"]
        assert (item["missed"], item["flaky"], item["escaped"]) == ([], [], False)
        assert item["tests_total"] == 1
    assert f"{repo.name}: 2 mutants on 1 bases\n  2 caught by a test" in summarize([out])
    # Once every site is measured, a run adds nothing.
    assert main(_arguments(repo, tmp_path)) == 0
    assert len(_records(out)) == 2


def test_a_line_only_a_started_process_runs_is_covered_by_the_test_that_starts_it(
    repo_factory, tmp_path: Path
) -> None:
    repo, _, _ = repo_factory(PROCESS, {**PROCESS, "README.md": "x\n"})
    assert main(_arguments(repo, tmp_path, "--mutants", "1")) == 0
    (record,) = _records(tmp_path / "mutants.jsonl")
    assert record["path"] == "pkg/__main__.py"
    assert record["covering"] == ["tests/test_cli.py"]
    assert record["killed_by"] == ["tests/test_cli.py::test_cli"]
    # `select` reads `-m pkg` as running the package.
    assert record["selected"] == ["tests/test_cli.py"]


def test_a_mutant_only_tests_select_left_out_caught_has_escaped(
    repo_factory, tmp_path: Path, monkeypatch
) -> None:
    repo, _, _ = repo_factory(PACKAGE, {**PACKAGE, "README.md": "x\n"})
    monkeypatch.setattr(mutate_select, "select", _selects_nothing)
    assert main(_arguments(repo, tmp_path, "--mutants", "1")) == 0
    (record,) = _records(tmp_path / "mutants.jsonl")
    assert record["killed_by"] == ["tests/test_mod.py::test_allowed"]
    assert record["missed"] == ["tests/test_mod.py::test_allowed"]
    assert (record["flaky"], record["escaped"]) == ([], True)
    assert "(escaped)" in summarize([tmp_path / "mutants.jsonl"])


def test_a_test_that_fails_again_without_its_mutant_caught_nothing(
    repo_factory, tmp_path: Path, monkeypatch
) -> None:
    repo, _, _ = repo_factory(FLAKY, {**FLAKY, "README.md": "x\n"})
    monkeypatch.setattr(mutate_select, "select", _selects_nothing)
    monkeypatch.setenv("FLAKY_MARKER", str(tmp_path / "ran"))
    assert main(_arguments(repo, tmp_path, "--mutants", "1")) == 0
    (record,) = _records(tmp_path / "mutants.jsonl")
    assert record["flaky"] == ["tests/test_flaky.py::test_once"]
    assert "tests/test_flaky.py::test_once" not in record["killed_by"]
    assert "tests/test_flaky.py::test_once" not in record["missed"]


def test_mutants_of_a_script_suite_run_each_test_file_as_a_script(
    repo_factory, tmp_path: Path
) -> None:
    repo, _, _ = repo_factory(SCRIPTS, {**SCRIPTS, "README.md": "x\n"})
    out = tmp_path / "mutants.jsonl"
    arguments = ["run", "--repo", str(repo), "--cov-source", "src", "--runner", "script"]
    arguments += ["--test-file", "test_unit.py", "--mutants", "5", "--out", str(out)]
    arguments += ["--scratch", str(tmp_path / "scratch")]
    assert main(arguments) == 0
    (record,) = _records(out)
    assert (record["token"], record["replacement"]) == ("+", "-")
    assert record["covering"] == ["test_unit.py"]
    assert record["killed_by"] == ["test_unit.py"]
    assert record["selected"] == ["test_unit.py"]
    assert record["missed"] == []


def test_a_mutant_never_runs_from_bytecode_compiled_before_it(tmp_path: Path, monkeypatch) -> None:
    # A bytecode cache is valid while its source keeps its size and the second it was written in,
    # which a mutant as long as the code it replaces, written within that second, does. CI's
    # runners write caches, where a developer's shell may not.
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    monkeypatch.delenv("PYTHONPYCACHEPREFIX", raising=False)
    source = tmp_path / "mod.py"
    (tmp_path / "test_mod.py").write_text(
        "from mod import f\n\n\ndef test_f():\n    assert f() == 1\n", encoding="utf-8"
    )
    env = run_environment(os.environ, tmp_path / "no-bytecode")

    def write(value: int, *, keeping: os.stat_result | None = None) -> None:
        source.write_text(f"def f():\n    return {value}\n", encoding="utf-8")
        if keeping is not None:
            os.utime(source, ns=(keeping.st_atime_ns, keeping.st_mtime_ns))

    def failures() -> set[str]:
        return run_tests(tmp_path, sys.executable, ["test_mod.py"], timeout=60, env=env)[1]

    write(1)
    written = source.stat()
    assert failures() == set()
    # No run writes a cache that a later one would read.
    write(2, keeping=written)
    assert failures() == {"test_mod.py::test_f"}
    # Nor is a cache in the tree read, as a test's own process may write with its own environment.
    write(1, keeping=written)
    cache = tmp_path / "__pycache__" / f"mod.{sys.implementation.cache_tag}.pyc"
    py_compile.compile(
        str(source),
        cfile=str(cache),
        doraise=True,
        invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP,
    )
    write(3, keeping=written)
    assert failures() == {"test_mod.py::test_f"}


@pytest.mark.skipif(os.name != "posix", reason="a process session is POSIX's")
def test_a_timeout_ends_the_processes_a_test_started(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    code = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(120)\n"
    )
    command = [sys.executable, "-c", code]
    assert run_bounded(command, cwd=tmp_path, env=os.environ, timeout=5) is None
    pid = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    pytest.fail(f"process {pid}, which the timed-out command started, still runs")


def test_a_script_suite_needs_its_test_files_named(capsys) -> None:
    with pytest.raises(SystemExit):
        main(["run", "--cov-source", "src", "--runner", "script", "--out", "x.jsonl"])
    assert "--test-file" in capsys.readouterr().err


def test_a_base_is_never_read_as_an_option(capsys) -> None:
    with pytest.raises(SystemExit):
        main(["run", "--cov-source", "src", "--base=--output=x", "--out", "x.jsonl"])
    assert "must name a revision" in capsys.readouterr().err


def test_an_interpreter_without_coverage_stops_the_run_before_any_clone(
    repo_factory, tmp_path: Path
) -> None:
    repo, _, _ = repo_factory(PACKAGE, {**PACKAGE, "README.md": "x\n"})
    with pytest.raises(SystemExit, match="needs coverage, pytest"):
        main(_arguments(repo, tmp_path, "--python", str(tmp_path / "no-python")))
    assert not (tmp_path / "scratch").exists()


def test_the_scratch_directory_stays_outside_the_repositories(repo_factory) -> None:
    repo, _, _ = repo_factory(PACKAGE, {**PACKAGE, "README.md": "x\n"})
    with pytest.raises(SystemExit, match="outside the repositories"):
        main(
            [
                "run",
                "--repo",
                str(repo),
                "--cov-source",
                "pkg",
                "--out",
                str(repo / "x.jsonl"),
                "--scratch",
                str(repo / "scratch"),
            ]
        )
