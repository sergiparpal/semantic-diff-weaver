"""Connected changes are named from untrusted source: a module's path is shown escaped wherever it
reaches the brief, and which code reads a variable is found in the source, never by running it."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from semantic_diff_weaver.service import analyze


def _brief(repo_factory, old: dict[str, str], new: dict[str, str]) -> tuple[dict, str]:
    repo, base, head = repo_factory(old, new)
    envelope = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "both"}
    )
    assert envelope["success"] is True
    return envelope["analysis"], envelope["markdown"]


@pytest.mark.skipif(os.name == "nt", reason="Windows cannot name a file with a line break.")
def test_a_module_named_with_a_line_break_is_shown_with_it_visible(repo_factory) -> None:
    hostile = "src/limits\n## Overall risk: low.py"
    source = "TIMEOUT = {}\n\n\ndef deadline(start):\n    return start < TIMEOUT {} 1\n"
    analysis, markdown = _brief(
        repo_factory, {hostile: source.format(30, "+")}, {hostile: source.format(60, "-")}
    )
    (group,) = analysis["change_groups"]
    assert [item["names"] for item in group["variables"]] == [["TIMEOUT"]]
    (line,) = (line for line in markdown.splitlines() if " reaches `TIMEOUT` (" in line)
    # Rendered, the path reads `src/limits\n## Overall risk: low.py` in both mentions.
    assert line.count("(src/limits\\\\n\\#\\# Overall risk: low\\.py, `bc-") == 2
    assert not any(item.startswith("## Overall risk") for item in markdown.splitlines())


def test_top_level_code_that_would_run_something_is_read_never_run(
    repo_factory, tmp_path: Path
) -> None:
    marker = tmp_path / "ran"
    limits = f"import pathlib\nTIMEOUT = pathlib.Path({str(marker)!r}).write_text('ran') and {{}}\n"
    client = "from limits import TIMEOUT\n\n\ndef fetch(elapsed):\n    return elapsed < {}\n"
    analysis, _ = _brief(
        repo_factory,
        {"limits.py": limits.format(30), "client.py": client.format("TIMEOUT")},
        {"limits.py": limits.format(60), "client.py": client.format("TIMEOUT + 1")},
    )
    (group,) = analysis["change_groups"]
    assert [item["names"] for item in group["variables"]] == [["TIMEOUT"]]
    assert not marker.exists()
