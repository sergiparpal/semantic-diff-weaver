"""Upsert the review brief as a single pull-request comment.

Idempotency is the whole point: a re-run must *edit* the existing comment, never append a
second one. The comment is identified by a hidden marker on its first line, so the match does
not depend on the comment's author, its position, or any state carried between runs.

The `gh` boundary follows the same rule as `git_diff/process.py`: argument lists only,
`shell=False`, no interpolation of untrusted text into a command string. The brief is passed
through a file, never through the command line, so a hostile repository cannot reach the
shell via its own content.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

MARKER = "<!-- semantic-diff-weaver:v1 -->"

# `repository` and `pull_request` are interpolated into `gh api` resource paths. The `gh`
# boundary is argument-list-only, so this is not a shell-injection surface — but an
# unvalidated value containing `/` or `..` would silently retarget the request at a different
# endpoint, so both are constrained to their documented shapes before use.
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")

# GitHub rejects a comment body above 65,536 characters.
MAX_COMMENT_CHARS = 65_536
# Room for the marker, the truncation notice, and a safety margin.
TRUNCATION_BUDGET = 1_200

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_ARGUMENT_ERROR = 2


class CommentError(Exception):
    """A reportable failure that must not leak a token or a raw provider message."""


def read_markdown(path: Path) -> str:
    """Extract the brief from the CLI's `--format both` envelope."""
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CommentError(f"could not read the analysis envelope at {path.name}") from exc
    if not isinstance(payload, dict):
        raise CommentError("the analysis envelope is not a JSON object")
    if payload.get("success") is False:
        error = str(payload.get("error", "unknown_error"))
        message = str(payload.get("message", ""))
        raise CommentError(f"the analysis failed: {error}: {message}")
    markdown = payload.get("markdown")
    if not isinstance(markdown, str) or not markdown.strip():
        raise CommentError("the analysis envelope carried no Markdown brief")
    return markdown


def truncate(body: str, limit: int = MAX_COMMENT_CHARS) -> str:
    """Cut at a section boundary and say plainly what was dropped.

    Truncating mid-sentence would leave a finding that reads as complete but is not. Cutting
    at a heading keeps every retained section whole, and the notice reports the counts so a
    reader knows to open the full run.
    """
    if len(body) <= limit:
        return body
    budget = max(0, limit - TRUNCATION_BUDGET)
    lines = body.splitlines()
    kept: list[str] = []
    used = 0
    boundary = 0
    for line in lines:
        cost = len(line) + 1
        if used + cost > budget:
            break
        kept.append(line)
        used += cost
        if line.startswith("#") or line.startswith("- **"):
            boundary = len(kept)
    if boundary:
        kept = kept[:boundary]
    dropped_lines = len(lines) - len(kept)
    dropped_findings = sum(1 for line in lines[len(kept) :] if line.startswith("- **"))
    notice = (
        "\n"
        "> **Output truncated.** This comment reached GitHub's 65,536-character limit. "
        f"{dropped_lines} further line(s) were dropped, including {dropped_findings} "
        "finding(s) or obligation(s). Run the analysis locally or read the workflow log for "
        "the complete brief."
    )
    return "\n".join(kept).rstrip() + "\n" + notice


def compose(markdown: str) -> str:
    """Prefix the marker so a later run can find this comment again."""
    return truncate(f"{MARKER}\n{markdown.strip()}\n")


def validated_target(repository: str, pull_request: str) -> tuple[str, str]:
    """Constrain both path components before they are interpolated into a `gh api` path."""
    if not REPOSITORY_RE.match(repository):
        raise CommentError("the repository must be given as 'owner/repo'")
    if not pull_request.isdigit():
        raise CommentError("the pull request must be given as a number")
    return repository, pull_request


def _gh() -> str:
    executable = shutil.which("gh")
    if executable is None:
        raise CommentError("the GitHub CLI ('gh') is not available on PATH")
    return executable


def run_gh(arguments: list[str], *, executable: str | None = None) -> str:
    """Invoke `gh` with an argument list. No shell, no interpolation, bounded output."""
    try:
        completed = subprocess.run(
            [executable or _gh(), *arguments],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CommentError(f"the GitHub CLI failed to run ({type(exc).__name__})") from exc
    if completed.returncode != 0:
        # `gh` echoes the request URL but not the token; still, report only the code.
        raise CommentError(f"the GitHub CLI exited with status {completed.returncode}")
    return completed.stdout


def find_existing(repository: str, pull_request: str, *, runner: Any = None) -> int | None:
    """Return the id of this tool's previous comment, matched by the hidden marker.

    ``runner`` is resolved at call time rather than bound as a default, so the `gh` boundary
    stays injectable — a default argument would capture the function at import.
    """
    runner = runner or run_gh
    raw = runner(
        [
            "api",
            "--paginate",
            f"repos/{repository}/issues/{pull_request}/comments",
            "--jq",
            ".[] | {id: .id, body: .body}",
        ]
    )
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            comment = json.loads(line)
        except json.JSONDecodeError:
            continue
        body = comment.get("body")
        identifier = comment.get("id")
        if isinstance(body, str) and body.startswith(MARKER) and isinstance(identifier, int):
            return identifier
    return None


def upsert(
    repository: str,
    pull_request: str,
    body: str,
    *,
    runner: Any = None,
) -> str:
    """Edit this tool's comment when one exists, otherwise create it. Never append."""
    runner = runner or run_gh
    repository, pull_request = validated_target(repository, pull_request)
    existing = find_existing(repository, pull_request, runner=runner)
    # The body goes through a file: it contains untrusted repository content, and an
    # argument-list value that large is fragile besides.
    handle, name = tempfile.mkstemp(prefix="sdw-comment-", suffix=".md")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(body)
        if existing is None:
            runner(
                [
                    "api",
                    "--method",
                    "POST",
                    f"repos/{repository}/issues/{pull_request}/comments",
                    "-F",
                    f"body=@{name}",
                ]
            )
            return "created"
        runner(
            [
                "api",
                "--method",
                "PATCH",
                f"repos/{repository}/issues/comments/{existing}",
                "-F",
                f"body=@{name}",
            ]
        )
        return "updated"
    finally:
        Path(name).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pr_comment.py",
        description="Upsert the semantic diff brief as one pull-request comment.",
    )
    parser.add_argument("--envelope", required=True, help="path to the CLI's `both` JSON output")
    parser.add_argument("--repository", required=True, help="owner/repo")
    parser.add_argument("--pull-request", required=True, help="pull request number")
    try:
        namespace = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_SUCCESS if exc.code in (0, None) else EXIT_ARGUMENT_ERROR

    try:
        body = compose(read_markdown(Path(namespace.envelope)))
        action = upsert(namespace.repository, namespace.pull_request, body)
    except CommentError as exc:
        print(f"pr_comment: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    print(f"pr_comment: {action} the review comment on #{namespace.pull_request}")
    return EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
