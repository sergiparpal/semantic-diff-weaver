"""Say whether CI tests the newest Hermes release, and open an issue when it does not.

CI's `hermes-current` job builds the Hermes release that `HERMES_TAG` names in
`.github/workflows/ci.yml` from its Git tag, since PyPI stops at 0.19.0. Dependabot sees neither
that tag nor the commit beside it, so nothing else notices a new release. This compares the tag with
the latest release of NousResearch/hermes-agent:

    python scripts/check_hermes_release.py               # exits 1 when CI is behind
    python scripts/check_hermes_release.py --open-issue  # and opens an issue for it

With `--open-issue`, which the weekly `hermes-release` workflow passes, a release CI is behind on
gets one issue, titled after the release, unless an open issue has that title already. The issue
is the notice, so the run then succeeds; a run that fails could not tell, or could not open it.
GITHUB_TOKEN raises the API's rate limit, and opening an issue needs it, with `issues: write`, in
the repository GITHUB_REPOSITORY names. Exit codes: 0 up to date or notified, 1 behind or not
notified, 2 unable to compare.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

API_ROOT = "https://api.github.com/"
UPSTREAM = "NousResearch/hermes-agent"
WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
PINNED = re.compile(r'^[ \t]+HERMES_TAG:[ \t]*"(?P<tag>[^"\n]*)"[ \t]*$', re.MULTILINE)
RELEASE = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
COMMIT = re.compile(r"[0-9a-f]{40}")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
# This repository holds a handful of open issues; a page this long reaches the one to find.
ISSUE_PAGE = 100

# GETs a GitHub API URL, or POSTs the payload to it, and returns the decoded JSON.
Api = Callable[[str, dict[str, Any] | None], Any]


def request(url: str, payload: dict[str, Any] | None = None) -> Any:
    """Call the GitHub API: GET, or POST with a JSON payload."""
    if not url.startswith(API_ROOT):
        raise ValueError(f"refusing to request a non-GitHub URL: {url}")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "semantic-diff-weaver-hermes-release",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    prepared = urllib.request.Request(url, data=data, headers=headers)  # noqa: S310 - API_ROOT
    with urllib.request.urlopen(prepared, timeout=30) as response:  # noqa: S310 - see above
        return json.loads(response.read().decode("utf-8"))


def version(tag: str) -> tuple[int, int, int]:
    """A release tag, `v0.23.0`, as numbers that compare as releases do."""
    match = RELEASE.fullmatch(tag)
    if match is None:
        raise ValueError(f"not a release tag of the form vX.Y.Z: {tag!r}")
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def pinned_release(workflow: str) -> str:
    """The release `hermes-current` builds: the one `HERMES_TAG` the workflow sets."""
    found = PINNED.findall(workflow)
    if len(found) != 1:
        raise ValueError(f"ci.yml sets HERMES_TAG {len(found)} times, not once")
    version(found[0])
    return str(found[0])


def latest_release(api: Api) -> dict[str, str]:
    """NousResearch/hermes-agent's latest release: its tag and the day it was published.

    Only a tag of the form vX.Y.Z is taken, and the release's page is built from it, so nothing
    else the API returns reaches an issue.
    """
    release = api(f"{API_ROOT}repos/{UPSTREAM}/releases/latest", None)
    if not isinstance(release, dict):
        raise ValueError(f"the latest {UPSTREAM} release is no JSON object")
    tag = release.get("tag_name")
    if not isinstance(tag, str):
        raise ValueError(f"the latest {UPSTREAM} release has no tag")
    version(tag)
    published = str(release.get("published_at") or "")[:10]
    return {"tag": tag, "published": published if DATE.fullmatch(published) else ""}


def tag_commit(api: Api, tag: str) -> str | None:
    """The commit a tag names, through an annotated tag's object; None if the API does not say."""
    ref = api(f"{API_ROOT}repos/{UPSTREAM}/git/ref/tags/{tag}", None)
    target = ref.get("object") if isinstance(ref, dict) else None
    if isinstance(target, dict) and target.get("type") == "tag":
        sha = target.get("sha")
        if not isinstance(sha, str) or not COMMIT.fullmatch(sha):
            return None
        annotated = api(f"{API_ROOT}repos/{UPSTREAM}/git/tags/{sha}", None)
        target = annotated.get("object") if isinstance(annotated, dict) else None
    if not isinstance(target, dict) or target.get("type") != "commit":
        return None
    sha = target.get("sha")
    return sha if isinstance(sha, str) and COMMIT.fullmatch(sha) else None


def issue_title(latest: str, pinned: str) -> str:
    return f"Hermes {latest} is out; CI tests {pinned}"


def issue_body(latest: dict[str, str], pinned: str, commit: str | None) -> str:
    """What to move, and where to find each value, for the release CI is behind on."""
    tag = latest["tag"]
    page = f"https://github.com/{UPSTREAM}/releases/tag/{tag}"
    published = f" on {latest['published']}" if latest["published"] else ""
    named = f", `{commit}` when this issue was opened" if commit else ""
    installer = f"https://github.com/{UPSTREAM}/blob/{tag}/scripts/install.sh"
    lines = [
        f"{UPSTREAM} published [{tag}]({page}){published}. CI's `hermes-current` job still "
        f"builds {pinned}.",
        "",
        "Move these together in `.github/workflows/ci.yml`, as AGENTS.md says:",
        "",
        f"- `HERMES_TAG` to `{tag}`;",
        f"- `HERMES_COMMIT` to the commit the tag names{named};",
        f"- `UV_VERSION` to the uv that [`scripts/install.sh`]({installer}) pins at that tag.",
        "",
        "Let `hermes-current` run on the pull request, then update the documents that name "
        f"{pinned} as the current release, `docs/decisions.md`'s compatibility snapshot and "
        "`docs/release-checklist.md` among them.",
    ]
    server = os.environ.get("GITHUB_SERVER_URL", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    run = os.environ.get("GITHUB_RUN_ID", "")
    if server.startswith("https://") and REPOSITORY.fullmatch(repository) and run.isdecimal():
        url = f"{server}/{repository}/actions/runs/{run}"
        lines += ["", f"Opened by the weekly `hermes-release` workflow, [run {run}]({url})."]
    return "\n".join(lines) + "\n"


def open_issue(api: Api, repository: str, title: str, body: str) -> str:
    """Open an issue titled `title`, unless an open issue, not a pull request, has that title."""
    if not REPOSITORY.fullmatch(repository):
        raise ValueError(f"GITHUB_REPOSITORY names no repository: {repository!r}")
    issues = api(f"{API_ROOT}repos/{repository}/issues?state=open&per_page={ISSUE_PAGE}", None)
    for issue in issues if isinstance(issues, list) else []:
        if isinstance(issue, dict) and "pull_request" not in issue and issue.get("title") == title:
            return f"issue #{issue.get('number')} is open already"
    created = api(f"{API_ROOT}repos/{repository}/issues", {"title": title, "body": body})
    if not isinstance(created, dict) or not isinstance(created.get("number"), int):
        raise ValueError("the API did not say which issue it opened")
    return f"opened issue #{created['number']}"


def main(argv: list[str] | None = None, api: Api = request, workflow: Path = WORKFLOW) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--open-issue",
        action="store_true",
        help="when CI is behind, open an issue in GITHUB_REPOSITORY, once per release",
    )
    arguments = parser.parse_args(argv)
    try:
        pinned = pinned_release(workflow.read_text(encoding="utf-8"))
        latest = latest_release(api)
    except (OSError, ValueError, urllib.error.URLError) as error:
        print(f"Could not compare the Hermes releases: {error}", file=sys.stderr)
        return 2
    if version(latest["tag"]) <= version(pinned):
        print(f"CI tests {pinned}, and the latest Hermes release is {latest['tag']}.")
        return 0
    print(f"Hermes {latest['tag']} is out; CI's hermes-current job tests {pinned}.")
    if not arguments.open_issue:
        return 1
    try:
        commit = tag_commit(api, latest["tag"])
    except (ValueError, urllib.error.URLError):
        commit = None
    title = issue_title(latest["tag"], pinned)
    try:
        status = open_issue(
            api,
            os.environ.get("GITHUB_REPOSITORY", ""),
            title,
            issue_body(latest, pinned, commit),
        )
    except (ValueError, urllib.error.URLError) as error:
        print(f"Could not open the issue: {error}", file=sys.stderr)
        return 1
    print(status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
