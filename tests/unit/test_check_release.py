"""The check of a published GitHub Release against its tag, the changelog, and its files."""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import urllib.error
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from scripts import check_release, release_notes

RELEASES = "https://api.github.com/repos/owner/repo/releases/"
TITLE = "v1.2.0 — a summary"
CHANGELOG = "# Changelog\n\n## 1.2.0 - 2026-11-10\n\nWhat changed.\n"
WHEEL = b"wheel bytes"
SDIST = b"sdist bytes"


def _dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "pkg-1.2.0-py3-none-any.whl").write_bytes(WHEEL)
    (dist / "pkg-1.2.0.tar.gz").write_bytes(SDIST)
    return dist


def _asset(name: str, content: bytes, **changes: Any) -> dict[str, Any]:
    asset = {
        "name": name,
        "size": len(content),
        "state": "uploaded",
        "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
    }
    return {**asset, **changes}


def _release(**changes: Any) -> dict[str, Any]:
    release = {
        "tag_name": "v1.2.0",
        "name": TITLE,
        "draft": False,
        "prerelease": False,
        "body": release_notes.notes(CHANGELOG, "1.2.0"),
        "assets": [
            _asset("pkg-1.2.0-py3-none-any.whl", WHEEL),
            _asset("pkg-1.2.0.tar.gz", SDIST),
        ],
    }
    return {**release, **changes}


def _api(release: dict[str, Any], latest: str = "v1.2.0") -> check_release.Api:
    def answer(url: str) -> Any:
        if url == f"{RELEASES}tags/v1.2.0":
            return release
        if url == f"{RELEASES}latest":
            return {"tag_name": latest}
        raise urllib.error.HTTPError(url, 404, "Not Found", Message(), io.BytesIO(b""))

    return answer


def _run(tmp_path: Path, release: dict[str, Any], *extra: str, latest: str = "v1.2.0") -> int:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")
    return check_release.main(
        [
            "v1.2.0",
            str(_dist(tmp_path)),
            "--repository",
            "owner/repo",
            "--changelog",
            str(changelog),
            *extra,
        ],
        api=_api(release, latest),
        subject=lambda tag: TITLE,
    )


def test_a_release_as_published_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(tmp_path, _release()) == 0
    out = capsys.readouterr().out
    assert out.count("ok  ") == 5
    assert "v1.2.0 is the latest Release." in out


def test_a_release_that_is_not_the_latest_still_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(tmp_path, _release(), latest="v1.3.0") == 0
    assert "v1.2.0 is not the latest Release." in capsys.readouterr().out


@pytest.mark.parametrize(
    ("changes", "failure"),
    [
        pytest.param({"name": "v1.2.0"}, "title", id="title"),
        pytest.param({"draft": True}, "published", id="draft"),
        pytest.param({"prerelease": True}, "published", id="prerelease"),
        pytest.param({"body": "Released on 2026-11-10.\n"}, "notes", id="notes"),
        pytest.param(
            {"assets": [_asset("pkg-1.2.0-py3-none-any.whl", WHEEL)]}, "assets differ", id="missing"
        ),
        pytest.param(
            {
                "assets": [
                    _asset("pkg-1.2.0-py3-none-any.whl", WHEEL),
                    _asset("pkg-1.2.0.tar.gz", b"other bytes"),
                ]
            },
            "pkg-1.2.0.tar.gz",
            id="digest",
        ),
        pytest.param(
            {
                "assets": [
                    _asset("pkg-1.2.0-py3-none-any.whl", WHEEL, state="starter"),
                    _asset("pkg-1.2.0.tar.gz", SDIST),
                ]
            },
            "pkg-1.2.0-py3-none-any.whl",
            id="not-uploaded",
        ),
    ],
)
def test_each_difference_fails_its_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], changes: dict[str, Any], failure: str
) -> None:
    assert _run(tmp_path, _release(**changes)) == 1
    failed = [line for line in capsys.readouterr().out.splitlines() if line.startswith("FAIL")]
    assert len(failed) == 1
    assert failure in failed[0]


def test_a_body_with_windows_line_breaks_reads_as_written(tmp_path: Path) -> None:
    body = release_notes.notes(CHANGELOG, "1.2.0").replace("\n", "\r\n")
    assert _run(tmp_path, _release(body=body)) == 0


def test_a_missing_release_cannot_be_checked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")
    code = check_release.main(
        [
            "v9.9.9",
            str(_dist(tmp_path)),
            "--repository",
            "owner/repo",
            "--changelog",
            str(changelog),
        ],
        api=_api(_release()),
        subject=lambda tag: TITLE,
    )
    assert code == 2
    assert "no Release for v9.9.9" in capsys.readouterr().err


def test_an_empty_directory_cannot_be_checked(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(check_release.CheckError, match="no files"):
        check_release.digests(empty)
    with pytest.raises(check_release.CheckError, match="not a directory"):
        check_release.digests(tmp_path / "absent")


def test_the_api_refuses_a_url_outside_github() -> None:
    with pytest.raises(ValueError, match="non-GitHub"):
        check_release.request("https://example.invalid/repos")


def test_the_tag_message_comes_from_the_local_repository(repo_factory) -> None:
    repo, _, _ = repo_factory({"a.txt": "one\n"}, {"a.txt": "two\n"})
    git = shutil.which("git")
    assert git is not None
    subprocess.run(
        [git, "tag", "--annotate", "v1.2.0", "--message", f"{TITLE}\n\nA longer body."],
        cwd=repo,
        check=True,
        env={
            **os.environ,
            "GIT_COMMITTER_NAME": "Tests",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
        },
    )

    assert check_release.tag_subject("v1.2.0", repo) == TITLE
    with pytest.raises(check_release.CheckError, match="no local tag"):
        check_release.tag_subject("v9.9.9", repo)
