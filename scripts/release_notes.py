"""Write a GitHub Release's notes from the version's section of CHANGELOG.md.

The notes are what every Release since v0.15.0 carries: a `Released on DATE.` line, the section
with each paragraph and each list item on one line, since GitHub keeps a body's line breaks, and a
closing sentence about the attached wheel and source distribution:

    python scripts/release_notes.py 0.20.0                  # print the notes
    python scripts/release_notes.py 0.20.0 --out notes.md   # or write them to a file
    python scripts/release_notes.py 0.3.0 --no-assets       # for a Release with nothing attached

A nested list item keeps its indentation, and so does a paragraph that continues a list item after
a blank line. `scripts/check_release.py` compares a published Release with these notes. Exit codes:
0 written, 2 no such version or no changelog.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parents[1] / "CHANGELOG.md"
HEADING = re.compile(r"^## (?P<version>\S+) - (?P<date>\d{4}-\d{2}-\d{2})$")
SECTION_END = re.compile(r"^## ")
LIST_ITEM = re.compile(r"^(?P<indent> *)(?P<marker>[-*]|\d+\.) (?P<text>.*)$")
ASSETS = (
    "The wheel and source distribution attached were built from this tag's tree and checked by "
    "`scripts/verify_wheel.py`."
)


class NotesError(Exception):
    """The changelog has no section for the version."""


def section(text: str, version: str) -> tuple[str, list[str]]:
    """The version's release date and the lines of its section, without its heading."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = HEADING.match(line)
        if match and match["version"] == version:
            end = next(
                (at for at in range(index + 1, len(lines)) if SECTION_END.match(lines[at])),
                len(lines),
            )
            return match["date"], lines[index + 1 : end]
    raise NotesError(f"CHANGELOG.md has no section for {version}")


def unwrap(lines: list[str]) -> list[str]:
    """Each paragraph and each list item on one line, blocks apart as the section sets them.

    A block's first line keeps its indentation and list marker, and its wrapped lines join it with
    one space. A blank line ends a block; one blank line stays between blocks, however many the
    section has.
    """
    blocks: list[str] = []
    current: list[str] = []

    def close() -> None:
        if current:
            blocks.append(" ".join(current))
            current.clear()

    for line in lines:
        if not line.strip():
            close()
            if blocks and blocks[-1]:
                blocks.append("")
            continue
        item = LIST_ITEM.match(line)
        if item:
            close()
            current.append(f"{item['indent']}{item['marker']} {item['text'].strip()}")
        elif current:
            current.append(line.strip())
        else:
            indent = line[: len(line) - len(line.lstrip(" "))]
            current.append(f"{indent}{line.strip()}")
    close()
    while blocks and not blocks[-1]:
        blocks.pop()
    while blocks and not blocks[0]:
        blocks.pop(0)
    return blocks


def notes(text: str, version: str, *, assets: bool = True) -> str:
    """The Release notes for `version`, from the changelog's `text`."""
    date, lines = section(text, version)
    body = [f"Released on {date}.", "", *unwrap(lines)]
    if assets:
        body += ["", ASSETS]
    return "\n".join(body) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("version", help="the release, as `0.20.0` or `v0.20.0`")
    parser.add_argument("--changelog", type=Path, default=CHANGELOG)
    parser.add_argument("--out", type=Path, help="write the notes here instead of printing them")
    parser.add_argument(
        "--no-assets",
        action="store_true",
        help="leave out the sentence about the attached wheel and source distribution",
    )
    arguments = parser.parse_args(argv)
    version = arguments.version.removeprefix("v")
    try:
        text = arguments.changelog.read_text(encoding="utf-8")
        written = notes(text, version, assets=not arguments.no_assets)
    except (OSError, NotesError) as error:
        print(f"release_notes: {error}", file=sys.stderr)
        return 2
    if arguments.out is None:
        sys.stdout.write(written)
    else:
        arguments.out.write_text(written, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
