"""The shadow measurement of `select` on real red runs: what it reads from a log, and how it judges.

`scripts/shadow_select.py` reads public projects' CI logs, which are untrusted text, and reports a
failed test file `select` left out as a miss only when nothing else explains it. These pin both
halves offline: which lines name a failed test and which file each names, how the token is kept
from the host a log redirects to, and when a left-out failure counts as shared.
"""

from __future__ import annotations

import email.message
import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
import yaml

import scripts.shadow_select as shadow
from scripts.shadow_select import (
    DOCUMENTATION_IGNORE,
    GitHub,
    base_of,
    basename_index,
    clean_line,
    documentation_profile,
    failed_files,
    failed_names,
    file_of_dotted,
    file_of_path,
    head_label,
    is_test_file,
    judge,
    log_host_allowed,
    main,
    pull_request_of,
    read_run,
    run_verdict,
    summarize,
)
from tests.conftest import git

FILES = frozenset(
    {
        "src/pkg/__init__.py",
        "src/pkg/core.py",
        "tests/test_core.py",
        "tests/unit/test_api.py",
        "tests/integration/test_api.py",
        "tests/streams/test_text.py",
    }
)
INDEX = basename_index(FILES)


def test_a_line_loses_the_runners_timestamp_and_its_colors() -> None:
    line = "2026-11-08T02:56:02.8225824Z \x1b[31mFAILED\x1b[0m tests/test_core.py::test_a - E"

    assert clean_line(line) == "FAILED tests/test_core.py::test_a - E"


def test_pytest_and_unittest_failures_are_read_and_nothing_else_is() -> None:
    log = "\n".join(
        [
            "2026-11-08T02:56:02.1Z tests/test_core.py::test_verbose FAILED          [ 10%]",
            "2026-11-08T02:56:02.1Z tests/test_core.py::test_fine PASSED             [ 11%]",
            "2026-11-08T02:56:02.2Z [gw1] [ 20%] FAILED tests/test_core.py::test_worker",
            "2026-11-08T02:56:02.3Z FAILED tests/test_core.py::test_a[x y] - AssertionError: no",
            "2026-11-08T02:56:02.4Z ERROR tests/streams/test_text.py",
            "2026-11-08T02:56:02.5Z FAIL: test_b (tests.test_core.Case.test_b)",
            "2026-11-08T02:56:02.6Z ERROR: test_c (tests.unit.test_api.Case)",
            "2026-11-08T02:56:02.7Z some FAILED text in the middle of a line",
        ]
    )

    nodeids, dotted = failed_names(log)

    assert nodeids == {
        "tests/test_core.py::test_verbose",
        "tests/test_core.py::test_worker",
        "tests/test_core.py::test_a[x y]",
        "tests/streams/test_text.py",
    }
    assert dotted == {"tests.test_core.Case.test_b", "tests.unit.test_api.Case"}


def test_a_file_is_read_from_the_progress_when_the_summary_leaves_failures_out() -> None:
    # httpx's `-r` lists only what was skipped, so its summary names no failed test.
    log = "\n".join(
        [
            "tests/test_core.py ......F...                                        [ 96%]",
            "tests/unit/test_api.py ..............................................",
            "......E..                                                            [ 98%]",
            "tests/streams/test_text.py ....                                      [100%]",
            "..F",
            "=========================== short test summary info ============================",
            "SKIPPED [1] tests/test_core.py:273: needs Python 3.11",
            "................F",
        ]
    )

    nodeids, _dotted = failed_names(log)

    # The wrapped letters count toward the file above them, until any other line comes.
    assert nodeids == {"tests/test_core.py", "tests/unit/test_api.py", "tests/streams/test_text.py"}


def test_a_path_is_kept_only_when_it_names_one_file_of_the_head() -> None:
    assert file_of_path("tests/test_core.py", FILES, INDEX) == "tests/test_core.py"
    assert file_of_path("./tests/test_core.py", FILES, INDEX) == "tests/test_core.py"
    assert file_of_path("tests\\test_core.py", FILES, INDEX) == "tests/test_core.py"
    # Started below the root, pytest prints paths relative to its own directory.
    assert file_of_path("streams/test_text.py", FILES, INDEX) == "tests/streams/test_text.py"
    # Two files end the same way, so the path names neither.
    assert file_of_path("test_api.py", FILES, INDEX) is None
    # A test that runs pytest prints the paths of its own temporary files.
    assert file_of_path("test_hypothesis_multiple_backends.py", FILES, INDEX) is None
    assert file_of_path("/tmp/pytest-0/test_core.py", FILES, INDEX) is None


def test_a_unittest_name_finds_the_module_it_was_defined_in() -> None:
    assert file_of_dotted("tests.test_core.Case.test_b", FILES, INDEX) == "tests/test_core.py"
    assert file_of_dotted("tests.unit.test_api.Case", FILES, INDEX) == "tests/unit/test_api.py"
    assert file_of_dotted("elsewhere.test_x.Case", FILES, INDEX) is None


def test_failures_are_grouped_by_file_and_the_names_matching_none_are_counted() -> None:
    logs = [
        "FAILED tests/test_core.py::test_a - E\nERROR test_temporary.py - TypeError",
        "FAILED tests/test_core.py::test_b\nFAILED (failures=2)\n",
    ]

    found, unmatched = failed_files(logs, FILES)

    assert found == {
        "tests/test_core.py": ["tests/test_core.py::test_a", "tests/test_core.py::test_b"]
    }
    assert unmatched == 2


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/test_core.py", True),
        ("tests/core_test.py", True),
        ("app/tests.py", True),
        ("tests/conftest.py", False),
        ("tests/test_data.json", False),
        ("build/lib/tests/test_core.py", False),
        (".tox/py311/test_core.py", False),
        ("tests/helpers.py", False),
    ],
)
def test_test_files_are_counted_by_the_names_select_reads(path: str, expected: bool) -> None:
    assert is_test_file(path) is expected


def test_the_documentation_profile_sets_exactly_the_fixed_ignore_list() -> None:
    assert yaml.safe_load(documentation_profile()) == {
        "selection": {"ignore": list(DOCUMENTATION_IGNORE)}
    }


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://productionresultssa13.blob.core.windows.net/actions-results/x?sig=1", True),
        ("https://pipelines.actions.githubusercontent.com/logs/x", True),
        ("http://productionresultssa13.blob.core.windows.net/x", False),
        ("https://blob.core.windows.net.attacker.example/x", False),
        ("https://example.com/x", False),
        ("", False),
    ],
)
def test_a_log_redirect_is_followed_only_to_githubs_log_hosts(url: str, allowed: bool) -> None:
    assert log_host_allowed(url) is allowed


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = io.BytesIO(body)

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)


def _redirect(location: str) -> urllib.error.HTTPError:
    headers = email.message.Message()
    headers["Location"] = location
    return urllib.error.HTTPError("https://api.github.com/x", 302, "Found", headers, None)


def test_a_log_is_fetched_from_its_redirect_without_the_token() -> None:
    seen: list[urllib.request.Request] = []

    def api(request: urllib.request.Request, timeout: float) -> Any:
        seen.append(request)
        raise _redirect("https://results.blob.core.windows.net/log?sig=s")

    def host(request: urllib.request.Request, timeout: float) -> Any:
        seen.append(request)
        return _Response(b"FAILED tests/test_core.py::test_a\n")

    text, problem, cut = GitHub("secret-token", api, host).log("o/r", 7)

    assert (text, problem, cut) == ("FAILED tests/test_core.py::test_a\n", None, False)
    assert seen[0].get_header("Authorization") == "Bearer secret-token"
    assert seen[1].full_url == "https://results.blob.core.windows.net/log?sig=s"
    assert seen[1].get_header("Authorization") is None


def test_a_log_redirected_elsewhere_or_gone_is_not_read() -> None:
    def elsewhere(request: urllib.request.Request, timeout: float) -> Any:
        raise _redirect("https://example.com/log")

    def gone(request: urllib.request.Request, timeout: float) -> Any:
        raise urllib.error.HTTPError(request.full_url, 410, "Gone", email.message.Message(), None)

    def unused(request: urllib.request.Request, timeout: float) -> Any:
        raise AssertionError("the log host must not be asked")

    assert GitHub("t", elsewhere, unused).log("o/r", 7) == (
        None,
        "redirected to an unexpected host",
        False,
    )
    assert GitHub("t", gone, unused).log("o/r", 7) == (None, "HTTP 410", False)


def test_a_log_past_the_limit_is_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shadow, "MAX_LOG_BYTES", 4)

    def api(request: urllib.request.Request, timeout: float) -> Any:
        return _Response(b"0123456789")

    assert GitHub("t", api, api).log("o/r", 7) == ("0123", None, True)


def _moved(location: str) -> urllib.error.HTTPError:
    headers = email.message.Message()
    headers["Location"] = location
    return urllib.error.HTTPError("https://api.github.com/x", 301, "Moved", headers, None)


def test_a_moved_repository_is_followed_once_inside_the_api() -> None:
    asked: list[str] = []

    def api(request: urllib.request.Request, timeout: float) -> Any:
        asked.append(request.full_url)
        if len(asked) == 1:
            raise _moved("https://api.github.com/repositories/42")
        return _Response(b'{"full_name": "new/name"}')

    assert GitHub("t", api, api).get("repos/old/name") == {"full_name": "new/name"}
    assert asked == [
        "https://api.github.com/repos/old/name",
        "https://api.github.com/repositories/42",
    ]


def test_a_redirect_out_of_the_api_is_not_followed() -> None:
    def api(request: urllib.request.Request, timeout: float) -> Any:
        raise _moved("https://example.com/repositories/42")

    with pytest.raises(urllib.error.HTTPError):
        GitHub("t", api, api).get("repos/old/name")


def test_a_request_outside_the_api_is_refused() -> None:
    with pytest.raises(ValueError, match="non-GitHub URL"):
        GitHub("t")._request("https://example.com/x")


class _Api:
    def __init__(self, pulls: list[dict[str, Any]]) -> None:
        self.pulls = pulls
        self.asked: list[tuple[str, Any]] = []

    def get(self, path: str, params: Any = None) -> Any:
        self.asked.append((path, params))
        return self.pulls


FORK_RUN = {
    "head_sha": "a" * 40,
    "head_branch": "fix/thing",
    "head_repository": {"owner": {"login": "someone"}},
    "pull_requests": [],
}


def test_a_forks_pull_request_is_found_by_its_head_label() -> None:
    api = _Api(
        [
            {"number": 9, "head": {"sha": "b" * 40}, "base": {"ref": "main"}},
            {"number": 12, "head": {"sha": "a" * 40}, "base": {"ref": "release"}},
        ]
    )

    assert head_label(FORK_RUN) == "someone:fix/thing"
    assert pull_request_of(api, "o/r", FORK_RUN, "main") == (12, "release")
    assert api.asked[0][1]["head"] == "someone:fix/thing"


def test_a_same_repository_run_names_its_pull_request_itself() -> None:
    run = {**FORK_RUN, "pull_requests": [{"number": 5, "base": {"ref": "dev"}}]}
    api = _Api([])

    assert pull_request_of(api, "o/r", run, "main") == (5, "dev")
    assert api.asked == []


def _commit(repo: Path, monkeypatch: pytest.MonkeyPatch, name: str, when: str) -> str:
    monkeypatch.setenv("GIT_AUTHOR_DATE", when)
    monkeypatch.setenv("GIT_COMMITTER_DATE", when)
    (repo / name).write_text(name, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "--quiet", "--message", name)
    return git(repo, "rev-parse", "HEAD")


def test_the_base_is_the_branch_as_it_stood_when_the_run_began(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet", "--initial-branch=main")
    first = _commit(repo, monkeypatch, "a.txt", "2026-11-01T10:00:00Z")
    git(repo, "switch", "--quiet", "--create", "pr")
    head = _commit(repo, monkeypatch, "b.txt", "2026-11-02T10:00:00Z")
    git(repo, "switch", "--quiet", "main")
    _commit(repo, monkeypatch, "c.txt", "2026-11-03T10:00:00Z")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-11-06T10:00:00Z")
    git(repo, "merge", "--quiet", "--no-ff", "--message", "merge pr", "pr")
    git(repo, "update-ref", "refs/remotes/origin/main", "main")

    # Before the merge, the change is the pull request's own commit.
    assert base_of(repo, "main", head, "2026-11-04T00:00:00Z") == first
    # After it, the branch holds the head itself, which leaves no change to select for.
    assert base_of(repo, "main", head, "2026-11-07T00:00:00Z") is None
    assert base_of(repo, "-main", head, "2026-11-04T00:00:00Z") is None
    assert base_of(repo, "main..x", head, "2026-11-04T00:00:00Z") is None


class _RunApi:
    """The two calls `read_run` makes: a run's jobs, and each job's log."""

    def __init__(
        self, jobs: list[dict[str, Any]], logs: dict[int, tuple[str | None, str | None, bool]]
    ) -> None:
        self.jobs = jobs
        self.logs = logs

    def pages(self, path: str, key: str, params: Any) -> Any:
        return iter(self.jobs)

    def log(self, repository: str, job_id: int) -> tuple[str | None, str | None, bool]:
        return self.logs.get(job_id, (None, "HTTP 410", False))


FAILED_JOB = {
    "id": 5,
    "conclusion": "failure",
    "name": "py3.12",
    "steps": [{"name": "Test with pytest", "conclusion": "failure"}],
}


@pytest.fixture
def head_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_a.py").write_text("def test_x(): pass\n", encoding="utf-8")
    (repo / "a.py").write_text("X = 1\n", encoding="utf-8")
    git(repo, "init", "--quiet")
    git(repo, "add", "--all")
    git(repo, "commit", "--quiet", "--message", "head")
    return repo, git(repo, "rev-parse", "HEAD")


def _run(head: str) -> dict[str, Any]:
    return {
        "id": 1,
        "head_sha": head,
        "html_url": "https://github.com/o/r/actions/runs/1",
        "name": "tests",
        "created_at": "2026-11-08T10:00:00Z",
        "head_branch": "x",
        "head_repository": {"owner": {"login": "o"}},
    }


def test_a_run_reads_the_failed_files_of_its_head(head_repo: tuple[Path, str]) -> None:
    repo, head = head_repo
    api = _RunApi([FAILED_JOB], {5: ("FAILED tests/test_a.py::test_x - E\n", None, False)})

    record = read_run(api, repo, "o/r", _run(head), "pull_request")  # type: ignore[arg-type]

    assert record.outcome == "measured"
    assert record.failures == {"tests/test_a.py": ["tests/test_a.py::test_x"]}
    assert record.test_files == 1
    assert record.failed_jobs == [
        {"id": 5, "name": "py3.12", "failed_steps": ["Test with pytest"], "log": "read"}
    ]


@pytest.mark.parametrize(
    ("jobs", "logs", "outcome"),
    [
        pytest.param([], {}, "no failed job", id="no-job"),
        pytest.param([FAILED_JOB], {}, "logs unavailable", id="log-gone"),
        pytest.param(
            [FAILED_JOB],
            {5: ("ERROR: lint failed\n", None, False)},
            "no failed test file",
            id="lint",
        ),
    ],
)
def test_a_run_without_a_failed_test_file_says_why(
    head_repo: tuple[Path, str],
    jobs: list[dict[str, Any]],
    logs: dict[int, tuple[str | None, str | None, bool]],
    outcome: str,
) -> None:
    repo, head = head_repo

    record = read_run(_RunApi(jobs, logs), repo, "o/r", _run(head), "pull_request")  # type: ignore[arg-type]

    assert record.outcome == outcome


def test_a_run_whose_head_is_gone_is_not_read(head_repo: tuple[Path, str]) -> None:
    repo, _head = head_repo
    api = _RunApi([FAILED_JOB], {})

    assert read_run(api, repo, "o/r", _run("f" * 40), "base").outcome == "head unavailable"  # type: ignore[arg-type]


def _pull(
    run_id: int,
    label: str,
    when: str,
    failures: list[str],
    documentation: list[str],
    *,
    run_all: bool = False,
) -> dict[str, Any]:
    return {
        "repository": "o/r",
        "kind": "pull_request",
        "run_id": run_id,
        "run_url": f"https://github.com/o/r/actions/runs/{run_id}",
        "created_at": when,
        "head_label": label,
        "outcome": "measured",
        "pull_request": run_id,
        "failures": {path: [f"{path}::test_x"] for path in failures},
        "test_files": 10,
        "selections": {
            "default": {"run_all": True, "tests": [], "reasons": ["changelog"]},
            "documentation": {"run_all": run_all, "tests": documentation, "reasons": []},
        },
    }


def _base(run_id: int, when: str, failures: list[str]) -> dict[str, Any]:
    return {
        "repository": "o/r",
        "kind": "base",
        "run_id": run_id,
        "run_url": f"https://github.com/o/r/actions/runs/{run_id}",
        "created_at": when,
        "head_label": "o:main",
        "outcome": "measured",
        "failures": {path: [f"{path}::test_x"] for path in failures},
        "selections": {},
    }


def test_a_failure_left_out_is_shared_when_the_base_failed_it_nearby() -> None:
    pull = _pull(1, "a:x", "2026-11-05T10:00:00Z", ["tests/test_a.py"], ["tests/test_b.py"])
    records = [pull, _base(2, "2026-11-06T10:00:00Z", ["tests/test_a.py"])]

    assert judge(pull, "default", records) == {"tests/test_a.py": "selected"}
    assert judge(pull, "documentation", records) == {"tests/test_a.py": "shared"}


def test_a_failure_far_from_any_other_is_unexplained() -> None:
    pull = _pull(1, "a:x", "2026-11-01T10:00:00Z", ["tests/test_a.py"], [])
    records = [pull, _base(2, "2026-11-05T10:00:01Z", ["tests/test_a.py"])]

    assert judge(pull, "documentation", records) == {"tests/test_a.py": "unexplained"}


def test_another_pull_request_counts_only_when_select_left_the_file_out_there_too() -> None:
    pull = _pull(1, "a:x", "2026-11-05T10:00:00Z", ["tests/test_a.py"], [])
    connected = _pull(2, "b:y", "2026-11-05T12:00:00Z", ["tests/test_a.py"], ["tests/test_a.py"])
    same_branch = _pull(3, "a:x", "2026-11-05T13:00:00Z", ["tests/test_a.py"], [])
    unconnected = _pull(4, "c:z", "2026-11-05T14:00:00Z", ["tests/test_a.py"], [])

    assert judge(pull, "documentation", [pull, connected, same_branch]) == {
        "tests/test_a.py": "unexplained"
    }
    assert judge(pull, "documentation", [pull, connected, unconnected]) == {
        "tests/test_a.py": "shared"
    }


def test_a_failed_file_select_cannot_name_is_kept_apart() -> None:
    pull = _pull(1, "a:x", "2026-11-05T10:00:00Z", ["tests/checks.py"], [])

    assert judge(pull, "documentation", [pull]) == {"tests/checks.py": "not a test file by name"}


def test_the_summary_counts_each_verdict_and_lists_what_select_left_out(tmp_path: Path) -> None:
    records = [
        _pull(1, "a:x", "2026-11-05T10:00:00Z", ["tests/test_a.py"], ["tests/test_a.py"]),
        _pull(2, "b:y", "2026-11-05T11:00:00Z", ["tests/test_b.py"], ["tests/test_c.py"]),
        _pull(3, "c:z", "2026-11-05T12:00:00Z", ["tests/test_d.py"], [], run_all=True),
        {**_pull(4, "d:w", "2026-11-05T13:00:00Z", [], []), "outcome": "no failed test file"},
    ]
    results = tmp_path / "results.jsonl"
    results.write_text(
        "".join(json.dumps(item) + "\n" for item in [*records, records[0]]), encoding="utf-8"
    )

    text = summarize([results])

    assert text.splitlines() == [
        "o/r: 4 red pull-request runs, 3 with a failed test file of the head; 0 red base pushes",
        "  default: the whole suite for 3 of 3, otherwise - of the test files on average",
        "    runs: 3 caught, 0 explained, 0 missed, 0 escaped",
        "    failed files: 3 selected, 0 shared, 0 unexplained, 0 not a test file by name",
        "  documentation: the whole suite for 1 of 3, otherwise 10% of the test files on average",
        "    runs: 2 caught, 0 explained, 0 missed, 1 escaped",
        "    failed files: 2 selected, 0 shared, 1 unexplained, 0 not a test file by name",
        "    unexplained: tests/test_b.py in https://github.com/o/r/actions/runs/2 PR #2",
        "  not measured: 1 no failed test file",
    ]


def test_the_summary_adds_up_every_repository_it_holds(tmp_path: Path) -> None:
    first = _pull(1, "a:x", "2026-11-05T10:00:00Z", ["tests/test_a.py"], ["tests/test_a.py"])
    second = {
        **_pull(2, "b:y", "2026-11-05T11:00:00Z", ["tests/test_b.py"], ["tests/test_c.py"]),
        "repository": "p/q",
    }
    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n", encoding="utf-8")

    lines = summarize([results]).splitlines()
    total = "all 2 repositories: 2 red pull-request runs, 2 with a failed test file of the head; "

    assert lines[lines.index(f"{total}0 red base pushes") :] == [
        f"{total}0 red base pushes",
        "  default: the whole suite for 2 of 2, otherwise - of the test files on average",
        "    runs: 2 caught, 0 explained, 0 missed, 0 escaped",
        "    failed files: 2 selected, 0 shared, 0 unexplained, 0 not a test file by name",
        "  documentation: the whole suite for 0 of 2, otherwise 10% of the test files on average",
        "    runs: 1 caught, 0 explained, 0 missed, 1 escaped",
        "    failed files: 1 selected, 0 shared, 1 unexplained, 0 not a test file by name",
    ]


@pytest.mark.parametrize(
    ("verdicts", "expected"),
    [
        ({"a": "selected"}, "caught"),
        ({"a": "selected", "b": "shared"}, "explained"),
        ({"a": "shared"}, "explained"),
        # The selection alone would still have failed, in `a`.
        ({"a": "selected", "b": "unexplained"}, "missed"),
        # The selection alone would have passed.
        ({"a": "unexplained"}, "escaped"),
        ({"a": "shared", "b": "unexplained"}, "escaped"),
        ({"a": "not a test file by name"}, "escaped"),
    ],
)
def test_a_run_is_judged_by_whether_its_selection_alone_would_still_fail(
    verdicts: dict[str, str], expected: str
) -> None:
    assert run_verdict(verdicts) == expected


def test_run_refuses_a_repository_that_is_not_owner_and_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["run", "--repo", "../x", "--scratch", "s", "--out", "o.jsonl"])

    assert raised.value.code == 2
    assert "--repo must be OWNER/NAME" in capsys.readouterr().err


def test_run_needs_a_token_for_the_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    with pytest.raises(SystemExit, match="GITHUB_TOKEN"):
        main(["run", "--repo", "o/r", "--scratch", str(tmp_path), "--out", str(tmp_path / "o")])
