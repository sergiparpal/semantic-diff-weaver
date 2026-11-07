"""Measure `select` against mutants: does it ever leave out a test that catches one?

Each mutant is a small fault in one token of the product code, `<` for `<=` or `and` for `or`,
committed on a throwaway branch of a scratch clone. The script asks `semantic-diff-weaver select`
which test files the change can affect, then runs the tests that can see the mutated line and
records which of them caught it. A test that caught a mutant but was not selected is a miss, and a
mutant that only such tests caught escaped: running just the selected tests would have passed it.

To keep it cheap, each test file runs once per base under coverage, in a process of its own whose
context is the file's path, so what the file imports and the Python processes it starts count as
what it ran. A mutant then runs only the test files that ran its line, since no other can change
its result, unless it reads the code as text or starts Python without passing its environment on.

This runs the analyzed repository's tests, so it stays out of the package, which never executes
analyzed code. The tests run without the environment's credentials and must not need the network.
Each mutant is appended to a JSONL file, and a run skips the mutants the file already holds for
its base, so nightly runs with one seed continue a single sequence instead of repeating it:

    python scripts/mutate_select.py run --repo . --cov-source semantic_diff_weaver \\
        --mutants 20 --out ~/mutants/semantic-diff-weaver.jsonl
    python scripts/mutate_select.py summary ~/mutants/*.jsonl
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import io
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tokenize
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED = 20261108
DEFAULT_TIMEOUT_SECONDS = 600
# A mutant's tests may take this many times what they took at the base, under coverage, and at
# least the floor, before they count as timed out: a mutant may loop where the code ended.
TIMEOUT_FACTOR = 5
TIMEOUT_FLOOR_SECONDS = 60
# `select` exits 4 when the whole suite has to run. Its own bounds end it in seconds, so one that
# runs for minutes is a fault to report, not a selection.
RUN_ALL_EXIT = 4
SELECT_TIMEOUT_SECONDS = 300

# Each token a mutant may change, and what it changes to. Operators come from `tokenize`, never
# from text, so nothing in a string or a comment is touched.
OPERATOR_SWAPS = {
    "<": "<=",
    "<=": "<",
    ">": ">=",
    ">=": ">",
    "==": "!=",
    "!=": "==",
    "+": "-",
    "-": "+",
}
NAME_SWAPS = {"and": "or", "or": "and", "True": "False", "False": "True"}
# A short summary line, `FAILED t.py::test_a[x y] - AssertionError`: the node ID may hold spaces.
FAILED_LINE_RE = re.compile(r"^(?:FAILED|ERROR) (.+?)(?: - |$)")
# The variables the analyzed tests never see: credentials a test could spend or leak.
SECRET_NAME_RE = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH", re.IGNORECASE)


@dataclass(frozen=True)
class Site:
    """One token of a file a mutant can change: where it is, what it is, and what it becomes."""

    line: int
    column: int
    token: str
    replacement: str


@dataclass
class Mutant:
    """What one mutant was, what `select` chose for it, and which tests caught it."""

    path: str
    line: int
    column: int
    token: str
    replacement: str
    # The test files whose process ran the line at the base, which are the ones run.
    covering: list[str] = field(default_factory=list)
    outcome: str = ""
    killed_by: list[str] = field(default_factory=list)
    selected: list[str] = field(default_factory=list)
    run_all: bool = False
    select_reason: str = ""
    missed: list[str] = field(default_factory=list)
    # Tests that failed with the mutant and failed again without it, so caught nothing.
    flaky: list[str] = field(default_factory=list)
    escaped: bool = False
    select_seconds: float = 0.0
    test_seconds: float = 0.0


def mutation_sites(source: str) -> list[Site]:
    """Every token of `source` a mutant may change, in the order they appear."""
    sites: list[Site] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError):
        return sites
    for item in tokens:
        (line, column) = item.start
        if item.type == tokenize.OP and item.string in OPERATOR_SWAPS:
            sites.append(Site(line, column, item.string, OPERATOR_SWAPS[item.string]))
        elif item.type == tokenize.NAME and item.string in NAME_SWAPS:
            sites.append(Site(line, column, item.string, NAME_SWAPS[item.string]))
        elif item.type == tokenize.NUMBER and item.string.isdigit():
            sites.append(Site(line, column, item.string, str(int(item.string) + 1)))
    return sites


def apply(source: str, site: Site) -> str:
    """`source` with the site's token replaced, every other character as it was.

    Lines end where `tokenize` ends them, at each `\\n`, so a form feed or a `\\r\\n` stays put.
    """
    lines = source.split("\n")
    text = lines[site.line - 1]
    end = site.column + len(site.token)
    if text[site.column : end] != site.token:
        raise ValueError(f"line {site.line} holds no {site.token!r} at column {site.column}")
    lines[site.line - 1] = text[: site.column] + site.replacement + text[end:]
    return "\n".join(lines)


def changes_the_program(original: str, mutated: str) -> bool:
    """Whether the mutant still parses and is another program, not the same one respelled."""
    try:
        return ast.dump(ast.parse(original)) != ast.dump(ast.parse(mutated))
    except (SyntaxError, ValueError, RecursionError):
        return False


def nodeid_file(nodeid: str) -> str:
    """The file of a pytest node ID: `tests/test_a.py` for `tests/test_a.py::test_b[1]`."""
    return nodeid.split("::", 1)[0]


def failed_tests(output: str) -> set[str]:
    """The node IDs a pytest run's short summary (`-rfE`) names as failed or errored."""
    return {match[1] for line in output.splitlines() if (match := FAILED_LINE_RE.match(line))}


def missed_tests(killed_by: Iterable[str], selected: Iterable[str], run_all: bool) -> list[str]:
    """The tests that caught a mutant in a file `select` did not choose: none when it chose all."""
    if run_all:
        return []
    chosen = set(selected)
    return sorted(nodeid for nodeid in killed_by if nodeid_file(nodeid) not in chosen)


def mutant_timeout(files: Sequence[str], base_seconds: Mapping[str, float], cap: float) -> float:
    """How long a mutant's test files may run: a multiple of their time at the base, within
    bounds, so that a mutant that loops costs a little more than its tests, not the cap."""
    took = sum(base_seconds.get(name, cap) for name in files)
    return min(cap, max(TIMEOUT_FLOOR_SECONDS, TIMEOUT_FACTOR * took))


def has_escaped(killed_by: Sequence[str], missed: Sequence[str]) -> bool:
    """Whether a test caught the mutant and every test that did was one `select` left out."""
    return bool(killed_by) and set(killed_by) <= set(missed)


def run_environment(environ: Mapping[str, str], pycache: Path) -> dict[str, str]:
    """The environment the analyzed tests run in: this one without its credentials, and without
    bytecode caches.

    A cache is valid while its source keeps its size and the second it was written in, which a
    mutant as long as the code it replaces, written in that second, does: the code would run in its
    place, and the base in the mutant's. So nothing writes a cache, and `pycache`, which stays empty,
    replaces every `__pycache__` of the tree, including those a test's own processes may write.
    """
    kept = {name: value for name, value in environ.items() if not SECRET_NAME_RE.search(name)}
    return {**kept, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPYCACHEPREFIX": str(pycache)}


def contexts_by_line(coverage_file: Path, root: Path) -> dict[str, dict[int, set[str]]]:
    """Per repository-relative file and line, the test files whose process ran it."""
    from coverage import CoverageData

    if not coverage_file.exists():
        return {}
    data = CoverageData(basename=str(coverage_file))
    data.read()
    found: dict[str, dict[int, set[str]]] = {}
    for measured in data.measured_files():
        try:
            relative = Path(measured).resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
        lines: dict[int, set[str]] = {}
        for line, contexts in (data.contexts_by_lineno(measured) or {}).items():
            # The empty context is what ran outside any test file's process.
            if tests := {context for context in contexts if context}:
                lines[line] = tests
        found[relative] = lines
    return found


def measured_keys(out: Path, repository: str, base: str) -> set[tuple[str, int, int, str]]:
    """The mutants `out` already holds for a repository's base, which a run does not repeat."""
    if not out.exists():
        return set()
    keys: set[tuple[str, int, int, str]] = set()
    for line in out.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if item.get("repository") == repository and item.get("base") == base:
            keys.add((item["path"], item["line"], item["column"], item["replacement"]))
    return keys


def git_executable() -> str:
    found = shutil.which("git")
    if found is None:
        raise SystemExit("mutate_select: Git is unavailable")
    return found


def git(repo: Path, *arguments: str) -> str:
    completed = subprocess.run(
        [git_executable(), "-C", str(repo), *arguments],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        shell=False,
    )
    return completed.stdout.strip()


def check_interpreter(python: str, runner: str) -> None:
    """Stop before any clone when the interpreter cannot run what the measurement needs: coverage
    7.10 or later, the first to follow the processes a test starts, and pytest to run its tests."""
    modules = "coverage, pytest" if runner == "pytest" else "coverage"
    probe = f"import {modules}; raise SystemExit(coverage.version_info < (7, 10))"
    try:
        completed = subprocess.run(
            [python, "-c", probe], capture_output=True, check=False, shell=False
        )
    except OSError:
        completed = None
    if completed is None or completed.returncode != 0:
        raise SystemExit(f"mutate_select: {python} needs {modules}, coverage 7.10 or later")


def run_bounded(
    command: Sequence[str], *, cwd: Path, env: Mapping[str, str], timeout: float
) -> subprocess.CompletedProcess[str] | None:
    """Run a command of the analyzed repository's, or return None when it outlasts `timeout`.

    On POSIX the command runs in a session of its own, and a timeout kills the whole session: a
    mutant may loop in a process a test started, which killing the test runner alone leaves running.
    """
    posix = os.name == "posix"
    with subprocess.Popen(
        list(command),
        cwd=cwd,
        env=dict(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        start_new_session=posix,
        shell=False,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if posix:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            # A process that left the session may hold the output open; it is not waited for.
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.communicate(timeout=10)
            return None
    return subprocess.CompletedProcess(list(command), process.returncode, stdout, stderr)


def run_tests(
    clone: Path, python: str, tests: Sequence[str], *, timeout: float, env: Mapping[str, str]
) -> tuple[str, set[str], float]:
    """Run tests in the clone: their outcome (`passed`, `failed`, or `timeout`), the node IDs that
    failed or errored, and how long it took."""
    command = [python, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-rfE"]
    # A file that no longer imports must not keep the other files from running.
    command += ["--continue-on-collection-errors", *tests]
    started = time.perf_counter()
    completed = run_bounded(command, cwd=clone, env=env, timeout=timeout)
    if completed is None:
        return "timeout", set(), time.perf_counter() - started
    failures = failed_tests(completed.stdout)
    # 5 is pytest's "no tests ran"; 1 is some failed. Anything else broke the run itself.
    outcome = "failed" if failures or completed.returncode == 1 else "passed"
    if completed.returncode not in (0, 1, 5):
        tail = " | ".join((completed.stdout + completed.stderr).strip().splitlines()[-3:])
        outcome = f"error {completed.returncode}: {tail}"
    return outcome, failures, time.perf_counter() - started


def run_scripts(
    clone: Path, python: str, files: Sequence[str], *, timeout: float, env: Mapping[str, str]
) -> tuple[str, set[str], float]:
    """Run each test file as a script, as `python test_unit.py`, one that exits nonzero failing.

    A suite of plain scripts, as meal-manager's, has no test IDs: each file is one test.
    """
    started = time.perf_counter()
    failures: set[str] = set()
    for name in files:
        completed = run_bounded([python, name], cwd=clone, env=env, timeout=timeout)
        if completed is None:
            return "timeout", failures, time.perf_counter() - started
        if completed.returncode != 0:
            failures.add(name)
    return ("failed" if failures else "passed"), failures, time.perf_counter() - started


def record_coverage(
    clone: Path,
    python: str,
    files: Sequence[str],
    *,
    runner: str,
    timeout: float,
    work: Path,
    sources: Sequence[str],
    env: Mapping[str, str],
) -> tuple[Path, set[str], dict[str, float]]:
    """Run each test file under coverage in a process of its own, the file's path its context.

    One process a file also keeps coverage's writes out of the tests' way: with a context per test,
    coverage wrote its database as each test ended, and a test replacing `sqlite3.connect` for its
    own purpose broke every test after it. Coverage reads settings of this script's, never the
    repository's, and follows the Python processes a test starts, which keep the file's context.

    Returns the data file, what failed at the base, as node IDs or the path of a file that did not
    run, and how long each file took.
    """
    data_file = work / "base.coverage"
    rcfile = work / "coveragerc"
    measured = "".join(f"\n    {(clone / source).resolve()}" for source in sources)
    rcfile.write_text(
        f"[run]\ndata_file = {data_file}\npatch = subprocess\nsource ={measured}\n",
        encoding="utf-8",
    )
    failures: set[str] = set()
    seconds: dict[str, float] = {}
    for name in files:
        started = time.perf_counter()
        target = ["-m", "pytest", "-p", "no:cacheprovider", "-q", "-rfE", name]
        command = [python, "-m", "coverage", "run", f"--rcfile={rcfile}", f"--context={name}"]
        command += target if runner == "pytest" else [name]
        completed = run_bounded(
            command, cwd=clone, env={**env, "COVERAGE_CORE": "ctrace"}, timeout=timeout
        )
        seconds[name] = time.perf_counter() - started
        if completed is None:
            failures.add(name)
            continue
        if runner == "pytest":
            failures |= failed_tests(completed.stdout)
            if completed.returncode not in (0, 1, 5):
                failures.add(name)
        elif completed.returncode != 0:
            failures.add(name)
    # Following processes writes a data file each, which one file then combines.
    subprocess.run(
        [python, "-m", "coverage", "combine", f"--rcfile={rcfile}", "--quiet"],
        cwd=clone,
        capture_output=True,
        env=dict(env),
        check=False,
        shell=False,
    )
    return data_file, failures, seconds


def select(clone: Path, base: str, head: str, *, work: Path) -> tuple[list[str], bool, str, float]:
    """What `select` chooses for a change: its test files, whether it runs them all, and why."""
    started = time.perf_counter()
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "semantic_diff_weaver",
            "select",
            "--repo",
            str(clone),
            "--base",
            base,
            "--head",
            head,
        ],
        # The tool under test is this checkout's, never the clone's, which holds the mutant when
        # the repository measured is this one.
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS": str(work)},
        timeout=SELECT_TIMEOUT_SECONDS,
        shell=False,
    )
    seconds = time.perf_counter() - started
    if completed.returncode == RUN_ALL_EXIT:
        return [], True, completed.stderr.strip(), seconds
    if completed.returncode != 0:
        raise RuntimeError(f"select failed ({completed.returncode}): {completed.stderr.strip()}")
    return [line for line in completed.stdout.splitlines() if line.strip()], False, "", seconds


def is_test_path(path: str, test_roots: Sequence[str]) -> bool:
    name = PurePosixPath(path).name
    return (
        any(path == root or path.startswith(f"{root.rstrip('/')}/") for root in test_roots)
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


def read_source(path: Path) -> str | None:
    """A file's text exactly as stored, line endings included, or None when it is not UTF-8."""
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return None


def candidate_sites(
    clone: Path, sources: Sequence[str], test_roots: Sequence[str]
) -> list[tuple[str, Site]]:
    """Every site of the product files under `sources`, by file path and position."""
    found: list[tuple[str, Site]] = []
    for source in sources:
        for path in sorted((clone / source).rglob("*.py")):
            relative = path.relative_to(clone).as_posix()
            text = read_source(path)
            if text is None or is_test_path(relative, test_roots):
                continue
            found.extend((relative, site) for site in mutation_sites(text))
    return found


def measure(arguments: argparse.Namespace) -> int:
    repo = Path(arguments.repo).resolve()
    scratch = Path(arguments.scratch or tempfile.gettempdir()).resolve()
    for root in (REPOSITORY_ROOT, repo):
        if scratch == root or root in scratch.parents:
            raise SystemExit("mutate_select: --scratch must be outside the repositories")
    check_interpreter(arguments.python, arguments.runner)
    scratch.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="sdw-mutants-", dir=scratch))
    try:
        return _measure(arguments, repo, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _measure(arguments: argparse.Namespace, repo: Path, work: Path) -> int:
    clone = work / "clone"
    subprocess.run(
        [git_executable(), "clone", "--quiet", "--no-hardlinks", str(repo), str(clone)],
        check=True,
        shell=False,
    )
    base = git(clone, "rev-parse", "--verify", f"{arguments.base}^{{commit}}")
    git(clone, "checkout", "--quiet", "--detach", base)
    git(clone, "config", "user.email", "mutants@example.invalid")
    git(clone, "config", "user.name", "Mutants")
    git(clone, "config", "commit.gpgsign", "false")
    out = Path(arguments.out).resolve()
    done = measured_keys(out, repo.name, base)
    env = run_environment(os.environ, work / "no-bytecode")
    test_roots = arguments.test_root
    scripts = arguments.runner == "script"
    if scripts:
        tests_total = sorted(arguments.test_file)
    else:
        tests_total = sorted(
            path.relative_to(clone).as_posix()
            for root in test_roots
            for path in (clone / root).rglob("*.py")
            if path.name.startswith("test_") or path.name.endswith("_test.py")
        )

    def run(files: Sequence[str], timeout: float) -> tuple[str, set[str], float]:
        if scripts:
            return run_scripts(clone, arguments.python, files, timeout=timeout, env=env)
        return run_tests(clone, arguments.python, files, timeout=timeout, env=env)

    print(f"base {base[:12]}: {len(tests_total)} test files under coverage", file=sys.stderr)
    data_file, base_failures, base_seconds = record_coverage(
        clone,
        arguments.python,
        tests_total,
        runner=arguments.runner,
        timeout=arguments.timeout,
        work=work,
        sources=arguments.cov_source,
        env=env,
    )
    covered = contexts_by_line(data_file, clone)
    print(
        f"base suite: {len(base_failures)} failing, {sum(base_seconds.values()):.0f} s",
        file=sys.stderr,
    )

    rng = random.Random(f"{arguments.seed}:{base}")  # noqa: S311 - draws mutants, not secrets
    sites = candidate_sites(clone, arguments.cov_source, test_roots)
    rng.shuffle(sites)
    out.parent.mkdir(parents=True, exist_ok=True)
    made = 0
    for path, site in sites:
        if made >= arguments.mutants:
            break
        if (path, site.line, site.column, site.replacement) in done:
            continue
        original = read_source(clone / path) or ""
        try:
            mutated = apply(original, site)
        except ValueError:
            continue
        if not changes_the_program(original, mutated):
            continue
        made += 1
        mutant = Mutant(path, site.line, site.column, site.token, site.replacement)
        git(clone, "checkout", "--quiet", "--force", "-B", "mutant", base)
        (clone / path).write_bytes(mutated.encode("utf-8"))
        git(clone, "commit", "--quiet", "-m", f"mutant {len(done) + made}", "--", path)
        head = git(clone, "rev-parse", "HEAD")
        mutant.selected, mutant.run_all, mutant.select_reason, mutant.select_seconds = select(
            clone, base, head, work=work
        )
        mutant.covering = sorted(covered.get(path, {}).get(site.line, ()))
        killed: list[str] = []
        timeout = mutant_timeout(mutant.covering, base_seconds, arguments.timeout)
        if mutant.covering:
            mutant.outcome, failures, mutant.test_seconds = run(mutant.covering, timeout)
            killed = sorted(
                nodeid
                for nodeid in failures
                if nodeid not in base_failures and nodeid_file(nodeid) not in base_failures
            )
        else:
            mutant.outcome = "not covered"
        git(clone, "checkout", "--quiet", "--force", "--detach", base)
        missed = missed_tests(killed, mutant.selected, mutant.run_all)
        if missed:
            # A miss is confirmed without the mutant: a test that fails there too caught nothing.
            _, again, _ = run(missed, timeout)
            mutant.flaky = [item for item in missed if item in again or nodeid_file(item) in again]
        mutant.killed_by = [item for item in killed if item not in mutant.flaky]
        mutant.missed = [item for item in missed if item not in mutant.flaky]
        mutant.escaped = has_escaped(mutant.killed_by, mutant.missed)
        record = {
            "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "repository": repo.name,
            "base": base,
            "seed": arguments.seed,
            "index": len(done) + made,
            "tests_total": len(tests_total),
            **asdict(mutant),
        }
        with out.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        verdict = ("ESCAPED " if mutant.escaped else "MISSED ") + ", ".join(mutant.missed)
        print(
            f"{len(done) + made:>3} {path}:{site.line} {site.token}->{site.replacement}: "
            f"{mutant.outcome}, killed by {len(mutant.killed_by)}, "
            f"selected {'all' if mutant.run_all else len(mutant.selected)}; "
            f"{verdict if mutant.missed else 'ok'}",
            file=sys.stderr,
        )
    return 0


def summarize(paths: Sequence[Path]) -> str:
    """The mutants of JSONL files in a few lines a repository: how many were caught and missed."""
    records = [
        json.loads(line)
        for path in paths
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    lines: list[str] = []
    for repository in sorted({item["repository"] for item in records}):
        mine = [item for item in records if item["repository"] == repository]
        killed = sum(bool(item["killed_by"]) for item in mine)
        missed = [item for item in mine if item["missed"]]
        escaped = sum(item["escaped"] for item in mine)
        flaky = sum(len(item["flaky"]) for item in mine)
        run_all = sum(item["run_all"] for item in mine)
        fractions = [
            len(item["selected"]) / item["tests_total"]
            for item in mine
            if not item["run_all"] and item["tests_total"]
        ]
        mean = sum(fractions) / len(fractions) if fractions else 0.0
        lines += [
            f"{repository}: {len(mine)} mutants on {len({item['base'] for item in mine})} bases",
            f"  {killed} caught by a test, "
            f"{sum(item['outcome'] == 'timeout' for item in mine)} timed out, "
            f"{sum(item['outcome'] == 'not covered' for item in mine)} on lines no test runs, "
            f"{sum(item['outcome'].startswith('error') for item in mine)} broke the test run",
            f"  select chose the whole suite {run_all} times, and otherwise {mean:.0%} of the "
            "test files on average",
            f"  missed: {len(missed)} mutants caught by a test select left out, {escaped} by no "
            f"test it chose; {flaky} tests failed again without their mutant",
        ]
        lines += [
            f"    {item['path']}:{item['line']} {item['token']}->{item['replacement']}: "
            f"{', '.join(item['missed'])}{' (escaped)' if item['escaped'] else ''}"
            for item in missed
        ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="measure mutants and append them to a JSONL file")
    run.add_argument("--repo", default=".", help="repository to mutate, cloned first")
    run.add_argument("--base", default="HEAD", help="revision the mutants are made from")
    run.add_argument(
        "--cov-source",
        action="append",
        required=True,
        help="product directory to mutate and measure, such as semantic_diff_weaver",
    )
    run.add_argument("--test-root", action="append", default=None, help="test directory")
    run.add_argument(
        "--runner",
        choices=("pytest", "script"),
        default="pytest",
        help="run the tests with pytest, or each test file as a plain script",
    )
    run.add_argument(
        "--test-file",
        action="append",
        default=[],
        help="with --runner script, a test file to run as a script",
    )
    run.add_argument("--mutants", type=int, default=20)
    run.add_argument("--seed", type=int, default=DEFAULT_SEED)
    run.add_argument("--out", required=True, help="JSONL file each mutant is appended to")
    run.add_argument("--scratch", help="directory for the clone, outside the repositories")
    run.add_argument(
        "--python",
        default=sys.executable,
        help="interpreter with the repository's test needs, coverage and pytest included",
    )
    run.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    report = commands.add_parser("summary", help="summarize JSONL files of mutants")
    report.add_argument("paths", nargs="+", type=Path)
    arguments = parser.parse_args(argv)
    if arguments.command == "summary":
        print(summarize(arguments.paths))
        return 0
    arguments.test_root = arguments.test_root or ["tests"]
    if arguments.runner == "script" and not arguments.test_file:
        parser.error("--runner script needs at least one --test-file")
    if arguments.base.startswith("-"):
        parser.error("--base must name a revision, not an option")
    return measure(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
