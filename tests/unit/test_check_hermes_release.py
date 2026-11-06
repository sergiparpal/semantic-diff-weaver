"""The weekly check that CI's hermes-current job tests the newest Hermes release."""

from __future__ import annotations

import urllib.error
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts import check_hermes_release

ROOT = Path(__file__).parents[2]
UPSTREAM = "https://api.github.com/repos/NousResearch/hermes-agent/"
LATEST = f"{UPSTREAM}releases/latest"
ISSUES = "https://api.github.com/repos/owner/repo/issues"
COMMIT = "1" * 40
TAG_OBJECT = "2" * 40
TITLE = "Hermes v0.24.0 is out; CI tests v0.23.0"


def _workflow(tmp_path: Path, tag: str = "v0.23.0") -> Path:
    path = tmp_path / "ci.yml"
    path.write_text(
        "jobs:\n"
        "  hermes-current:\n"
        "    env:\n"
        f'      HERMES_TAG: "{tag}"\n'
        f'      HERMES_COMMIT: "{"a" * 40}"\n',
        encoding="utf-8",
    )
    return path


class _Api:
    """GitHub's API as the script sees it: an answer for each URL, and every call kept."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.created: Any = {"number": 7}

    def __call__(self, url: str, payload: dict[str, Any] | None) -> Any:
        self.calls.append((url, payload))
        if payload is not None:
            return self.created
        if url.split("?")[0] not in self.answers:
            raise AssertionError(f"unexpected request: {url}")
        return self.answers[url.split("?")[0]]

    @property
    def posted(self) -> list[dict[str, Any]]:
        return [payload for _, payload in self.calls if payload is not None]


def _api(latest: str = "v0.24.0", issues: list[dict[str, Any]] | None = None) -> _Api:
    return _Api(
        {
            LATEST: {"tag_name": latest, "published_at": "2026-11-12T10:00:00Z"},
            f"{UPSTREAM}git/ref/tags/{latest}": {"object": {"type": "tag", "sha": TAG_OBJECT}},
            f"{UPSTREAM}git/tags/{TAG_OBJECT}": {"object": {"type": "commit", "sha": COMMIT}},
            ISSUES: issues or [],
        }
    )


@pytest.fixture
def repository(monkeypatch: pytest.MonkeyPatch) -> None:
    """The variables Actions sets for a run, which the issue names."""
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")


def test_the_tag_read_is_the_one_the_workflow_sets() -> None:
    """A ci.yml reformatted past the pattern fails here, not silently on Mondays."""
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    environment = yaml.safe_load(text)["jobs"]["hermes-current"]["env"]

    assert check_hermes_release.pinned_release(text) == environment["HERMES_TAG"]


def test_ci_on_the_latest_release_passes(tmp_path: Path, capsys, repository) -> None:
    api = _api(latest="v0.23.0")

    assert check_hermes_release.main(["--open-issue"], api, _workflow(tmp_path)) == 0
    assert "CI tests v0.23.0, and the latest Hermes release is v0.23.0." in capsys.readouterr().out
    assert [url for url, _ in api.calls] == [LATEST]


def test_releases_compare_as_numbers(tmp_path: Path) -> None:
    """v0.10.0 is newer than v0.9.9, so a pin ahead of the latest release is no news."""
    assert check_hermes_release.main([], _api(latest="v0.9.9"), _workflow(tmp_path, "v0.10.0")) == 0
    assert check_hermes_release.main([], _api(latest="v0.10.0"), _workflow(tmp_path, "v0.9.9")) == 1


def test_a_newer_release_fails_the_check_without_an_issue(tmp_path: Path, capsys) -> None:
    api = _api()

    assert check_hermes_release.main([], api, _workflow(tmp_path)) == 1
    assert "Hermes v0.24.0 is out; CI's hermes-current job tests v0.23.0." in (
        capsys.readouterr().out
    )
    assert api.posted == []


def test_a_newer_release_opens_an_issue_saying_what_to_move(
    tmp_path: Path, capsys, repository
) -> None:
    api = _api()

    assert check_hermes_release.main(["--open-issue"], api, _workflow(tmp_path)) == 0
    assert "opened issue #7" in capsys.readouterr().out
    (issue,) = api.posted
    assert issue["title"] == TITLE
    body = issue["body"]
    assert (
        "NousResearch/hermes-agent published "
        "[v0.24.0](https://github.com/NousResearch/hermes-agent/releases/tag/v0.24.0) on "
        "2026-11-12. CI's `hermes-current` job still builds v0.23.0."
    ) in body
    assert "- `HERMES_TAG` to `v0.24.0`;" in body
    assert f"- `HERMES_COMMIT` to the commit the tag names, `{COMMIT}` when this issue" in body
    assert "(https://github.com/NousResearch/hermes-agent/blob/v0.24.0/scripts/install.sh)" in body
    assert "[run 123](https://github.com/owner/repo/actions/runs/123)." in body


@pytest.mark.parametrize("kind", ["issue", "pull request"])
def test_a_release_gets_one_issue(tmp_path: Path, capsys, repository, kind: str) -> None:
    """An open issue with the title is the notice already; a pull request with it is not one."""
    found: dict[str, Any] = {"number": 5, "title": TITLE}
    if kind == "pull request":
        found["pull_request"] = {"url": "https://api.github.com/repos/owner/repo/pulls/5"}
    api = _api(issues=[{"number": 4, "title": "Hermes v0.23.0 is out; CI tests v0.22.7"}, found])

    assert check_hermes_release.main(["--open-issue"], api, _workflow(tmp_path)) == 0
    if kind == "issue":
        assert "issue #5 is open already" in capsys.readouterr().out
        assert api.posted == []
    else:
        assert [issue["title"] for issue in api.posted] == [TITLE]


@pytest.mark.parametrize(
    "ref",
    [
        {"object": {"type": "commit", "sha": "not a commit"}},
        {"object": {"type": "tag", "sha": "not an object"}},
        {"message": "Not Found"},
    ],
)
def test_a_commit_the_api_does_not_give_is_left_out(
    tmp_path: Path, repository, ref: dict[str, Any]
) -> None:
    api = _api()
    api.answers[f"{UPSTREAM}git/ref/tags/v0.24.0"] = ref

    assert check_hermes_release.main(["--open-issue"], api, _workflow(tmp_path)) == 0
    (issue,) = api.posted
    assert "- `HERMES_COMMIT` to the commit the tag names;" in issue["body"]


def test_a_lightweight_tag_names_its_commit_itself() -> None:
    api = _Api({f"{UPSTREAM}git/ref/tags/v0.24.0": {"object": {"type": "commit", "sha": COMMIT}}})

    assert check_hermes_release.tag_commit(api, "v0.24.0") == COMMIT


def test_an_unreachable_tag_still_opens_the_issue(tmp_path: Path, repository) -> None:
    api = _api()

    def _api_without_refs(url: str, payload: dict[str, Any] | None) -> Any:
        if "/git/" in url:
            raise urllib.error.URLError("unreachable")
        return api(url, payload)

    assert check_hermes_release.main(["--open-issue"], _api_without_refs, _workflow(tmp_path)) == 0
    assert len(api.posted) == 1


def test_outside_a_run_the_issue_names_no_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    api = _api()

    assert check_hermes_release.main(["--open-issue"], api, _workflow(tmp_path)) == 0
    assert "Opened by" not in api.posted[0]["body"]


@pytest.mark.parametrize("repository_name", ["", "owner", "owner/repo/extra", "../owner/repo"])
def test_without_a_repository_to_open_it_in_no_issue_is_opened(
    tmp_path: Path, capsys, monkeypatch, repository_name: str
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", repository_name)
    api = _api()

    assert check_hermes_release.main(["--open-issue"], api, _workflow(tmp_path)) == 1
    assert "Could not open the issue: GITHUB_REPOSITORY names no repository" in (
        capsys.readouterr().err
    )
    assert api.posted == []


def test_an_issue_the_api_does_not_confirm_fails_the_run(
    tmp_path: Path, capsys, repository
) -> None:
    api = _api()
    api.created = {"message": "Resource not accessible by integration"}

    assert check_hermes_release.main(["--open-issue"], api, _workflow(tmp_path)) == 1
    assert "the API did not say which issue it opened" in capsys.readouterr().err


@pytest.mark.parametrize(
    "release",
    [{"tag_name": "nightly"}, {"tag_name": 23}, {}, ["v0.24.0"]],
    ids=["not-a-release", "not-text", "no-tag", "not-an-object"],
)
def test_a_latest_release_without_a_release_tag_cannot_be_compared(
    tmp_path: Path, capsys, release: Any
) -> None:
    api = _Api({LATEST: release})

    assert check_hermes_release.main(["--open-issue"], api, _workflow(tmp_path)) == 2
    assert "Could not compare the Hermes releases" in capsys.readouterr().err
    assert api.posted == []


def test_an_unreachable_api_cannot_be_compared(tmp_path: Path, capsys) -> None:
    def _offline(url: str, payload: dict[str, Any] | None) -> Any:
        raise urllib.error.URLError("offline")

    assert check_hermes_release.main([], _offline, _workflow(tmp_path)) == 2
    assert "offline" in capsys.readouterr().err


@pytest.mark.parametrize(
    "text",
    [
        "jobs: {}\n",
        '      HERMES_TAG: "v0.23.0"\n      HERMES_TAG: "v0.24.0"\n',
        '      HERMES_TAG: "latest"\n',
        'HERMES_TAG: "v0.23.0"\n',
    ],
    ids=["none", "twice", "not-a-release", "not-in-a-job"],
)
def test_a_workflow_without_one_release_tag_cannot_be_compared(tmp_path: Path, text: str) -> None:
    path = tmp_path / "ci.yml"
    path.write_text(text, encoding="utf-8")

    assert check_hermes_release.main([], _api(), path) == 2


def test_a_missing_workflow_cannot_be_compared(tmp_path: Path) -> None:
    assert check_hermes_release.main([], _api(), tmp_path / "missing.yml") == 2


def test_the_request_helper_refuses_a_non_github_host() -> None:
    with pytest.raises(ValueError, match="non-GitHub URL"):
        check_hermes_release.request("https://example.invalid/repos/a/b")


def test_the_request_helper_posts_json_with_the_token(monkeypatch) -> None:
    sent: dict[str, Any] = {}

    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"number": 7}'

    def _urlopen(prepared: Any, timeout: float) -> _Response:
        sent["request"], sent["timeout"] = prepared, timeout
        return _Response()

    monkeypatch.setattr(check_hermes_release.urllib.request, "urlopen", _urlopen)
    monkeypatch.setenv("GITHUB_TOKEN", "token-for-the-test")

    assert check_hermes_release.request(ISSUES, {"title": TITLE}) == {"number": 7}
    prepared = sent["request"]
    assert prepared.get_method() == "POST"
    assert prepared.data == b'{"title": "Hermes v0.24.0 is out; CI tests v0.23.0"}'
    assert prepared.get_header("Authorization") == "Bearer token-for-the-test"
    assert prepared.get_header("Content-type") == "application/json"
    assert sent["timeout"] == 30


def test_the_weekly_workflow_may_open_issues_and_nothing_more() -> None:
    path = ROOT / ".github" / "workflows" / "hermes-release.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert workflow["permissions"] == {"contents": "read", "issues": "write"}
    # YAML 1.1, which PyYAML reads, takes the `on` key for true.
    assert workflow[True]["schedule"] and "workflow_dispatch" in workflow[True]
    (job,) = workflow["jobs"].values()
    runs = [step["run"] for step in job["steps"] if "run" in step]
    assert runs == ["python scripts/check_hermes_release.py --open-issue"]
