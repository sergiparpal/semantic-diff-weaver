"""A GitHub Release's notes, written from the version's section of CHANGELOG.md."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import release_notes

CHANGELOG = """# Changelog

## Unreleased

- **Fix:** not released yet.

## 1.2.0 - 2026-11-10

A summary that wraps
over two lines.

- **Fix:** an item that wraps
  over two lines.
- **Tests:** an item with a list:
  - a nested item that
    wraps;
  - another.

  A paragraph that continues the item.


- **Docs:** after two blank lines.

## 1.1.0 - 2026-11-09

Older.
"""

NOTES = """Released on 2026-11-10.

A summary that wraps over two lines.

- **Fix:** an item that wraps over two lines.
- **Tests:** an item with a list:
  - a nested item that wraps;
  - another.

  A paragraph that continues the item.

- **Docs:** after two blank lines.

The wheel and source distribution attached were built from this tag's tree and checked by \
`scripts/verify_wheel.py`.
"""


def test_each_paragraph_and_item_goes_on_one_line() -> None:
    assert release_notes.notes(CHANGELOG, "1.2.0") == NOTES


def test_a_release_with_nothing_attached_leaves_out_the_assets_sentence() -> None:
    assert release_notes.notes(CHANGELOG, "1.1.0", assets=False) == (
        "Released on 2026-11-09.\n\nOlder.\n"
    )


def test_a_version_without_a_dated_section_is_refused() -> None:
    with pytest.raises(release_notes.NotesError, match=r"1\.3\.0"):
        release_notes.notes(CHANGELOG, "1.3.0")
    with pytest.raises(release_notes.NotesError, match="Unreleased"):
        release_notes.notes(CHANGELOG, "Unreleased")


def test_the_command_writes_the_notes_for_a_tag(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")
    out = tmp_path / "notes.md"

    assert release_notes.main(["v1.2.0", "--changelog", str(changelog), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == NOTES


def test_the_command_prints_without_the_assets_sentence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")

    assert release_notes.main(["1.1.0", "--changelog", str(changelog), "--no-assets"]) == 0
    assert capsys.readouterr().out == "Released on 2026-11-09.\n\nOlder.\n"


def test_the_command_says_why_it_wrote_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")

    assert release_notes.main(["9.9.9", "--changelog", str(changelog)]) == 2
    assert release_notes.main(["1.2.0", "--changelog", str(tmp_path / "missing.md")]) == 2
    assert "no section for 9.9.9" in capsys.readouterr().err


def test_the_repository_changelog_has_notes_for_its_latest_release() -> None:
    text = release_notes.CHANGELOG.read_text(encoding="utf-8")
    version = next(
        match["version"]
        for line in text.splitlines()
        if (match := release_notes.HEADING.match(line))
    )

    written = release_notes.notes(text, version)
    assert written.startswith("Released on ")
    assert written.endswith(release_notes.ASSETS + "\n")
