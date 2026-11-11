"""Check a published GitHub Release against its tag, the changelog, and the files built for it.

After `gh release create TAG --verify-tag`, this reads the Release back from the GitHub API and
checks what the release checklist asks of it:

    python scripts/check_release.py v0.20.0 dist/

- its title is the tag's one-line message, and it is neither a draft nor a prerelease;
- its notes are exactly what `scripts/release_notes.py` writes for the version;
- it carries exactly the files in the directory, each uploaded, with the SHA-256 digest of the
  local file, which `scripts/verify_wheel.py` checked.

It also says whether the Release is the latest, without failing when it is not. The tag must be
fetched locally. GITHUB_TOKEN raises the API's rate limit. Exit codes: 0 every check passed, 1 one
failed, 2 unable to check.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.release_notes import CHANGELOG, NotesError, notes  # noqa: E402

API_ROOT = "https://api.github.com/"
DEFAULT_REPOSITORY = "sergiparpal/semantic-diff-weaver"

# GETs a GitHub API URL and returns the decoded JSON.
Api = Callable[[str], Any]
# Returns a tag's one-line message.
Subject = Callable[[str], str]


class CheckError(Exception):
    """The check could not run: no such Release or tag, or no files to compare."""


def request(url: str) -> Any:
    """GET a GitHub API URL."""
    if not url.startswith(API_ROOT):
        raise ValueError(f"refusing to request a non-GitHub URL: {url}")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "semantic-diff-weaver-release-check",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    prepared = urllib.request.Request(url, headers=headers)  # noqa: S310 - API_ROOT
    with urllib.request.urlopen(prepared, timeout=30) as reply:  # noqa: S310 - see above
        return json.load(reply)


def tag_subject(tag: str, repository: Path = REPOSITORY_ROOT) -> str:
    """The tag's one-line message, read from the local repository."""
    git = shutil.which("git")
    if git is None:
        raise CheckError("git is not on PATH")
    completed = subprocess.run(
        [git, "tag", "--list", "--format=%(contents:subject)", tag],
        cwd=repository,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    subject = completed.stdout.strip()
    if completed.returncode != 0 or not subject:
        raise CheckError(f"no local tag {tag} with a message; fetch the tags first")
    return subject


def digests(directory: Path) -> dict[str, str]:
    """Each file in the directory, by name, to its SHA-256 digest as the API writes it."""
    if not directory.is_dir():
        raise CheckError(f"{directory} is not a directory")
    found = {
        path.name: "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.iterdir())
        if path.is_file()
    }
    if not found:
        raise CheckError(f"{directory} holds no files to compare")
    return found


def check(
    tag: str,
    directory: Path,
    *,
    repository: str = DEFAULT_REPOSITORY,
    changelog: str,
    api: Api = request,
    subject: Subject = tag_subject,
) -> list[tuple[bool, str]]:
    """Each check's outcome and what it found."""
    try:
        release = api(f"{API_ROOT}repos/{repository}/releases/tags/{tag}")
    except urllib.error.HTTPError as error:
        raise CheckError(f"no Release for {tag} in {repository} ({error.code})") from error
    local = digests(directory)
    title = subject(tag)
    try:
        expected = notes(changelog, tag.removeprefix("v"))
    except NotesError as error:
        raise CheckError(str(error)) from error
    results = [
        (release.get("name") == title, f"title {release.get('name')!r}, the tag's message"),
        (not release.get("draft") and not release.get("prerelease"), "published, not a draft"),
        ((release.get("body") or "").replace("\r\n", "\n") == expected, "notes as written"),
    ]
    assets = {asset["name"]: asset for asset in release.get("assets", [])}
    if set(assets) != set(local):
        missing = sorted(set(local) - set(assets))
        extra = sorted(set(assets) - set(local))
        results.append((False, f"assets differ: missing {missing}, unexpected {extra}"))
    for name, digest in local.items():
        asset = assets.get(name)
        if asset is None:
            continue
        uploaded = asset.get("state") == "uploaded"
        results.append(
            (uploaded and asset.get("digest") == digest, f"{name}: {asset.get('size')} bytes")
        )
    return results


def is_latest(tag: str, repository: str, api: Api) -> bool:
    try:
        return bool(api(f"{API_ROOT}repos/{repository}/releases/latest").get("tag_name") == tag)
    except urllib.error.HTTPError:
        return False


def main(argv: list[str] | None = None, api: Api = request, subject: Subject = tag_subject) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("tag", help="the release tag, as `v0.20.0`")
    parser.add_argument("dist", type=Path, help="the directory of the files attached to it")
    parser.add_argument(
        "--repository", default=os.environ.get("GITHUB_REPOSITORY") or DEFAULT_REPOSITORY
    )
    parser.add_argument("--changelog", type=Path, default=CHANGELOG)
    arguments = parser.parse_args(argv)
    try:
        changelog = arguments.changelog.read_text(encoding="utf-8")
        results = check(
            arguments.tag,
            arguments.dist,
            repository=arguments.repository,
            changelog=changelog,
            api=api,
            subject=subject,
        )
    except (OSError, CheckError, urllib.error.URLError) as error:
        print(f"check_release: {error}", file=sys.stderr)
        return 2
    for passed, found in results:
        print(f"{'ok  ' if passed else 'FAIL'} {found}")
    latest = is_latest(arguments.tag, arguments.repository, api)
    print(f"{arguments.tag} is {'the latest Release' if latest else 'not the latest Release'}.")
    return 0 if all(passed for passed, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
