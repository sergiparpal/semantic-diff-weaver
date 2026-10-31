"""Upsert the review brief as a single pull-request comment, and keep a baseline in it.

Idempotency is the whole point: a re-run must *edit* the existing comment, never append a
second one. The comment is identified by a hidden marker on its first line and by its author,
the identity the action comments as: anyone can post a comment starting with the marker, and
one posted first would have been edited, and failed, in place of the action's own.

The comment also ends with a hidden digest of its analysis — each finding's category, path,
symbol, risk, and a fingerprint of its evidence — which `--read-baseline` reads back on the
next push, so that analysis can say which findings are new since. Only the action's own
comment is read, and the analyzer validates what it holds like any other baseline.

The `gh` boundary follows the same rule as `git_diff/process.py`: argument lists only,
`shell=False`, no interpolation of untrusted text into a command string. The brief is passed
through a file, never through the command line, so a hostile repository cannot reach the
shell via its own content.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path
from typing import Any

# The action runs this from its own checkout, beside the package it installed from it.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from semantic_diff_weaver.baseline import digest_of, load_baseline  # noqa: E402
from semantic_diff_weaver.errors import WeaverError  # noqa: E402

MARKER = "<!-- semantic-diff-weaver:v1 -->"
# The login GitHub gives a comment posted with `github.token`, as the action posts its own.
DEFAULT_AUTHOR = "github-actions[bot]"
# The digest's own line, last in the comment. Base64 holds no `-` or `>`, so it cannot close
# the HTML comment early, and the brief never writes a line starting `<!--`.
DIGEST_RE = re.compile(r"^<!-- semantic-diff-weaver-baseline:v1 ([A-Za-z0-9+/=]+) -->$", re.M)
# How long the encoded digest may be, so the brief keeps most of the comment; a longer one is
# left out, and the next push compares with nothing.
MAX_DIGEST_CHARS = 20_000
# What a digest read back may inflate to: a crafted comment cannot make it a zip bomb.
MAX_DIGEST_BYTES = 2_000_000

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


def read_envelope(path: Path) -> dict[str, Any]:
    """Read the CLI's `--format both` envelope, which carries the brief and the analysis."""
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
    return payload


def read_markdown(path: Path) -> str:
    """Extract the brief from the CLI's `--format both` envelope."""
    markdown = read_envelope(path).get("markdown")
    if not isinstance(markdown, str) or not markdown.strip():
        raise CommentError("the analysis envelope carried no Markdown brief")
    return markdown


def encode_digest(envelope: dict[str, Any]) -> str | None:
    """The digest of the envelope's analysis, compressed for the comment, or None past its limit."""
    try:
        digest = digest_of(envelope.get("analysis"))
    except WeaverError:
        return None
    raw = json.dumps(digest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded = base64.b64encode(zlib.compress(raw, 9)).decode("ascii")
    return encoded if len(encoded) <= MAX_DIGEST_CHARS else None


def decode_digest(encoded: str) -> Any:
    """A digest read back from a comment, inflated no further than `MAX_DIGEST_BYTES`."""
    if len(encoded) > MAX_DIGEST_CHARS:
        raise CommentError("the recorded baseline is longer than a digest may be")
    try:
        inflater = zlib.decompressobj()
        raw = inflater.decompress(base64.b64decode(encoded, validate=True), MAX_DIGEST_BYTES)
        if inflater.unconsumed_tail or not inflater.eof:
            raise CommentError("the recorded baseline inflates past its size limit")
        return json.loads(raw.decode("utf-8"))
    except (binascii.Error, zlib.error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CommentError("the recorded baseline is not readable") from exc


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


def compose(markdown: str, digest: str | None = None) -> str:
    """Prefix the marker so a later run can find this comment again, and end with the digest
    that run compares with. A long brief is cut to keep the digest whole."""
    if digest is None:
        return truncate(f"{MARKER}\n{markdown.strip()}\n")
    line = f"<!-- semantic-diff-weaver-baseline:v1 {digest} -->"
    body = truncate(f"{MARKER}\n{markdown.strip()}\n", limit=MAX_COMMENT_CHARS - len(line) - 2)
    return f"{body.rstrip()}\n{line}\n"


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


def find_comment(
    repository: str, pull_request: str, *, author: str = DEFAULT_AUTHOR, runner: Any = None
) -> tuple[int, str] | None:
    """Return the id and body of this tool's previous comment: the marker's, by `author`.

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
            ".[] | {id: .id, body: .body, author: .user.login}",
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
        if (
            isinstance(body, str)
            and body.startswith(MARKER)
            and isinstance(identifier, int)
            and comment.get("author") == author
        ):
            return identifier, body
    return None


def find_existing(
    repository: str, pull_request: str, *, author: str = DEFAULT_AUTHOR, runner: Any = None
) -> int | None:
    """Return the id of this tool's previous comment, matched by the marker and its author."""
    found = find_comment(repository, pull_request, author=author, runner=runner)
    return found[0] if found is not None else None


def read_baseline(
    repository: str,
    pull_request: str,
    destination: Path,
    *,
    author: str = DEFAULT_AUTHOR,
    runner: Any = None,
) -> bool:
    """Write the digest this tool's previous comment ends with, and say whether there was one.

    It is checked with the analyzer's own loader before it is kept, so a digest that cannot be
    compared — an edited comment, or one from an incompatible release — is reported and left
    out rather than failing the analysis that would read it.
    """
    repository, pull_request = validated_target(repository, pull_request)
    found = find_comment(repository, pull_request, author=author, runner=runner)
    if found is None:
        return False
    recorded = DIGEST_RE.findall(found[1])
    if not recorded:
        return False
    digest = decode_digest(recorded[-1])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(digest), encoding="utf-8")
    try:
        load_baseline(destination, MAX_DIGEST_BYTES)
    except WeaverError as exc:
        destination.unlink(missing_ok=True)
        raise CommentError(f"the recorded baseline cannot be compared: {exc.safe_message}") from exc
    return True


def upsert(
    repository: str,
    pull_request: str,
    body: str,
    *,
    author: str = DEFAULT_AUTHOR,
    runner: Any = None,
) -> str:
    """Edit this tool's comment when one exists, otherwise create it. Never append."""
    runner = runner or run_gh
    repository, pull_request = validated_target(repository, pull_request)
    existing = find_existing(repository, pull_request, author=author, runner=runner)
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
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--envelope", help="path to the CLI's `both` JSON output, to post")
    mode.add_argument(
        "--read-baseline",
        metavar="PATH",
        help="write the digest the previous comment ends with to PATH, when there is one",
    )
    parser.add_argument("--repository", required=True, help="owner/repo")
    parser.add_argument("--pull-request", required=True, help="pull request number")
    parser.add_argument(
        "--author",
        default=DEFAULT_AUTHOR,
        help=f"the login this tool comments as (default: {DEFAULT_AUTHOR})",
    )
    try:
        namespace = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_SUCCESS if exc.code in (0, None) else EXIT_ARGUMENT_ERROR

    if namespace.read_baseline is not None:
        # Comparing with the previous push is a convenience: without it the analysis is still
        # whole, so nothing here fails the run.
        try:
            recorded = read_baseline(
                namespace.repository,
                namespace.pull_request,
                Path(namespace.read_baseline),
                author=namespace.author,
            )
        except CommentError as exc:
            print(f"pr_comment: no baseline is compared: {exc}", file=sys.stderr)
            return EXIT_SUCCESS
        print(
            "pr_comment: the previous analysis is compared"
            if recorded
            else "pr_comment: no previous analysis was recorded on this pull request"
        )
        return EXIT_SUCCESS

    try:
        envelope_path = Path(namespace.envelope)
        body = compose(read_markdown(envelope_path), encode_digest(read_envelope(envelope_path)))
        action = upsert(namespace.repository, namespace.pull_request, body, author=namespace.author)
    except CommentError as exc:
        print(f"pr_comment: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    print(f"pr_comment: {action} the review comment on #{namespace.pull_request}")
    return EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
