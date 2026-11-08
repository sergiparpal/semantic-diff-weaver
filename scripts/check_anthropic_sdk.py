"""Say whether the newest anthropic SDK the extra admits still sends what the adapter expects, and
open an issue when it does not.

The adapter, `semantic_diff_weaver/providers/anthropic_client.py`, shapes each request for the API
version the SDK sends, and a request that version rejects fails every call into deterministic
fallback behind a generic warning. CI never installs the `anthropic` extra, so no test runs the
SDK, and a release inside the extra's range reaches users with no Dependabot pull request. This
reads the newest such release from PyPI, without installing or importing it, and checks two
things:

- the API version its `anthropic/_constants.py` sets is `CHECKED_API_VERSION`, the one the
  adapter's requests were last checked against;
- its `Messages.create` still takes every keyword the adapter passes, `ADAPTER_KEYWORDS`.

    python scripts/check_anthropic_sdk.py               # exits 1 when either moved
    python scripts/check_anthropic_sdk.py --open-issue  # and opens an issue for it

With `--open-issue`, which the weekly `anthropic-sdk` workflow passes, a new API version, or a set
of keywords `create` stopped taking, gets one issue, titled after it, unless an open issue has that
title already. The issue is the notice, so the run then succeeds; a run that fails could not
check, or could not open it. Opening an issue needs GITHUB_TOKEN, with `issues: write`, in the
repository GITHUB_REPOSITORY names. Exit codes: 0 unchanged or notified, 1 moved and not
notified, 2 unable to check.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import http.client
import io
import json
import os
import re
import sys
import tomllib
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.check_hermes_release import REPOSITORY, Api, open_issue, request  # noqa: E402

PYPROJECT = REPOSITORY_ROOT / "pyproject.toml"
INDEX = "https://pypi.org/simple/anthropic/"
FILES = "https://files.pythonhosted.org/"
RELEASES = "https://github.com/anthropics/anthropic-sdk-python/releases/tag/v"
# The API version the adapter's requests were last checked against: offline, with a local stub
# server recording the version header and the body each SDK release sends, as docs/decisions.md
# describes on 2026-10-25. Move it only once a new version has been checked the same way.
CHECKED_API_VERSION = "2026-10-21"
# Every keyword `AnthropicClient.complete_structured` passes to `messages.create`, for one model
# family or another. A unit test keeps the two in step.
ADAPTER_KEYWORDS = ("max_tokens", "messages", "model", "output_config", "system", "thinking")
CONSTANTS = "anthropic/_constants.py"
MESSAGES = "anthropic/resources/messages/messages.py"
# A final release's pure-Python wheel, which is what installing the extra takes.
WHEEL = re.compile(r"anthropic-(?P<version>\d+(?:\.\d+)*)-py3-none-any\.whl")
CLAUSE = re.compile(r"(?P<operator>>=|<=|==|!=|>|<)(?P<version>\d+(?:\.\d+)*)")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
SHA256 = re.compile(r"[0-9a-f]{64}")
MAX_INDEX_BYTES = 20_000_000
MAX_WHEEL_BYTES = 50_000_000
MAX_SOURCE_BYTES = 5_000_000

Version = tuple[int, ...]
Clauses = list[tuple[str, Version]]
# GETs a PyPI URL and returns its body, refusing one longer than the limit in bytes.
Fetch = Callable[[str, int], bytes]


def fetch_pypi(url: str, limit: int) -> bytes:
    """GET the project's JSON index on PyPI, or a file it lists, and nothing else."""
    if not url.startswith((INDEX, FILES)):
        raise ValueError(f"refusing to fetch outside PyPI: {url}")
    headers = {"User-Agent": "semantic-diff-weaver-anthropic-sdk"}
    if url == INDEX:
        headers["Accept"] = "application/vnd.pypi.simple.v1+json"
    prepared = urllib.request.Request(url, headers=headers)  # noqa: S310 - PyPI only, see above
    with urllib.request.urlopen(prepared, timeout=60) as response:  # noqa: S310 - see above
        if not str(response.geturl()).startswith((INDEX, FILES)):
            raise ValueError(f"{url} redirected outside PyPI")
        body = response.read(limit + 1)
    if len(body) > limit:
        raise ValueError(f"{url} is larger than {limit:,} bytes")
    return bytes(body)


def number(version: str) -> Version:
    """A release, `2.6.0`, as numbers."""
    return tuple(int(part) for part in version.split("."))


def compare(left: Version, right: Version) -> int:
    """-1, 0, or 1, comparing releases as PEP 440 does, so that `3` and `3.0.0` are equal."""
    width = max(len(left), len(right))
    padded = left + (0,) * (width - len(left)), right + (0,) * (width - len(right))
    return (padded[0] > padded[1]) - (padded[0] < padded[1])


def extra_range(pyproject: str) -> Clauses:
    """The `anthropic` extra's one requirement, `anthropic>=0.77,<3`, as comparisons of numbers.

    Only comparisons of plain release numbers are read. Anything else, a marker or `~=` among
    them, is refused rather than misread.
    """
    project = tomllib.loads(pyproject).get("project", {})
    requirements = project.get("optional-dependencies", {}).get("anthropic")
    if not isinstance(requirements, list) or len(requirements) != 1:
        raise ValueError("the anthropic extra does not hold one requirement")
    requirement = re.sub(r"\s+", "", str(requirements[0]))
    unreadable = ValueError(f"cannot read the anthropic extra's range: {requirements[0]!r}")
    if not requirement.startswith("anthropic"):
        raise unreadable
    clauses: Clauses = []
    for clause in requirement.removeprefix("anthropic").split(","):
        match = CLAUSE.fullmatch(clause)
        if match is None:
            raise unreadable
        clauses.append((match["operator"], number(match["version"])))
    return clauses


def admits(version: Version, clauses: Clauses) -> bool:
    for operator, bound in clauses:
        order = compare(version, bound)
        held = {
            ">=": order >= 0,
            ">": order > 0,
            "<=": order <= 0,
            "<": order < 0,
            "==": order == 0,
            "!=": order != 0,
        }[operator]
        if not held:
            return False
    return True


def written(clauses: Clauses) -> str:
    """The clauses as the requirement writes them: `>=0.77,<3`."""
    return ",".join(operator + ".".join(map(str, bound)) for operator, bound in clauses)


def newest_admitted(index: Any, clauses: Clauses) -> dict[str, str]:
    """The newest final release the clauses admit, with its wheel's URL and digest.

    A yanked file, a URL off PyPI's file host, or a malformed digest is passed over.
    """
    files = index.get("files") if isinstance(index, dict) else None
    if not isinstance(files, list):
        raise ValueError("PyPI's index for anthropic lists no files")
    newest: tuple[Version, dict[str, str]] | None = None
    for entry in files:
        if not isinstance(entry, dict) or entry.get("yanked"):
            continue
        match = WHEEL.fullmatch(str(entry.get("filename", "")))
        url = entry.get("url")
        hashes = entry.get("hashes")
        digest = hashes.get("sha256") if isinstance(hashes, dict) else None
        if match is None or not isinstance(url, str) or not url.startswith(FILES):
            continue
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            continue
        version = number(match["version"])
        if admits(version, clauses) and (newest is None or compare(version, newest[0]) > 0):
            newest = version, {"version": match["version"], "url": url, "sha256": digest}
    if newest is None:
        raise ValueError(f"PyPI lists no wheel of an anthropic release {written(clauses)} admits")
    return newest[1]


def read_sources(wheel: bytes, digest: str) -> dict[str, str]:
    """The two modules this reads, out of a wheel whose digest is the one PyPI lists."""
    if hashlib.sha256(wheel).hexdigest() != digest:
        raise ValueError("the wheel's digest is not the one PyPI lists")
    sources: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
        for name in (CONSTANTS, MESSAGES):
            try:
                member = archive.open(name)
            except KeyError:
                raise ValueError(f"the wheel holds no {name}") from None
            with member:
                body = member.read(MAX_SOURCE_BYTES + 1)
            if len(body) > MAX_SOURCE_BYTES:
                raise ValueError(f"{name} is larger than {MAX_SOURCE_BYTES:,} bytes")
            sources[name] = body.decode("utf-8")
    return sources


def api_version(constants: str) -> str:
    """The API version `anthropic/_constants.py` sets last, read from its source."""
    value: ast.expr | None = None
    for node in ast.parse(constants).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "ANTHROPIC_API_VERSION"
            for target in node.targets
        ):
            value = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "ANTHROPIC_API_VERSION"
        ):
            value = node.value
    if value is None:
        raise ValueError(f"{CONSTANTS} sets no ANTHROPIC_API_VERSION")
    if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
        raise ValueError(f"{CONSTANTS} does not set ANTHROPIC_API_VERSION to a string")
    if not DATE.fullmatch(value.value):
        raise ValueError(f"{CONSTANTS} sets ANTHROPIC_API_VERSION to no YYYY-MM-DD date")
    return value.value


def missing_keywords(messages: str) -> list[str]:
    """The adapter's keywords that `Messages.create` does not take.

    Only the last definition counts, the one that runs: the `@overload`s before it only type it.
    A keyword-only or ordinary parameter takes a keyword, and `**kwargs` takes them all.
    """
    classes = [
        node
        for node in ast.parse(messages).body
        if isinstance(node, ast.ClassDef) and node.name == "Messages"
    ]
    definitions = [
        item
        for item in (classes[-1].body if classes else [])
        if isinstance(item, ast.FunctionDef) and item.name == "create"
    ]
    if not definitions:
        raise ValueError(f"{MESSAGES} defines no Messages.create")
    arguments = definitions[-1].args
    if arguments.kwarg is not None:
        return []
    taken = {argument.arg for argument in (*arguments.args, *arguments.kwonlyargs)}
    return [keyword for keyword in ADAPTER_KEYWORDS if keyword not in taken]


def issue_title(sent: str, missing: list[str]) -> str:
    if sent != CHECKED_API_VERSION:
        return (
            f"The anthropic SDK sends API version {sent}; "
            f"the adapter was checked against {CHECKED_API_VERSION}"
        )
    return f"The anthropic SDK's messages.create no longer takes {', '.join(missing)}"


def issue_body(release: dict[str, str], clauses: Clauses, sent: str, missing: list[str]) -> str:
    """What moved, and how to check the adapter against it before anything else moves."""
    version = release["version"]
    lines = [
        f"anthropic [{version}]({RELEASES}{version}), the newest release the extra's "
        f"`anthropic{written(clauses)}` admits, changed what the adapter in "
        "`semantic_diff_weaver/providers/anthropic_client.py` relies on:",
        "",
    ]
    if sent != CHECKED_API_VERSION:
        lines.append(
            f"- it sends API version `{sent}`, and the adapter's requests were checked against "
            f"`{CHECKED_API_VERSION}`;"
        )
    if missing:
        names = ", ".join(f"`{keyword}`" for keyword in missing)
        lines.append(f"- its `messages.create` no longer takes {names}, which the adapter passes;")
    lines += [
        "",
        "A request the SDK rejects, or one its API version rejects, fails every call into "
        "deterministic fallback behind a generic warning, so nothing else will show it. Check the "
        "adapter as on 2026-10-25 (`docs/decisions.md`, *Anthropic requests carry no sampling "
        "parameters*): install this release and the extra's floor in scratch environments, run "
        "the adapter against a local stub server that records the `anthropic-version` header and "
        "the body each one sends for every model family, and compare them with what the API's "
        "release notes say the version accepts. Reshape the adapter if it needs it, then move "
        "`CHECKED_API_VERSION` or `ADAPTER_KEYWORDS` in `scripts/check_anthropic_sdk.py`.",
    ]
    server = os.environ.get("GITHUB_SERVER_URL", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    run = os.environ.get("GITHUB_RUN_ID", "")
    if server.startswith("https://") and REPOSITORY.fullmatch(repository) and run.isdecimal():
        url = f"{server}/{repository}/actions/runs/{run}"
        lines += ["", f"Opened by the weekly `anthropic-sdk` workflow, [run {run}]({url})."]
    return "\n".join(lines) + "\n"


def main(
    argv: list[str] | None = None,
    fetch: Fetch = fetch_pypi,
    api: Api = request,
    pyproject: Path = PYPROJECT,
) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--open-issue",
        action="store_true",
        help="when something moved, open an issue in GITHUB_REPOSITORY, once per title",
    )
    arguments = parser.parse_args(argv)
    try:
        clauses = extra_range(pyproject.read_text(encoding="utf-8"))
        release = newest_admitted(json.loads(fetch(INDEX, MAX_INDEX_BYTES)), clauses)
        sources = read_sources(fetch(release["url"], MAX_WHEEL_BYTES), release["sha256"])
        sent = api_version(sources[CONSTANTS])
        missing = missing_keywords(sources[MESSAGES])
    except (
        OSError,
        ValueError,
        SyntaxError,
        RecursionError,
        zipfile.BadZipFile,
        http.client.HTTPException,
    ) as error:
        print(f"Could not check the anthropic SDK: {error}", file=sys.stderr)
        return 2
    version = release["version"]
    if sent == CHECKED_API_VERSION and not missing:
        print(
            f"anthropic {version}, the newest release anthropic{written(clauses)} admits, sends "
            f"API version {sent}, the one the adapter was checked against, and its "
            "messages.create takes every keyword the adapter passes."
        )
        return 0
    if sent != CHECKED_API_VERSION:
        print(
            f"anthropic {version} sends API version {sent}; "
            f"the adapter was checked against {CHECKED_API_VERSION}."
        )
    if missing:
        print(f"anthropic {version}'s messages.create no longer takes {', '.join(missing)}.")
    if not arguments.open_issue:
        return 1
    try:
        status = open_issue(
            api,
            os.environ.get("GITHUB_REPOSITORY", ""),
            issue_title(sent, missing),
            issue_body(release, clauses, sent, missing),
        )
    except (OSError, ValueError, http.client.HTTPException) as error:
        print(f"Could not open the issue: {error}", file=sys.stderr)
        return 1
    print(status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
