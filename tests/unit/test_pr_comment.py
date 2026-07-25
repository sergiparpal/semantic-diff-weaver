"""The pull-request comment upsert, with the `gh` boundary faked.

The behavior that matters is idempotency: a re-run must edit the existing comment rather than
append a second one, and the match must depend only on the hidden marker. No test here makes a
network call or invokes the real `gh`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))

import pr_comment
from pr_comment import (
    EXIT_ARGUMENT_ERROR,
    EXIT_FAILURE,
    EXIT_SUCCESS,
    MARKER,
    MAX_COMMENT_CHARS,
    CommentError,
    compose,
    find_existing,
    main,
    read_markdown,
    truncate,
    upsert,
)

BRIEF = "## Semantic Diff Test Brief\n\n**Overall risk:** medium (51/100)\n"


class FakeGh:
    """Records every invocation and answers `api` list calls from a canned comment set."""

    def __init__(self, comments: list[dict[str, Any]] | None = None) -> None:
        self.comments = comments or []
        self.calls: list[list[str]] = []

    def __call__(self, arguments: list[str], **kwargs: Any) -> str:
        self.calls.append(arguments)
        if "--jq" in arguments:
            return "\n".join(json.dumps(item) for item in self.comments)
        return "{}"

    @property
    def methods(self) -> list[str]:
        return [
            arguments[arguments.index("--method") + 1]
            for arguments in self.calls
            if "--method" in arguments
        ]

    def body_written(self) -> str:
        for arguments in self.calls:
            for item in arguments:
                if item.startswith("body=@"):
                    return Path(item[len("body=@") :]).read_text(encoding="utf-8")
        raise AssertionError("no comment body was written")


class RecordingGh(FakeGh):
    """Captures the body file's contents while it still exists."""

    def __call__(self, arguments: list[str], **kwargs: Any) -> str:
        for item in arguments:
            if item.startswith("body=@"):
                self.captured = Path(item[len("body=@") :]).read_text(encoding="utf-8")
        return super().__call__(arguments, **kwargs)


def _envelope(tmp_path: Path, payload: Any) -> Path:
    path = tmp_path / "envelope.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


def test_the_brief_is_read_from_the_both_envelope(tmp_path: Path) -> None:
    path = _envelope(tmp_path, {"success": True, "analysis": {}, "markdown": BRIEF})
    assert read_markdown(path) == BRIEF


def test_a_failed_analysis_is_reported_not_posted(tmp_path: Path) -> None:
    path = _envelope(
        tmp_path,
        {
            "success": False,
            "error": "invalid_ref",
            "message": "Unknown revision.",
            "remediation": "",
        },
    )
    with pytest.raises(CommentError) as raised:
        read_markdown(path)
    assert "invalid_ref" in str(raised.value)


@pytest.mark.parametrize(
    "payload",
    ["{not json", "[]", '"a string"', json.dumps({"success": True}), json.dumps({"markdown": ""})],
)
def test_a_malformed_envelope_is_reported(tmp_path: Path, payload: str) -> None:
    with pytest.raises(CommentError):
        read_markdown(_envelope(tmp_path, payload))


def test_a_missing_envelope_is_reported(tmp_path: Path) -> None:
    with pytest.raises(CommentError):
        read_markdown(tmp_path / "absent.json")


def test_the_marker_is_the_first_line() -> None:
    body = compose(BRIEF)
    assert body.splitlines()[0] == MARKER
    assert "## Semantic Diff Test Brief" in body


def test_a_comment_is_created_when_none_exists() -> None:
    runner = RecordingGh(comments=[])
    assert upsert("owner/repo", "7", compose(BRIEF), runner=runner) == "created"
    assert runner.methods == ["POST"]
    assert runner.captured.startswith(MARKER)


def test_a_comment_is_updated_when_one_exists() -> None:
    runner = RecordingGh(comments=[{"id": 42, "body": f"{MARKER}\nold brief"}])
    assert upsert("owner/repo", "7", compose(BRIEF), runner=runner) == "updated"
    assert runner.methods == ["PATCH"]
    assert any("issues/comments/42" in item for call in runner.calls for item in call)


def test_a_rerun_edits_rather_than_appends() -> None:
    """The property the whole design exists for."""
    posted = RecordingGh(comments=[])
    upsert("owner/repo", "7", compose(BRIEF), runner=posted)
    identifier = 99
    second = RecordingGh(comments=[{"id": identifier, "body": posted.captured}])
    upsert("owner/repo", "7", compose(BRIEF + "\nmore"), runner=second)
    assert second.methods == ["PATCH"]
    assert "POST" not in second.methods


def test_another_bot_s_comment_is_never_claimed() -> None:
    runner = RecordingGh(
        comments=[
            {"id": 1, "body": "<!-- other-tool:v1 -->\nnot ours"},
            {"id": 2, "body": "a plain human review comment"},
            {"id": 3, "body": f"trailing {MARKER} marker, not first"},
        ]
    )
    assert upsert("owner/repo", "7", compose(BRIEF), runner=runner) == "created"
    assert runner.methods == ["POST"]


def test_the_first_marked_comment_wins() -> None:
    runner = FakeGh(
        comments=[{"id": 5, "body": f"{MARKER}\nfirst"}, {"id": 6, "body": f"{MARKER}\nsecond"}]
    )
    assert find_existing("owner/repo", "7", runner=runner) == 5


def test_malformed_listing_lines_are_skipped() -> None:
    class Broken(FakeGh):
        def __call__(self, arguments: list[str], **kwargs: Any) -> str:
            self.calls.append(arguments)
            return (
                "\n{not json}\n\n"
                f'{{"id": "not-an-int", "body": "{MARKER}"}}\n'
                f'{{"id": 8, "body": "{MARKER}\\nok"}}'
            )

    assert find_existing("owner/repo", "7", runner=Broken()) == 8


def test_no_existing_comment_returns_none() -> None:
    assert find_existing("owner/repo", "7", runner=FakeGh(comments=[])) is None


def test_a_short_body_is_not_truncated() -> None:
    assert truncate(BRIEF) == BRIEF


def test_an_oversized_body_is_cut_at_a_section_boundary() -> None:
    finding = "- **Finding · boundary_change · medium risk:** something changed.\n  - detail line\n"
    body = "## Semantic Diff Test Brief\n\n" + finding * 4000
    assert len(body) > MAX_COMMENT_CHARS
    result = truncate(body)
    assert len(result) <= MAX_COMMENT_CHARS
    assert "Output truncated" in result
    # The last retained content line is a whole section, never a mid-sentence cut.
    retained = [line for line in result.splitlines() if line and not line.startswith(">")]
    assert retained[-1].startswith("- **") or retained[-1].startswith("#")


def test_the_truncation_notice_reports_what_was_dropped() -> None:
    finding = "- **Finding:** x\n"
    body = "## Brief\n" + finding * 5000
    result = truncate(body)
    notice = next(line for line in result.splitlines() if "Output truncated" in line)
    assert "further line(s) were dropped" in notice
    assert "finding(s) or obligation(s)" in notice
    dropped = int(notice.split("limit. ")[1].split(" further")[0])
    assert dropped > 0


def test_truncation_respects_a_custom_limit() -> None:
    body = "## Brief\n" + ("- **Finding:** x\n" * 500)
    result = truncate(body, limit=2000)
    assert len(result) <= 2000


def test_compose_truncates_too() -> None:
    body = compose("## Brief\n" + ("- **Finding:** x\n" * 6000))
    assert len(body) <= MAX_COMMENT_CHARS
    assert body.startswith(MARKER)


def test_main_creates_a_comment(tmp_path: Path, monkeypatch, capsys) -> None:
    runner = RecordingGh(comments=[])
    monkeypatch.setattr(pr_comment, "run_gh", runner)
    path = _envelope(tmp_path, {"success": True, "analysis": {}, "markdown": BRIEF})
    code = main(["--envelope", str(path), "--repository", "owner/repo", "--pull-request", "7"])
    assert code == EXIT_SUCCESS
    assert "created" in capsys.readouterr().out


def test_main_reports_a_failed_analysis(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(pr_comment, "run_gh", FakeGh())
    path = _envelope(tmp_path, {"success": False, "error": "diff_too_large", "message": "big"})
    code = main(["--envelope", str(path), "--repository", "owner/repo", "--pull-request", "7"])
    assert code == EXIT_FAILURE
    assert "diff_too_large" in capsys.readouterr().err


def test_main_reports_a_gh_failure(tmp_path: Path, monkeypatch, capsys) -> None:
    def failing(arguments: list[str], **kwargs: Any) -> str:
        raise CommentError("the GitHub CLI exited with status 403")

    monkeypatch.setattr(pr_comment, "run_gh", failing)
    path = _envelope(tmp_path, {"success": True, "analysis": {}, "markdown": BRIEF})
    code = main(["--envelope", str(path), "--repository", "owner/repo", "--pull-request", "7"])
    assert code == EXIT_FAILURE
    assert "403" in capsys.readouterr().err


def test_missing_arguments_are_an_argument_error(capsys) -> None:
    assert main(["--envelope", "x"]) == EXIT_ARGUMENT_ERROR
    assert "--repository" in capsys.readouterr().err


def test_help_exits_successfully(capsys) -> None:
    assert main(["--help"]) == EXIT_SUCCESS
    assert "--envelope" in capsys.readouterr().out


def test_the_body_never_travels_on_the_command_line() -> None:
    """Untrusted repository content must not be interpolated into an argument."""
    runner = RecordingGh(comments=[])
    hostile = "## Brief\n$(touch /tmp/pwned) `id` && echo injected\n"
    upsert("owner/repo", "7", compose(hostile), runner=runner)
    flattened = " ".join(item for call in runner.calls for item in call)
    assert "touch /tmp/pwned" not in flattened
    assert "injected" not in flattened
    assert "$(" not in flattened
    assert "injected" in runner.captured


def test_the_gh_invocation_is_shell_free(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class Completed:
        returncode = 0
        stdout = "{}"

    def fake_run(arguments: list[str], **kwargs: Any) -> Completed:
        captured["arguments"] = arguments
        captured["kwargs"] = kwargs
        return Completed()

    monkeypatch.setattr(pr_comment.subprocess, "run", fake_run)
    monkeypatch.setattr(pr_comment.shutil, "which", lambda name: "/usr/bin/gh")
    pr_comment.run_gh(["api", "repos/o/r/issues/1/comments"])
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["timeout"] == 60
    assert isinstance(captured["arguments"], list)


def test_a_missing_gh_is_reported(monkeypatch) -> None:
    monkeypatch.setattr(pr_comment.shutil, "which", lambda name: None)
    with pytest.raises(CommentError) as raised:
        pr_comment.run_gh(["api", "x"])
    assert "not available" in str(raised.value)


def test_a_nonzero_gh_status_is_reported_without_echoing_output(monkeypatch) -> None:
    class Completed:
        returncode = 403
        stdout = "token=ghp_SECRETVALUE"
        stderr = "HTTP 403 for https://api.github.com/... token=ghp_SECRETVALUE"

    monkeypatch.setattr(pr_comment.subprocess, "run", lambda *a, **k: Completed())
    monkeypatch.setattr(pr_comment.shutil, "which", lambda name: "/usr/bin/gh")
    with pytest.raises(CommentError) as raised:
        pr_comment.run_gh(["api", "x"])
    assert "403" in str(raised.value)
    assert "ghp_SECRET" not in str(raised.value)


def test_a_subprocess_failure_is_reported(monkeypatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise OSError("exec format error")

    monkeypatch.setattr(pr_comment.subprocess, "run", boom)
    monkeypatch.setattr(pr_comment.shutil, "which", lambda name: "/usr/bin/gh")
    with pytest.raises(CommentError) as raised:
        pr_comment.run_gh(["api", "x"])
    assert "OSError" in str(raised.value)


def test_the_temporary_body_file_is_always_removed() -> None:
    paths: list[Path] = []

    class Tracking(FakeGh):
        def __call__(self, arguments: list[str], **kwargs: Any) -> str:
            for item in arguments:
                if item.startswith("body=@"):
                    paths.append(Path(item[len("body=@") :]))
            return super().__call__(arguments, **kwargs)

    upsert("owner/repo", "7", compose(BRIEF), runner=Tracking(comments=[]))
    assert paths and all(not path.exists() for path in paths)


def test_the_body_file_is_removed_even_when_gh_fails() -> None:
    paths: list[Path] = []

    def failing(arguments: list[str], **kwargs: Any) -> str:
        if "--jq" in arguments:
            return ""
        for item in arguments:
            if item.startswith("body=@"):
                paths.append(Path(item[len("body=@") :]))
        raise CommentError("boom")

    with pytest.raises(CommentError):
        upsert("owner/repo", "7", compose(BRIEF), runner=failing)
    assert paths and all(not path.exists() for path in paths)
