"""The brief in the job summary: written on every run, cut to what GitHub keeps, never fatal."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import job_summary
from scripts.job_summary import MAX_SUMMARY_CHARS, SUMMARY_LIMIT, main, summary_of

BRIEF = "## Semantic Diff Test Brief\n\n**Overall risk:** medium (51/100)\n"


def _envelope(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "envelope.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_the_brief_is_appended_to_the_job_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    summary = tmp_path / "summary.md"
    # Another step of the job may have written first: that is kept.
    summary.write_text("earlier step\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    envelope = _envelope(tmp_path, {"success": True, "analysis": {}, "markdown": BRIEF})
    assert main(["--envelope", str(envelope)]) == 0
    assert summary.read_text(encoding="utf-8") == "earlier step\n" + BRIEF
    assert "wrote the brief" in capsys.readouterr().out


def test_a_brief_past_what_github_keeps_is_cut_at_a_section_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(job_summary, "MAX_SUMMARY_CHARS", 3_000)
    finding = "- **Finding · boundary_change · medium risk:** something changed.\n  - detail\n"
    envelope = _envelope(tmp_path, {"markdown": "## Brief\n\n" + finding * 200})
    body = summary_of(envelope)
    assert len(body) <= 3_000
    assert f"**Output truncated.** {SUMMARY_LIMIT}" in body
    retained = [line for line in body.splitlines() if line and not line.startswith(">")]
    assert retained[-1].startswith("- **")


def test_the_limit_fits_a_step_s_summary_in_bytes() -> None:
    # A character takes at most four bytes in UTF-8, and GitHub keeps 1 MiB a step.
    assert MAX_SUMMARY_CHARS * 4 <= 1_048_576


def test_without_a_job_summary_nothing_is_written_and_nothing_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    envelope = _envelope(tmp_path, {"markdown": BRIEF})
    assert main(["--envelope", str(envelope)]) == 0
    assert "GITHUB_STEP_SUMMARY is not set" in capsys.readouterr().out


@pytest.mark.parametrize(
    "payload",
    [
        {"success": False, "error": "invalid_ref", "message": "no such revision"},
        {"success": True, "analysis": {}},
        ["not", "an", "object"],
    ],
)
def test_an_envelope_without_a_brief_warns_and_does_not_fail(
    payload: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert main(["--envelope", str(_envelope(tmp_path, payload))]) == 0
    assert capsys.readouterr().out.startswith("::warning::The brief was not written")
    assert not summary.exists()


def test_a_summary_that_cannot_be_written_warns_and_does_not_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A directory where the file should be: opening it to append fails.
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path))
    envelope = _envelope(tmp_path, {"markdown": BRIEF})
    assert main(["--envelope", str(envelope)]) == 0
    assert capsys.readouterr().out.startswith("::warning::The brief was not written")
