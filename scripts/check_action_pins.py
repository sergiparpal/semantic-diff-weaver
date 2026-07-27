"""Enforce the repository's GitHub Actions pinning policy across workflows and documentation.

Dependabot rewrites `uses:` lines under `.github/workflows/`, but it never reads the YAML
embedded in prose. Every pin repeated in `README.md` or `docs/` therefore drifts silently the
moment an upstream release lands, and a reader copying the example pins an older commit than
the one this repository actually runs. This script closes that gap by treating documentation
as a first-class source of pins.

The default mode is offline and deterministic so it can gate every pull request:

  * third-party actions must be pinned to a full 40-character commit SHA, because a tag is
    mutable and `pr-review.yml` grants `pull-requests: write`;
  * every pin must carry a trailing `# vX.Y.Z` comment naming the release it points at;
  * an action referenced in more than one place must use the same SHA and version everywhere,
    which is what catches documentation left behind by a Dependabot bump;
  * this project's own action is referenced by tag rather than SHA, and every example must
    name the same tag.

`--verify-remote` and `--check-latest` add network checks for the two failures the offline
rules cannot see: a version comment that lies about its SHA, and a doc-only action that no
workflow pins and so quietly falls behind. They are meant for the scheduled run, where a
transient API failure costs nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

API_ROOT = "https://api.github.com"
OWN_ACTION = "sergiparpal/semantic-diff-weaver"
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

# Workflows and the composite action are scanned in full; Markdown contributes only its
# fenced ```yaml blocks. Prose mentions a placeholder pin — `docs/release-checklist.md` names
# `@vX.Y.Z` when describing the release step — and those are documentation about the policy
# rather than an instance of it.
YAML_SOURCES = ("action.yml", ".github/workflows/*.yml", ".github/workflows/*.yaml")
MARKDOWN_SOURCES = ("README.md", "docs/*.md")

USES_PATTERN = re.compile(
    r"^\s*(?:-\s+)?uses:\s*(?P<ref>\S+)(?:\s+#\s*(?P<comment>.+?))?\s*$",
)
FENCE_PATTERN = re.compile(r"^\s*```\s*(?P<language>[A-Za-z0-9_-]*)\s*$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
VERSION_PATTERN = re.compile(r"^v\d+\.\d+\.\d+$")


@dataclass(frozen=True)
class Reference:
    """A single `uses:` occurrence, located precisely enough to report back to the author."""

    path: str
    line: int
    action: str
    ref: str
    version: str | None

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}"


def _iter_yaml_lines(text: str) -> list[tuple[int, str]]:
    return list(enumerate(text.splitlines(), start=1))


def _iter_fenced_yaml_lines(text: str) -> list[tuple[int, str]]:
    """Return only the lines inside ```yaml fences, keeping their original line numbers."""
    lines: list[tuple[int, str]] = []
    language: str | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        fence = FENCE_PATTERN.match(line)
        if fence is not None:
            # A fence either opens a block (recording its language) or closes the open one.
            language = None if language is not None else fence.group("language").lower()
            continue
        if language in {"yaml", "yml"}:
            lines.append((number, line))
    return lines


def _parse(path: Path, root: Path, *, fenced: bool) -> list[Reference]:
    text = path.read_text(encoding="utf-8")
    lines = _iter_fenced_yaml_lines(text) if fenced else _iter_yaml_lines(text)
    references: list[Reference] = []
    for number, line in lines:
        match = USES_PATTERN.match(line)
        if match is None:
            continue
        ref = match.group("ref")
        # Local references (`./`) resolve to the checked-out tree and have nothing to pin.
        if ref.startswith((".", "docker://")):
            continue
        action, separator, revision = ref.partition("@")
        references.append(
            Reference(
                path=path.relative_to(root).as_posix(),
                line=number,
                action=action,
                ref=revision if separator else "",
                version=match.group("comment"),
            )
        )
    return references


def collect_references(root: Path) -> list[Reference]:
    """Gather every pinned action reference from workflows, the action, and the documentation."""
    references: list[Reference] = []
    for pattern in YAML_SOURCES:
        for path in sorted(root.glob(pattern)):
            references.extend(_parse(path, root, fenced=False))
    for pattern in MARKDOWN_SOURCES:
        for path in sorted(root.glob(pattern)):
            references.extend(_parse(path, root, fenced=True))
    return references


def check_policy(references: list[Reference]) -> list[str]:
    """Apply the offline pinning rules and return one message per violation."""
    failures: list[str] = []
    for reference in references:
        if reference.action == OWN_ACTION:
            if not VERSION_PATTERN.match(reference.ref):
                failures.append(
                    f"{reference.location}: {OWN_ACTION} must be referenced by an immutable "
                    f"release tag such as v1.2.3, found {reference.ref!r}"
                )
            continue
        if not SHA_PATTERN.match(reference.ref):
            failures.append(
                f"{reference.location}: {reference.action} must be pinned to a full 40-character "
                f"commit SHA, found {reference.ref!r}"
            )
        if reference.version is None:
            failures.append(
                f"{reference.location}: {reference.action} is missing the trailing "
                f"'# vX.Y.Z' comment naming the pinned release"
            )
        elif not VERSION_PATTERN.match(reference.version):
            failures.append(
                f"{reference.location}: {reference.action} has an unreadable version comment "
                f"{reference.version!r}, expected the form 'vX.Y.Z'"
            )
    return failures


def check_consistency(references: list[Reference]) -> list[str]:
    """Require every occurrence of an action to agree, which is what catches doc drift."""
    grouped: dict[str, list[Reference]] = defaultdict(list)
    for reference in references:
        grouped[reference.action].append(reference)
    failures: list[str] = []
    for action, occurrences in sorted(grouped.items()):
        pins = {(item.ref, item.version) for item in occurrences}
        if len(pins) == 1:
            continue
        detail = ", ".join(
            f"{item.location} -> {item.ref[:12] or '(none)'} ({item.version or 'no version'})"
            for item in occurrences
        )
        failures.append(
            f"{action} is pinned inconsistently; every workflow and documentation example must "
            f"name the same commit: {detail}"
        )
    return failures


def _request(url: str, token: str | None) -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "semantic-diff-weaver-pin-check",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if not url.startswith(API_ROOT):
        raise ValueError(f"refusing to request a non-GitHub URL: {url}")
    request = urllib.request.Request(url, headers=headers)  # noqa: S310 - checked against API_ROOT
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - see above
        return json.loads(response.read().decode("utf-8"))


def _resolve_tag(action: str, version: str, token: str | None) -> str:
    """Return the commit SHA a tag points at, dereferencing annotated tags."""
    reference = _request(f"{API_ROOT}/repos/{action}/git/ref/tags/{version}", token)
    target = reference["object"]
    if target["type"] == "tag":
        return str(
            _request(f"{API_ROOT}/repos/{action}/git/tags/{target['sha']}", token)["object"]["sha"]
        )
    return str(target["sha"])


def verify_remote(references: list[Reference], token: str | None) -> list[str]:
    """Confirm each version comment names the tag that really points at the pinned SHA."""
    failures: list[str] = []
    seen: set[tuple[str, str]] = set()
    for reference in references:
        if reference.version is None or reference.action == OWN_ACTION:
            continue
        key = (reference.action, reference.version)
        if key in seen:
            continue
        seen.add(key)
        try:
            resolved = _resolve_tag(reference.action, reference.version, token)
        except (urllib.error.URLError, KeyError, ValueError) as error:
            failures.append(
                f"{reference.location}: could not resolve {reference.action}@{reference.version}: "
                f"{error}"
            )
            continue
        if resolved != reference.ref:
            failures.append(
                f"{reference.location}: {reference.action} claims {reference.version} but that tag "
                f"points at {resolved}, not the pinned {reference.ref}"
            )
    return failures


def verify_own_tags(references: list[Reference], token: str | None) -> list[str]:
    """Confirm the release tag advertised in the examples was actually published."""
    failures: list[str] = []
    for version in sorted({item.ref for item in references if item.action == OWN_ACTION}):
        try:
            _resolve_tag(OWN_ACTION, version, token)
        except (urllib.error.URLError, KeyError, ValueError) as error:
            failures.append(
                f"the documentation advertises {OWN_ACTION}@{version}, which does not resolve to a "
                f"published tag: {error}"
            )
    return failures


def check_latest(references: list[Reference], token: str | None) -> list[str]:
    """Report pins that upstream has moved past.

    Only the scheduled run calls this. Failing a pull request because an unrelated project cut
    a release would make every branch red for reasons its author cannot fix.
    """
    findings: list[str] = []
    seen: set[str] = set()
    for reference in references:
        if reference.action == OWN_ACTION or reference.action in seen:
            continue
        seen.add(reference.action)
        try:
            latest = str(
                _request(f"{API_ROOT}/repos/{reference.action}/releases/latest", token)["tag_name"]
            )
        except (urllib.error.URLError, KeyError, ValueError) as error:
            findings.append(f"could not read the latest release of {reference.action}: {error}")
            continue
        if reference.version is not None and latest != reference.version:
            findings.append(
                f"{reference.action} is pinned to {reference.version} but {latest} is available "
                f"(referenced at {reference.location})"
            )
    return findings


def _report(title: str, failures: list[str]) -> None:
    print(f"{title}:", file=sys.stderr)
    for failure in failures:
        print(f"- {failure}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root",
        type=Path,
        default=REPOSITORY_ROOT,
        help="repository root to scan (defaults to this checkout)",
    )
    parser.add_argument(
        "--verify-remote",
        action="store_true",
        help="resolve every version comment against the GitHub API",
    )
    parser.add_argument(
        "--check-latest",
        action="store_true",
        help="report pins that a newer upstream release has moved past",
    )
    arguments = parser.parse_args(argv)

    references = collect_references(arguments.root)
    if not references:
        print("No action references found; the scan patterns are stale.", file=sys.stderr)
        return 2

    failures = check_policy(references) + check_consistency(references)
    if failures:
        _report("Action pinning policy failed", failures)
        return 1

    token = os.environ.get("GITHUB_TOKEN")
    if arguments.verify_remote:
        remote = verify_remote(references, token) + verify_own_tags(references, token)
        if remote:
            _report("Pinned SHAs disagree with their version comments", remote)
            return 1

    if arguments.check_latest:
        outdated = check_latest(references, token)
        if outdated:
            _report("Pinned actions are behind their latest release", outdated)
            return 1

    actions = len({item.action for item in references})
    print(
        f"Action pinning policy passed: {len(references)} references across {actions} actions "
        f"are pinned, commented, and consistent between the workflows and the documentation."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
