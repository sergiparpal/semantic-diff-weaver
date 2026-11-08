"""Measure `select` against real red CI runs: does it leave out a test file that failed?

`select` names the test files a committed change can affect. `mutate_select.py` tests it against
faults made on purpose; this tests it against failures that happened. The repositories this
project is tried on give none: their pre-commit hooks run the whole suite, so no commit reaches CI
red. Public projects' pull requests do, so this replays their red runs offline.

For each red run of a pull request it reads which tests failed from the failed jobs' logs, in the
short summary pytest prints and in unittest's `FAIL:` and `ERROR:` lines, and keeps the files that
exist in the head the run tested. It asks this checkout's `select` which test files the pull
request's change can affect, from its merge base with the base branch as the branch stood when the
run started, and records whether each failed file was among them. It asks twice: with the default
configuration, and with `selection.ignore` set to `DOCUMENTATION_IGNORE`, a list of documentation
and changelog paths fixed before the first record. Red runs of pushes to the base branch are kept
too, without a selection, as failures no pull request made.

A failed file `select` left out is a miss only if nothing else explains the failure. `summary`
counts it as shared when the same file also failed, within `SHARED_WINDOW`, on the base branch or
in another pull request whose change `select` did not connect to it either. A failure from a new
dependency release or a flaky test looks like that. It lists the rest, which are to be read.

Nothing from the projects runs: no test, no install, no hook. Their logs and paths are untrusted
data, matched with patterns and kept only when they name a file of the head revision. Job logs
need a token even for public repositories, so it reads the GitHub API with GITHUB_TOKEN, and
sends the token to the API alone, never to the host a log redirects to. Like `mutate_select.py`,
it stays out of the package:

    GITHUB_TOKEN=... python scripts/shadow_select.py run --repo agronholm/anyio \\
        --scratch ~/shadow --out ~/shadow/results.jsonl --limit 30
    python scripts/shadow_select.py summary ~/shadow/results.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from semantic_diff_weaver.selection import (  # noqa: E402
    COLLECTED_NAMES,
    UNCOLLECTED_DIRECTORIES,
)

API_ROOT = "https://api.github.com/"
# A job's log redirects to a short-lived signed URL on one of these hosts, fetched without the token.
LOG_HOSTS = (".blob.core.windows.net", ".actions.githubusercontent.com")
MAX_LOG_BYTES = 32 * 1024 * 1024
# A matrix fails many jobs alike; past this many, the rest add cost and rarely a failed file.
MAX_FAILED_JOBS = 30
MAX_PAGES = 10
PAGE = 100
DEFAULT_LIMIT = 30
SHARED_WINDOW = timedelta(days=3)
RUN_ALL_EXIT = 4
SELECT_TIMEOUT_SECONDS = 300
# Paths no test of a typical project reads: documentation and changelog fragments. Fixed on
# 2026-11-08, before the first record; `.github/` is left out, since a workflow change can change
# what every test runs on.
DOCUMENTATION_IGNORE = (
    "docs/**",
    "doc/**",
    "changelog.d/**",
    "changelog/**",
    "changes/**",
    "news/**",
    "newsfragments/**",
    "**/*.md",
    "*.rst",
)
PROFILES = ("default", "documentation")

REPOSITORY_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
SHA_RE = re.compile(r"[0-9a-f]{40}")
# A branch name as Git takes it after `refs/remotes/origin/`: never an option, never `..`.
BRANCH_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z ?")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# pytest's short summary: `FAILED t.py::test_a[x y] - AssertionError`, and `ERROR t.py` when a
# file fails to collect. The node ID may hold spaces. A project whose `-r` leaves failures out of
# the summary still prints them in its progress, read by the next three.
PYTEST_LINE_RE = re.compile(r"^(?:FAILED|ERROR) (.+?)(?: - |$)")
# pytest's progress, `tests/test_a.py ..F.s  [ 40%]`, one letter a test, wrapped onto lines of
# letters alone when a file's do not fit on one.
PROGRESS_RE = re.compile(r"^(\S+\.py) ([.sxXFER]+)(?: +\[ *\d+%\])?$")
CONTINUED_RE = re.compile(r"^([.sxXFER]+)(?: +\[ *\d+%\])?$")
# pytest -v, `tests/test_a.py::test_b FAILED  [ 40%]`, and pytest-xdist, `[gw1] [ 40%] FAILED x`.
VERBOSE_RE = re.compile(r"^(\S+\.py::\S.*?) (?:FAILED|ERROR)(?: +\[ *\d+%\])?$")
XDIST_RE = re.compile(r"^\[gw\d+\] \[ *\d+%\] (?:FAILED|ERROR) (\S.*?)$")
# unittest: `FAIL: test_a (tests.test_mod.Case.test_a)`, without the method's name before 3.11.
UNITTEST_LINE_RE = re.compile(r"^(?:FAIL|ERROR): \w+ \(([A-Za-z_][\w.]*)\)")
MAX_NAME_LENGTH = 1000
# Red runs older than this have most likely lost their logs, which GitHub keeps 90 days.
LOG_DAYS = 85


@dataclass
class Selection:
    """What `select` chose for a change under one configuration, or why it could not say."""

    run_all: bool = False
    tests: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    error: str | None = None
    seconds: float = 0.0


@dataclass
class Record:
    """One red run, what failed in it, and what `select` chose for its change."""

    repository: str
    kind: str
    run_id: int
    run_url: str
    workflow: str
    created_at: str
    head: str
    head_label: str
    outcome: str
    base_ref: str | None = None
    base: str | None = None
    pull_request: int | None = None
    failed_jobs: list[dict[str, Any]] = field(default_factory=list)
    failures: dict[str, list[str]] = field(default_factory=dict)
    unmatched: int = 0
    test_files: int = 0
    selections: dict[str, dict[str, Any]] = field(default_factory=dict)
    profile_sha256: str = ""
    tool: str = ""
    measured_at: str = ""


def clean_line(line: str) -> str:
    """A log line as the program printed it: the runner's timestamp and color codes taken off."""
    return ANSI_RE.sub("", TIMESTAMP_RE.sub("", line, count=1)).rstrip()


def failed_names(log: str) -> tuple[set[str], set[str]]:
    """The pytest node IDs or test file paths, and the unittest dotted names, a job's log names
    as failed or errored."""
    nodeids: set[str] = set()
    dotted: set[str] = set()
    progressing: str | None = None
    for raw in log.splitlines():
        line = clean_line(raw)
        if len(line) > MAX_NAME_LENGTH:
            progressing = None
            continue
        if match := PROGRESS_RE.match(line):
            progressing = match[1]
            if set(match[2]) & {"F", "E"}:
                nodeids.add(progressing)
            continue
        if progressing and (match := CONTINUED_RE.match(line)):
            if set(match[1]) & {"F", "E"}:
                nodeids.add(progressing)
            continue
        progressing = None
        if match := PYTEST_LINE_RE.match(line):
            nodeids.add(match[1].strip())
        elif match := VERBOSE_RE.match(line):
            nodeids.add(match[1])
        elif match := XDIST_RE.match(line):
            nodeids.add(match[1])
        elif match := UNITTEST_LINE_RE.match(line):
            dotted.add(match[1])
    return nodeids, dotted


def basename_index(files: Iterable[str]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = defaultdict(list)
    for path in files:
        index[PurePosixPath(path).name].append(path)
    return index


def file_of_path(path: str, files: frozenset[str], index: Mapping[str, list[str]]) -> str | None:
    """The head's file a path from a log names: itself, or the one file it is the tail of.

    A runner that starts pytest below the repository's root prints paths relative to there, and a
    test that runs pytest itself prints paths in its own temporary directory, which match none.
    """
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    if not path or path.startswith("/"):
        return None
    if path in files:
        return path
    tails = [
        candidate
        for candidate in index.get(PurePosixPath(path).name, ())
        if candidate.endswith(f"/{path}")
    ]
    return tails[0] if len(tails) == 1 else None


def file_of_dotted(name: str, files: frozenset[str], index: Mapping[str, list[str]]) -> str | None:
    """The head's file a unittest name such as `tests.test_mod.Case.test_a` was defined in."""
    parts = name.split(".")
    for length in range(len(parts), 0, -1):
        found = file_of_path("/".join(parts[:length]) + ".py", files, index)
        if found is not None:
            return found
    return None


def failed_files(logs: Iterable[str], files: frozenset[str]) -> tuple[dict[str, list[str]], int]:
    """Each head file a failed test was in, with the tests; and how many names matched no file."""
    index = basename_index(files)
    found: dict[str, set[str]] = defaultdict(set)
    unmatched = 0
    for log in logs:
        nodeids, dotted = failed_names(log)
        for nodeid in nodeids:
            path = file_of_path(nodeid.split("::", 1)[0], files, index)
            if path is None:
                unmatched += 1
            else:
                found[path].add(nodeid)
        for name in dotted:
            path = file_of_dotted(name, files, index)
            if path is None:
                unmatched += 1
            else:
                found[path].add(name)
    return {path: sorted(names) for path, names in sorted(found.items())}, unmatched


def is_test_file(path: str) -> bool:
    """Whether pytest and unittest collect tests from a file of this name by default, as
    `select` counts test files."""
    pure = PurePosixPath(path)
    if pure.suffix != ".py":
        return False
    if any(
        fnmatchcase(part, pattern)
        for part in pure.parts[:-1]
        for pattern in UNCOLLECTED_DIRECTORIES
    ):
        return False
    name = pure.name
    return name.startswith("test_") or name.endswith("_test.py") or name in COLLECTED_NAMES


def documentation_profile() -> str:
    patterns = ", ".join(json.dumps(pattern) for pattern in DOCUMENTATION_IGNORE)
    return f"selection:\n  ignore: [{patterns}]\n"


# ---------------------------------------------------------------------------------------------
# GitHub


Opener = Callable[[urllib.request.Request, float], Any]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Hands a redirect back as an error, so that the token never follows it to another host."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _open(request: urllib.request.Request, timeout: float) -> Any:
    """Open a request built for API_ROOT or a LOG_HOSTS URL, following no redirect."""
    return urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout)


def log_host_allowed(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    return parts.scheme == "https" and any(host.endswith(suffix) for suffix in LOG_HOSTS)


class GitHub:
    """The few GitHub API calls this needs, with the token sent to the API and nowhere else."""

    def __init__(self, token: str, open_api: Opener = _open, open_log: Opener = _open) -> None:
        self._token = token
        self._open_api = open_api
        self._open_log = open_log

    def _request(self, url: str) -> urllib.request.Request:
        if not url.startswith(API_ROOT):
            raise ValueError(f"refusing to request a non-GitHub URL: {url}")
        return urllib.request.Request(  # noqa: S310 - API_ROOT, checked above
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "User-Agent": "semantic-diff-weaver-shadow-select",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def get(self, path: str, params: Mapping[str, str | int] | None = None) -> Any:
        url = API_ROOT + path.lstrip("/")
        if params:
            url += "?" + urllib.parse.urlencode(params)
        try:
            with self._open_api(self._request(url), 60) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            # A moved repository answers with its new address, inside the API, once.
            location = error.headers.get("Location", "") if error.code in (301, 307, 308) else ""
            if not location.startswith(API_ROOT):
                raise
        with self._open_api(self._request(location), 60) as response:
            return json.loads(response.read().decode("utf-8"))

    def log(self, repository: str, job_id: int) -> tuple[str | None, str | None, bool]:
        """A job's log, cut at MAX_LOG_BYTES, or None and why; and whether it was cut."""
        url = f"{API_ROOT}repos/{repository}/actions/jobs/{job_id}/logs"
        try:
            with self._open_api(self._request(url), 60) as response:
                body = response.read(MAX_LOG_BYTES + 1)
        except urllib.error.HTTPError as error:
            if error.code not in (301, 302, 303, 307, 308):
                return None, f"HTTP {error.code}", False
            location = error.headers.get("Location", "")
            if not log_host_allowed(location):
                return None, "redirected to an unexpected host", False
            request = urllib.request.Request(  # noqa: S310 - LOG_HOSTS, checked above
                location, headers={"User-Agent": "semantic-diff-weaver-shadow-select"}
            )
            try:
                with self._open_log(request, 120) as response:
                    body = response.read(MAX_LOG_BYTES + 1)
            except urllib.error.HTTPError as inner:
                return None, f"HTTP {inner.code} from the log host", False
            except OSError as inner:
                return None, f"log host unreachable: {type(inner).__name__}", False
        except OSError as error:
            return None, f"API unreachable: {type(error).__name__}", False
        cut = len(body) > MAX_LOG_BYTES
        return body[:MAX_LOG_BYTES].decode("utf-8", errors="replace"), None, cut

    def pages(
        self, path: str, key: str, params: Mapping[str, str | int]
    ) -> Iterable[dict[str, Any]]:
        for page in range(1, MAX_PAGES + 1):
            items = self.get(path, {**params, "per_page": PAGE, "page": page}).get(key) or []
            yield from items
            if len(items) < PAGE:
                return


# ---------------------------------------------------------------------------------------------
# Git


def git_executable() -> str:
    found = shutil.which("git")
    if found is None:
        raise SystemExit("shadow_select: Git is unavailable")
    return found


def git(repo: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [git_executable(), "-C", str(repo), *arguments],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=check,
        shell=False,
        timeout=600,
    )


def ensure_clone(repository: str, scratch: Path) -> Path:
    clone = scratch / repository.replace("/", "__")
    if (clone / ".git").is_dir():
        git(clone, "fetch", "--quiet", "--prune", "origin")
    else:
        subprocess.run(
            [
                git_executable(),
                "clone",
                "--quiet",
                f"https://github.com/{repository}.git",
                str(clone),
            ],
            capture_output=True,
            encoding="utf-8",
            check=True,
            shell=False,
            timeout=1800,
        )
    return clone


def have_commit(clone: Path, sha: str) -> bool:
    if git(clone, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode == 0:
        return True
    git(clone, "fetch", "--quiet", "origin", sha, check=False)
    return git(clone, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode == 0


def base_of(clone: Path, base_ref: str, head: str, started: str) -> str | None:
    """The pull request's merge base with its base branch as that branch stood when the run began.

    The branch as it stands now may hold the pull request itself, merged, which would leave the
    change empty.
    """
    if not BRANCH_RE.fullmatch(base_ref) or ".." in base_ref:
        return None
    tip = git(
        clone,
        "rev-list",
        "-1",
        "--first-parent",
        f"--before={started}",
        f"refs/remotes/origin/{base_ref}",
        check=False,
    ).stdout.strip()
    if not SHA_RE.fullmatch(tip):
        return None
    found = git(clone, "merge-base", tip, head, check=False).stdout.strip()
    return found if SHA_RE.fullmatch(found) and found != head else None


def head_files(clone: Path, head: str) -> frozenset[str]:
    listing = git(clone, "ls-tree", "-r", "-z", "--name-only", head).stdout
    return frozenset(name for name in listing.split("\0") if name)


def tool_commit() -> str:
    found = git(REPOSITORY_ROOT, "rev-parse", "HEAD", check=False).stdout.strip()
    dirty = git(REPOSITORY_ROOT, "status", "--porcelain", "--untracked-files=no", check=False)
    return f"{found}{'+dirty' if dirty.stdout.strip() else ''}"


# ---------------------------------------------------------------------------------------------
# select


def select(clone: Path, base: str, head: str, scratch: Path, profile: Path | None) -> Selection:
    """What this checkout's `select` chooses for a change, under a profile or the defaults."""
    command = [
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
        "--format",
        "json",
    ]
    if profile is not None:
        command += ["--risk-profile", str(profile)]
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS": str(scratch)},
            timeout=SELECT_TIMEOUT_SECONDS,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return Selection(error=f"select ran past {SELECT_TIMEOUT_SECONDS} s")
    seconds = round(time.perf_counter() - started, 3)
    if completed.returncode not in (0, RUN_ALL_EXIT):
        return Selection(error=completed.stderr.strip()[-500:], seconds=seconds)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return Selection(error="select printed no JSON", seconds=seconds)
    return Selection(
        run_all=bool(payload.get("run_all")),
        tests=[item["path"] for item in payload.get("tests", [])],
        reasons=[str(reason) for reason in payload.get("reasons", [])],
        seconds=seconds,
    )


# ---------------------------------------------------------------------------------------------
# run


def recorded_runs(out: Path) -> set[tuple[str, int]]:
    if not out.exists():
        return set()
    seen = set()
    for line in out.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            seen.add((item["repository"], int(item["run_id"])))
    return seen


def head_label(run: Mapping[str, Any]) -> str:
    owner = ((run.get("head_repository") or {}).get("owner") or {}).get("login", "")
    return f"{owner}:{run.get('head_branch') or ''}"


def pull_request_of(
    api: GitHub, repository: str, run: Mapping[str, Any], default: str
) -> tuple[int | None, str]:
    """The pull request a run tested, and its base branch; a fork's run does not say."""
    for item in run.get("pull_requests") or []:
        return item.get("number"), ((item.get("base") or {}).get("ref") or default)
    try:
        found = api.get(
            f"repos/{repository}/pulls",
            {"state": "all", "head": head_label(run), "per_page": 10},
        )
    except (urllib.error.URLError, ValueError):
        return None, default
    for item in found or []:
        if (item.get("head") or {}).get("sha") == run.get("head_sha"):
            return item.get("number"), (item.get("base") or {}).get("ref") or default
    if found:
        return found[0].get("number"), (found[0].get("base") or {}).get("ref") or default
    return None, default


def failed_jobs(api: GitHub, repository: str, run_id: int) -> list[dict[str, Any]]:
    jobs = api.pages(f"repos/{repository}/actions/runs/{run_id}/jobs", "jobs", {"filter": "latest"})
    return [job for job in jobs if job.get("conclusion") == "failure"][:MAX_FAILED_JOBS]


def read_run(
    api: GitHub, clone: Path, repository: str, run: Mapping[str, Any], kind: str
) -> Record:
    """A red run's record up to its failures: the head files its failed tests were in."""
    head = str(run.get("head_sha") or "")
    record = Record(
        repository=repository,
        kind=kind,
        run_id=int(run["id"]),
        run_url=str(run.get("html_url") or ""),
        workflow=str(run.get("name") or ""),
        created_at=str(run.get("created_at") or ""),
        head=head,
        head_label=head_label(run),
        outcome="measured",
        measured_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    if not SHA_RE.fullmatch(head) or not have_commit(clone, head):
        record.outcome = "head unavailable"
        return record
    files = head_files(clone, head)
    record.test_files = sum(is_test_file(path) for path in files)
    try:
        jobs = failed_jobs(api, repository, record.run_id)
    except OSError:
        record.outcome = "logs unavailable"
        return record
    if not jobs:
        # A workflow that failed before starting a job, as one whose file does not parse.
        record.outcome = "no failed job"
        return record
    logs = []
    for job in jobs:
        text, problem, cut = api.log(repository, int(job["id"]))
        record.failed_jobs.append(
            {
                "id": job["id"],
                "name": job.get("name"),
                "failed_steps": [
                    step.get("name")
                    for step in job.get("steps") or []
                    if step.get("conclusion") == "failure"
                ],
                "log": problem or ("cut" if cut else "read"),
            }
        )
        if text is not None:
            logs.append(text)
    if not logs:
        record.outcome = "logs unavailable"
        return record
    record.failures, record.unmatched = failed_files(logs, files)
    if not record.failures:
        record.outcome = "no failed test file"
    return record


def measure(arguments: argparse.Namespace) -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise SystemExit("shadow_select: set GITHUB_TOKEN; job logs need one")
    repository = arguments.repo
    scratch = arguments.scratch.resolve()
    out = arguments.out.resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    api = GitHub(token)
    profile = scratch / "profiles" / "documentation.yaml"
    profile.parent.mkdir(exist_ok=True)
    profile.write_text(documentation_profile(), encoding="utf-8")
    profile_sha256 = hashlib.sha256(profile.read_bytes()).hexdigest()
    about = api.get(f"repos/{repository}")
    # A moved repository is recorded, cloned and asked for under the name it has now.
    repository = str(about.get("full_name") or repository)
    if not REPOSITORY_RE.fullmatch(repository) or ".." in repository:
        raise SystemExit(f"shadow_select: GitHub named the repository {repository!r}")
    default = str(about.get("default_branch") or "main")
    clone = ensure_clone(repository, scratch)
    seen = recorded_runs(out)
    tool = tool_commit()
    taken: list[Record] = []
    bases: set[str] = set()
    since = (datetime.now(UTC) - timedelta(days=arguments.log_days)).date()
    runs = api.pages(
        f"repos/{repository}/actions/runs",
        "workflow_runs",
        {"event": "pull_request", "status": "failure", "created": f">={since}"},
    )
    for run in runs:
        if len(taken) >= arguments.limit:
            break
        if (repository, int(run["id"])) in seen:
            continue
        if arguments.workflow and run.get("name") != arguments.workflow:
            continue
        record = read_run(api, clone, repository, run, "pull_request")
        record.pull_request, base_ref = pull_request_of(api, repository, run, default)
        record.base_ref = base_ref
        record.tool, record.profile_sha256 = tool, profile_sha256
        if record.outcome == "measured":
            record.base = base_of(clone, base_ref, record.head, record.created_at)
            if record.base is None:
                record.outcome = "no merge base"
            else:
                for name in PROFILES:
                    chosen = select(
                        clone,
                        record.base,
                        record.head,
                        scratch,
                        profile if name == "documentation" else None,
                    )
                    record.selections[name] = asdict(chosen)
                if any(item["error"] for item in record.selections.values()):
                    record.outcome = "select error"
        bases.add(base_ref)
        taken.append(record)
        append(out, record)
        print(describe(record), flush=True)
    if taken and not arguments.no_base_runs:
        times = sorted(parse_time(item.created_at) for item in taken)
        window = f"{(times[0] - SHARED_WINDOW).date()}..{(times[-1] + SHARED_WINDOW).date()}"
        for base_ref in sorted(bases):
            if not BRANCH_RE.fullmatch(base_ref):
                continue
            pushes = api.pages(
                f"repos/{repository}/actions/runs",
                "workflow_runs",
                {"event": "push", "status": "failure", "branch": base_ref, "created": window},
            )
            for run in pushes:
                if (repository, int(run["id"])) in seen or (
                    arguments.workflow and run.get("name") != arguments.workflow
                ):
                    continue
                record = read_run(api, clone, repository, run, "base")
                record.base_ref, record.tool = base_ref, tool
                append(out, record)
                print(describe(record), flush=True)
    return 0


def append(out: Path, record: Record) -> None:
    with out.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(record), sort_keys=True) + "\n")


def describe(record: Record) -> str:
    head = f"{record.repository} {record.kind} run {record.run_id}"
    if record.outcome != "measured":
        return f"{head}: {record.outcome}"
    if record.kind == "base":
        return f"{head}: {len(record.failures)} failed file(s)"
    parts = []
    for name, chosen in record.selections.items():
        if chosen["run_all"]:
            parts.append(f"{name} whole suite")
        else:
            left = [path for path in record.failures if path not in set(chosen["tests"])]
            parts.append(
                f"{name} {len(chosen['tests'])} of {record.test_files}, {len(left)} left out"
            )
    return f"{head}: {len(record.failures)} failed file(s); " + "; ".join(parts)


def parse_time(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------------------------
# summary


def chose(record: Mapping[str, Any], profile: str, path: str) -> bool:
    chosen = record.get("selections", {}).get(profile) or {}
    return bool(chosen.get("run_all")) or path in set(chosen.get("tests") or [])


def shared(
    record: Mapping[str, Any], path: str, profile: str, records: Sequence[Mapping[str, Any]]
) -> bool:
    """Whether the file also failed, near in time, where no change `select` connected to it ran:
    on the base branch, or in another pull request whose selection left it out too."""
    when = parse_time(record["created_at"])
    for other in records:
        if other is record or path not in (other.get("failures") or {}):
            continue
        if abs(parse_time(other["created_at"]) - when) > SHARED_WINDOW:
            continue
        if other["kind"] == "base":
            return True
        if (
            other["head_label"] != record["head_label"]
            and other["outcome"] == "measured"
            and not chose(other, profile, path)
        ):
            return True
    return False


def judge(
    record: Mapping[str, Any], profile: str, records: Sequence[Mapping[str, Any]]
) -> dict[str, str]:
    """For each failed file of a measured pull request's run, how `select` stood to it."""
    verdicts = {}
    for path in record.get("failures") or {}:
        if chose(record, profile, path):
            verdicts[path] = "selected"
        elif not is_test_file(path):
            verdicts[path] = "not a test file by name"
        elif shared(record, path, profile, records):
            verdicts[path] = "shared"
        else:
            verdicts[path] = "unexplained"
    return verdicts


def load(paths: Sequence[Path]) -> list[dict[str, Any]]:
    unique: dict[tuple[str, int], dict[str, Any]] = {}
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                unique[(item["repository"], int(item["run_id"]))] = item
    return list(unique.values())


FILE_VERDICTS = ("selected", "shared", "unexplained", "not a test file by name")
RUN_VERDICTS = ("caught", "explained", "missed", "escaped")


def run_verdict(verdicts: Mapping[str, str]) -> str:
    """How a run's selection stood to its failed files, as `mutate_select.py` judges a mutant.

    `caught`: every failed file was selected. `explained`: those left out are shared. `missed`: a
    file left out is not, though a selected file failed too, so running the selection alone would
    still have failed. `escaped`: no selected file failed, so the selection alone would have passed.
    """
    kinds = set(verdicts.values())
    if kinds <= {"selected"}:
        return "caught"
    if not kinds & {"unexplained", "not a test file by name"}:
        return "explained"
    return "missed" if "selected" in kinds else "escaped"


@dataclass
class Tally:
    """One configuration's counts over a set of measured runs."""

    runs: int = 0
    whole: int = 0
    fractions: list[float] = field(default_factory=list)
    files: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    verdicts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    listed: list[tuple[str, str, Mapping[str, Any]]] = field(default_factory=list)

    def add(self, other: Tally) -> None:
        self.runs += other.runs
        self.whole += other.whole
        self.fractions += other.fractions
        for key, count in other.files.items():
            self.files[key] += count
        for key, count in other.verdicts.items():
            self.verdicts[key] += count

    def lines(self, profile: str) -> list[str]:
        mean = f"{sum(self.fractions) / len(self.fractions):.0%}" if self.fractions else "-"
        lines = [
            f"  {profile}: the whole suite for {self.whole} of {self.runs}, otherwise {mean} of "
            "the test files on average",
            "    runs: " + ", ".join(f"{self.verdicts[key]} {key}" for key in RUN_VERDICTS),
            "    failed files: " + ", ".join(f"{self.files[key]} {key}" for key in FILE_VERDICTS),
        ]
        for verdict, path, item in sorted(self.listed, key=lambda entry: entry[0], reverse=True):
            number = f" PR #{item['pull_request']}" if item.get("pull_request") else ""
            lines.append(f"    {verdict}: {path} in {item['run_url']}{number}")
        return lines


def tally(
    measured: Sequence[Mapping[str, Any]], records: Sequence[Mapping[str, Any]], profile: str
) -> Tally:
    counts = Tally(runs=len(measured))
    for item in measured:
        chosen = item["selections"].get(profile) or {}
        if chosen.get("run_all"):
            counts.whole += 1
        elif item.get("test_files"):
            counts.fractions.append(len(chosen.get("tests") or []) / item["test_files"])
        verdicts = judge(item, profile, records)
        counts.verdicts[run_verdict(verdicts)] += 1
        for path, verdict in verdicts.items():
            counts.files[verdict] += 1
            if verdict != "selected":
                counts.listed.append((verdict, path, item))
    return counts


def summarize(paths: Sequence[Path]) -> str:
    records = load(paths)
    lines: list[str] = []
    totals = {profile: Tally() for profile in PROFILES}
    unmeasured: dict[str, int] = defaultdict(int)
    repositories = sorted({item["repository"] for item in records})
    counted = [0, 0, 0]
    for repository in repositories:
        mine = [item for item in records if item["repository"] == repository]
        pulls = [item for item in mine if item["kind"] == "pull_request"]
        measured = [item for item in pulls if item["outcome"] == "measured"]
        bases = [item for item in mine if item["kind"] == "base"]
        counted = [counted[0] + len(pulls), counted[1] + len(measured), counted[2] + len(bases)]
        lines.append(
            f"{repository}: {len(pulls)} red pull-request runs, {len(measured)} with a failed "
            f"test file of the head; {len(bases)} red base pushes"
        )
        for profile in PROFILES:
            counts = tally(measured, mine, profile)
            totals[profile].add(counts)
            lines += counts.lines(profile)
        others: dict[str, int] = defaultdict(int)
        for item in pulls:
            if item["outcome"] != "measured":
                others[item["outcome"]] += 1
                unmeasured[item["outcome"]] += 1
        if others:
            lines.append(
                "  not measured: "
                + ", ".join(f"{count} {outcome}" for outcome, count in sorted(others.items()))
            )
    if len(repositories) > 1:
        lines.append(
            f"all {len(repositories)} repositories: {counted[0]} red pull-request runs, "
            f"{counted[1]} with a failed test file of the head; {counted[2]} red base pushes"
        )
        for profile in PROFILES:
            lines += totals[profile].lines(profile)
        if unmeasured:
            lines.append(
                "  not measured: "
                + ", ".join(f"{count} {outcome}" for outcome, count in sorted(unmeasured.items()))
            )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="measure red pull-request runs, appending to JSONL")
    run.add_argument("--repo", required=True, help="public GitHub repository, OWNER/NAME")
    run.add_argument("--scratch", required=True, type=Path, help="directory for the clone")
    run.add_argument("--out", required=True, type=Path, help="JSONL file runs are appended to")
    run.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="pull-request runs to read")
    run.add_argument("--workflow", help="only runs of the workflow with this name")
    run.add_argument(
        "--no-base-runs", action="store_true", help="skip the base branch's red pushes"
    )
    run.add_argument(
        "--log-days",
        type=int,
        default=LOG_DAYS,
        help="read only runs this recent, whose logs GitHub still keeps",
    )
    report = commands.add_parser("summary", help="summarize JSONL files of red runs")
    report.add_argument("paths", nargs="+", type=Path)
    arguments = parser.parse_args(argv)
    if arguments.command == "summary":
        print(summarize(arguments.paths))
        return 0
    if not REPOSITORY_RE.fullmatch(arguments.repo) or ".." in arguments.repo:
        parser.error("--repo must be OWNER/NAME")
    if arguments.limit < 1:
        parser.error("--limit must be at least 1")
    return measure(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
