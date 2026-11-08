"""The weekly check that the newest anthropic SDK still sends what the adapter expects."""

from __future__ import annotations

import hashlib
import io
import json
import tomllib
import urllib.error
import zipfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.version import Version

from scripts import check_anthropic_sdk
from scripts.check_anthropic_sdk import (
    ADAPTER_KEYWORDS,
    CHECKED_API_VERSION,
    CONSTANTS,
    INDEX,
    MESSAGES,
    main,
)
from semantic_diff_weaver.providers.anthropic_client import (
    THINKING_ALWAYS_ON_PREFIXES,
    THINKING_BETWEEN_TOOLS_PREFIXES,
    AnthropicClient,
    ProviderUnavailable,
)

ROOT = Path(__file__).parents[2]
FILES = "https://files.pythonhosted.org/packages/"
ISSUES = "https://api.github.com/repos/owner/repo/issues"
NEW_VERSION = "2027-01-15"
MOVED = (
    f"The anthropic SDK sends API version {NEW_VERSION}; "
    f"the adapter was checked against {CHECKED_API_VERSION}"
)


def _extra(*requirements: str) -> str:
    """A pyproject.toml whose `anthropic` extra holds `requirements`."""
    return f"[project.optional-dependencies]\nanthropic = {json.dumps(requirements)}\n"


def _signature(keywords: Iterable[str], *, kwargs: bool = False) -> str:
    names = [f"{keyword}=None" for keyword in keywords]
    return ", ".join(["self", *(["*", *names] if names else []), *(["**kwargs"] if kwargs else [])])


def _messages(
    keywords: Iterable[str] = ADAPTER_KEYWORDS,
    *,
    kwargs: bool = False,
    overloads: Iterable[Iterable[str]] = (),
) -> str:
    """A `messages.py` whose `Messages.create` runs with `keywords`, typed by `overloads` first."""
    lines = ["from typing import overload", "", "", "class Messages:"]
    for names in overloads:
        lines += ["    @overload", f"    def create({_signature(names)}): ...", ""]
    lines += [f"    def create({_signature(keywords, kwargs=kwargs)}):", "        return None", ""]
    return "\n".join(lines)


def _wheel(
    api_version: str = CHECKED_API_VERSION,
    messages: str | None = None,
    *,
    constants: str | None = None,
    leave_out: str = "",
) -> bytes:
    if constants is None:
        constants = f'ANTHROPIC_API_VERSION = "{api_version}"\n'
    sources = {CONSTANTS: constants, MESSAGES: _messages() if messages is None else messages}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in sources.items():
            if name != leave_out:
                archive.writestr(name, text)
    return buffer.getvalue()


def _url(version: str) -> str:
    return f"{FILES}{version}/anthropic-{version}-py3-none-any.whl"


def _file(version: str, body: bytes, **changes: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "filename": f"anthropic-{version}-py3-none-any.whl",
        "url": _url(version),
        "hashes": {"sha256": hashlib.sha256(body).hexdigest()},
        "yanked": False,
    }
    entry.update(changes)
    return entry


class _PyPI:
    """PyPI as the script sees it: the index, the wheels it lists, and every URL asked for."""

    def __init__(
        self, wheels: dict[str, bytes], extra: Iterable[dict[str, Any]] = (), index: Any = None
    ) -> None:
        self.bodies = {_url(version): body for version, body in wheels.items()}
        self.index = index
        if index is None:
            entries = [_file(version, body) for version, body in wheels.items()]
            self.index = {"meta": {"api-version": "1.4"}, "files": [*entries, *extra]}
        self.asked: list[str] = []

    def __call__(self, url: str, limit: int) -> bytes:
        self.asked.append(url)
        if url == INDEX:
            if isinstance(self.index, bytes):
                return self.index
            return json.dumps(self.index).encode("utf-8")
        if url in self.bodies:
            return self.bodies[url]
        raise AssertionError(f"unexpected request: {url}")


class _Api:
    """GitHub's API as the script sees it: the open issues, and every call kept."""

    def __init__(self, issues: list[dict[str, Any]] | None = None) -> None:
        self.issues = issues or []
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def __call__(self, url: str, payload: dict[str, Any] | None) -> Any:
        self.calls.append((url, payload))
        if payload is not None:
            return {"number": 9}
        assert url.split("?")[0] == ISSUES, url
        return self.issues

    @property
    def posted(self) -> list[dict[str, Any]]:
        return [payload for _, payload in self.calls if payload is not None]


@pytest.fixture
def pyproject(tmp_path: Path) -> Path:
    path = tmp_path / "pyproject.toml"
    path.write_text(_extra("anthropic>=0.77,<3"), encoding="utf-8")
    return path


@pytest.fixture
def repository(monkeypatch: pytest.MonkeyPatch) -> None:
    """The variables Actions sets for a run, which the issue names."""
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")


def test_the_keywords_checked_are_the_ones_the_adapter_passes() -> None:
    """A keyword the adapter starts passing fails here until the check looks for it too."""
    passed: set[str] = set()

    class _Transport:
        def with_options(self, **options: Any) -> _Transport:
            return self

        @property
        def messages(self) -> _Transport:
            return self

        def create(self, **request: Any) -> None:
            passed.update(request)
            raise RuntimeError("recorded")

    for model in ("claude-opus-5", *THINKING_ALWAYS_ON_PREFIXES, *THINKING_BETWEEN_TOOLS_PREFIXES):
        client = AnthropicClient(model=model, transport=_Transport())
        with pytest.raises(ProviderUnavailable):
            client.complete_structured(
                instructions="Read the evidence.",
                input=[{"type": "text", "text": "evidence"}],
                json_schema={"type": "object"},
                schema_name="batch",
                temperature=0.1,
                max_tokens=2000,
                timeout=30,
                purpose="test",
            )
    assert passed == set(ADAPTER_KEYWORDS)


def test_the_range_read_is_the_one_the_extra_sets() -> None:
    """Read as packaging reads it, for releases on both sides of each bound."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    (requirement,) = tomllib.loads(text)["project"]["optional-dependencies"]["anthropic"]
    specifier = Requirement(requirement).specifier
    clauses = check_anthropic_sdk.extra_range(text)
    releases = ["0.40.0", "0.76.9", "0.77", "0.77.0", "1.17.0", "2.6.0", "2.99.99", "3", "3.0.0"]
    for release in [*releases, "3.0.1", "10.0.0"]:
        admitted = check_anthropic_sdk.admits(check_anthropic_sdk.number(release), clauses)
        assert admitted == specifier.contains(Version(release)), release


def test_an_sdk_that_sends_what_the_adapter_expects_passes(
    pyproject: Path, capsys: pytest.CaptureFixture[str], repository: None
) -> None:
    api = _Api()

    assert main(["--open-issue"], _PyPI({"2.6.0": _wheel()}), api, pyproject) == 0
    assert (
        "anthropic 2.6.0, the newest release anthropic>=0.77,<3 admits, sends API version "
        f"{CHECKED_API_VERSION}, the one the adapter was checked against"
    ) in capsys.readouterr().out
    assert api.calls == []


def test_the_newest_release_the_extra_admits_is_the_one_read(pyproject: Path) -> None:
    """Releases compare as numbers, and nothing the extra cannot install is read."""
    wheels = {version: _wheel() for version in ("2.5.0", "2.6.0", "2.10.0", "3.0.0")}
    passed_over = [
        _file("2.11.0", b"", yanked="a broken release"),
        _file("2.12.0rc1", b""),
        _file("2.13.0", b"", url="https://example.invalid/anthropic-2.13.0-py3-none-any.whl"),
        _file("2.14.0", b"", hashes={"sha256": "not a digest"}),
        {**_file("2.15.0", b""), "filename": "anthropic-2.15.0.tar.gz"},
        "not a file",
    ]
    pypi = _PyPI(wheels, extra=passed_over)

    assert main([], pypi, _Api(), pyproject) == 0
    assert pypi.asked == [INDEX, _url("2.10.0")]


def test_a_new_api_version_fails_the_check_without_an_issue(
    pyproject: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = _Api()

    assert main([], _PyPI({"2.7.0": _wheel(NEW_VERSION)}), api, pyproject) == 1
    assert (
        f"anthropic 2.7.0 sends API version {NEW_VERSION}; "
        f"the adapter was checked against {CHECKED_API_VERSION}."
    ) in capsys.readouterr().out
    assert api.calls == []


def test_a_new_api_version_opens_an_issue_saying_how_to_check_it(
    pyproject: Path, capsys: pytest.CaptureFixture[str], repository: None
) -> None:
    api = _Api()

    assert main(["--open-issue"], _PyPI({"2.7.0": _wheel(NEW_VERSION)}), api, pyproject) == 0
    assert "opened issue #9" in capsys.readouterr().out
    (issue,) = api.posted
    assert issue["title"] == MOVED
    body = issue["body"]
    assert (
        "anthropic [2.7.0](https://github.com/anthropics/anthropic-sdk-python/releases/tag/v2.7.0),"
        " the newest release the extra's `anthropic>=0.77,<3` admits"
    ) in body
    assert (
        f"- it sends API version `{NEW_VERSION}`, and the adapter's requests were checked against "
        f"`{CHECKED_API_VERSION}`;"
    ) in body
    assert "no longer takes" not in body
    assert "*Anthropic requests carry no sampling parameters*" in body
    assert "`CHECKED_API_VERSION` or `ADAPTER_KEYWORDS`" in body
    assert "[run 123](https://github.com/owner/repo/actions/runs/123)." in body


def test_an_api_version_gets_one_issue(
    pyproject: Path, capsys: pytest.CaptureFixture[str], repository: None
) -> None:
    api = _Api(issues=[{"number": 4, "title": MOVED}])

    assert main(["--open-issue"], _PyPI({"2.7.1": _wheel(NEW_VERSION)}), api, pyproject) == 0
    assert "issue #4 is open already" in capsys.readouterr().out
    assert api.posted == []


def test_a_keyword_create_stopped_taking_opens_its_own_issue(
    pyproject: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    keywords = [keyword for keyword in ADAPTER_KEYWORDS if keyword != "thinking"]
    api = _Api()

    pypi = _PyPI({"2.7.0": _wheel(messages=_messages(keywords))})
    assert main(["--open-issue"], pypi, api, pyproject) == 0
    assert "anthropic 2.7.0's messages.create no longer takes thinking." in (
        capsys.readouterr().out
    )
    (issue,) = api.posted
    assert issue["title"] == "The anthropic SDK's messages.create no longer takes thinking"
    body = issue["body"]
    assert "- its `messages.create` no longer takes `thinking`, which the adapter passes;" in body
    assert "it sends API version" not in body
    assert "Opened by" not in body


def test_both_changes_share_the_api_version_s_issue(pyproject: Path, repository: None) -> None:
    api = _Api()

    wheel = _wheel(NEW_VERSION, _messages(ADAPTER_KEYWORDS[:-1]))
    assert main(["--open-issue"], _PyPI({"2.7.0": wheel}), api, pyproject) == 0
    (issue,) = api.posted
    assert issue["title"] == MOVED
    assert "- it sends API version" in issue["body"]
    assert "no longer takes `thinking`" in issue["body"]


@pytest.mark.parametrize(
    ("source", "missing"),
    [
        (_messages(overloads=[ADAPTER_KEYWORDS]), []),
        (_messages(ADAPTER_KEYWORDS[1:], overloads=[ADAPTER_KEYWORDS]), ["max_tokens"]),
        (_messages(overloads=[ADAPTER_KEYWORDS[:2]]), []),
        (_messages([], kwargs=True), []),
        (
            "class Messages:\n    def create(self, model, /, *, max_tokens, messages, system,"
            " output_config, thinking):\n        return None\n",
            ["model"],
        ),
        (
            "class Messages:\n    def create(self, model, max_tokens, messages, system,"
            " output_config, thinking):\n        return None\n",
            [],
        ),
    ],
    ids=[
        "every-keyword",
        "the-definition-that-runs-lacks-one",
        "only-an-overload-lacks-them",
        "kwargs-takes-all",
        "positional-only-takes-no-keyword",
        "ordinary-parameters-take-keywords",
    ],
)
def test_only_the_create_that_runs_counts(source: str, missing: list[str]) -> None:
    assert check_anthropic_sdk.missing_keywords(source) == missing


def _unreadable(**changes: Any) -> _PyPI:
    """A PyPI whose 2.6.0 wheel was built with `changes`, or listed with `index` instead."""
    index = changes.pop("index", None)
    return _PyPI({"2.6.0": _wheel(**changes)}, index=index)


def _mislisted() -> _PyPI:
    pypi = _PyPI({"2.6.0": _wheel()})
    pypi.index["files"][0]["hashes"]["sha256"] = "0" * 64
    return pypi


def _not_a_zip() -> _PyPI:
    body = b"not a zip file"
    return _PyPI({"2.6.0": body})


@pytest.mark.parametrize(
    "pypi",
    [
        lambda: _unreadable(index=b"<html>not JSON</html>"),
        lambda: _unreadable(index={"meta": {}, "projects": []}),
        lambda: _PyPI({"3.0.0": _wheel()}),
        _mislisted,
        _not_a_zip,
        lambda: _unreadable(leave_out=CONSTANTS),
        lambda: _unreadable(leave_out=MESSAGES),
        lambda: _unreadable(constants="DEFAULT_TIMEOUT = 600\n"),
        lambda: _unreadable(constants='ANTHROPIC_API_VERSION = "latest"\n'),
        lambda: _unreadable(constants="ANTHROPIC_API_VERSION = VERSIONS[-1]\n"),
        lambda: _unreadable(messages="class Messages:\n    def count_tokens(self): ...\n"),
        lambda: _unreadable(messages="class Messages(:\n"),
    ],
    ids=[
        "index-not-json",
        "index-without-files",
        "no-release-in-range",
        "digest-not-pypi-s",
        "not-a-wheel",
        "no-constants",
        "no-messages",
        "no-api-version",
        "api-version-not-a-date",
        "api-version-not-a-string",
        "no-create",
        "messages-not-python",
    ],
)
def test_an_sdk_that_cannot_be_read_cannot_be_checked(
    pyproject: Path,
    capsys: pytest.CaptureFixture[str],
    repository: None,
    pypi: Callable[[], _PyPI],
) -> None:
    api = _Api()

    assert main(["--open-issue"], pypi(), api, pyproject) == 2
    assert "Could not check the anthropic SDK" in capsys.readouterr().err
    assert api.calls == []


def test_the_last_assignment_of_the_api_version_is_the_one_read() -> None:
    constants = 'ANTHROPIC_API_VERSION = "2023-06-01"\nANTHROPIC_API_VERSION: str = "2026-10-21"\n'
    assert check_anthropic_sdk.api_version(constants) == "2026-10-21"


def test_an_unreachable_pypi_cannot_be_checked(
    pyproject: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def _offline(url: str, limit: int) -> bytes:
        raise urllib.error.URLError("offline")

    assert main([], _offline, _Api(), pyproject) == 2
    assert "offline" in capsys.readouterr().err


@pytest.mark.parametrize(
    "text",
    [
        _extra("anthropic~=2.0"),
        _extra("anthropic>=0.77,<3; python_version > '3'"),
        _extra("anthropic[bedrock]>=0.77"),
        _extra("anthropic==2.*"),
        _extra("anthropic"),
        _extra("anthropic-tools>=1"),
        _extra("anthropic>=0.77", "httpx>=0.27"),
        "[project]\nname = 'x'\n",
        "not toml = [",
    ],
    ids=[
        "compatible-release",
        "marker",
        "extras",
        "wildcard",
        "no-range",
        "another-package",
        "two-requirements",
        "no-extra",
        "not-toml",
    ],
)
def test_an_extra_whose_range_cannot_be_read_is_not_checked(tmp_path: Path, text: str) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text(text, encoding="utf-8")
    pypi = _PyPI({"2.6.0": _wheel()})

    assert main([], pypi, _Api(), path) == 2
    assert pypi.asked == []


@pytest.mark.parametrize("repository_name", ["", "owner", "../owner/repo"])
def test_without_a_repository_to_open_it_in_no_issue_is_opened(
    pyproject: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    repository_name: str,
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", repository_name)
    api = _Api()

    assert main(["--open-issue"], _PyPI({"2.7.0": _wheel(NEW_VERSION)}), api, pyproject) == 1
    assert "Could not open the issue: GITHUB_REPOSITORY names no repository" in (
        capsys.readouterr().err
    )
    assert api.posted == []


@pytest.mark.parametrize(
    "url",
    [
        "https://example.invalid/simple/anthropic/",
        "https://pypi.org/simple/anthropic-tools/",
        "http://files.pythonhosted.org/packages/anthropic-2.6.0-py3-none-any.whl",
    ],
)
def test_the_fetch_helper_refuses_anything_but_pypi(url: str) -> None:
    with pytest.raises(ValueError, match="outside PyPI"):
        check_anthropic_sdk.fetch_pypi(url, 100)


class _Response:
    def __init__(self, url: str, body: bytes) -> None:
        self.url, self.body = url, body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def geturl(self) -> str:
        return self.url

    def read(self, size: int) -> bytes:
        return self.body[:size]


def test_the_fetch_helper_asks_for_the_json_index(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}

    def _urlopen(prepared: Any, timeout: float) -> _Response:
        sent["request"], sent["timeout"] = prepared, timeout
        return _Response(prepared.full_url, b'{"files": []}')

    monkeypatch.setattr(check_anthropic_sdk.urllib.request, "urlopen", _urlopen)

    assert check_anthropic_sdk.fetch_pypi(INDEX, 100) == b'{"files": []}'
    assert sent["request"].get_header("Accept") == "application/vnd.pypi.simple.v1+json"
    assert sent["timeout"] == 60
    assert check_anthropic_sdk.fetch_pypi(_url("2.6.0"), 100) == b'{"files": []}'
    assert sent["request"].get_header("Accept") is None


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (_Response(INDEX, b"x" * 101), "larger than 100 bytes"),
        (_Response("https://example.invalid/simple/anthropic/", b"{}"), "redirected outside PyPI"),
    ],
    ids=["too-large", "redirected"],
)
def test_the_fetch_helper_refuses_a_body_it_should_not_read(
    monkeypatch: pytest.MonkeyPatch, answer: _Response, message: str
) -> None:
    monkeypatch.setattr(
        check_anthropic_sdk.urllib.request, "urlopen", lambda prepared, timeout: answer
    )

    with pytest.raises(ValueError, match=message):
        check_anthropic_sdk.fetch_pypi(INDEX, 100)


def test_a_source_past_its_limit_is_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(check_anthropic_sdk, "MAX_SOURCE_BYTES", 10)
    wheel = _wheel()

    with pytest.raises(ValueError, match="larger than 10 bytes"):
        check_anthropic_sdk.read_sources(wheel, hashlib.sha256(wheel).hexdigest())


def test_the_weekly_workflow_may_open_issues_and_nothing_more() -> None:
    path = ROOT / ".github" / "workflows" / "anthropic-sdk.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert workflow["permissions"] == {"contents": "read", "issues": "write"}
    # YAML 1.1, which PyYAML reads, takes the `on` key for true.
    assert workflow[True]["schedule"] and "workflow_dispatch" in workflow[True]
    (job,) = workflow["jobs"].values()
    runs = [step["run"] for step in job["steps"] if "run" in step]
    assert runs == ["python scripts/check_anthropic_sdk.py --open-issue"]
