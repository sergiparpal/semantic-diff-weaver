"""Write the review brief to the job summary, the page of the workflow run itself.

The pull-request comment is where a reviewer reads the brief, but a run that cannot comment has
none: a push, which has no pull request, or a fork's pull request, whose token is read-only. The
job summary carries the brief on every run, whether or not it comments. GitHub keeps 1 MiB of
summary a step, so a longer brief is cut at a section boundary, as the comment is, with a notice
of what was dropped.

The summary is a convenience. A run whose summary cannot be written still has its analysis, its
outputs, and its comment, so nothing here fails the step: it warns and exits 0.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# The action runs this from its own checkout, beside pr_comment.py.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.pr_comment import CommentError, read_markdown, truncate  # noqa: E402

# GitHub keeps 1 MiB of summary a step. truncate() counts characters, and one takes at most four
# bytes in UTF-8.
MAX_SUMMARY_BYTES = 1_048_576
MAX_SUMMARY_CHARS = MAX_SUMMARY_BYTES // 4
SUMMARY_LIMIT = "This summary reached the size GitHub keeps of a step's summary."


def summary_of(envelope: Path) -> str:
    """The brief of the CLI's `--format both` envelope, cut to fit a step's summary."""
    body = f"{read_markdown(envelope).strip()}\n"
    return truncate(body, limit=MAX_SUMMARY_CHARS, reached=SUMMARY_LIMIT).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="job_summary.py", description="Write the semantic diff brief to the job summary."
    )
    parser.add_argument("--envelope", required=True, help="path to the CLI's `both` JSON output")
    namespace = parser.parse_args(argv)
    target = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if not target:
        print("job_summary: GITHUB_STEP_SUMMARY is not set, so no summary is written")
        return 0
    try:
        body = summary_of(Path(namespace.envelope))
        # Appended: another step of the job may have written to the same summary.
        with Path(target).open("a", encoding="utf-8") as stream:
            stream.write(body)
    except (CommentError, OSError) as exc:
        print(f"::warning::The brief was not written to the job summary: {exc}")
        return 0
    print("job_summary: wrote the brief to the job summary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
