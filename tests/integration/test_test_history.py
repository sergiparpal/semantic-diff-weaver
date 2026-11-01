"""A test file that kept changing with a module in the commits up to the base is its candidate."""

from __future__ import annotations

from pathlib import Path

import semantic_diff_weaver.git_diff.limits as git_limits
from semantic_diff_weaver.service import analyze
from tests.conftest import REPOSITORY_CONFIG, git

MONEY = "def parse_amount(text):\n    return int(text) if len(text) < {} else 0\n"
BILLING = "def test_invoice_total():\n    assert {}\n"


class History:
    """A repository built commit by commit."""

    def __init__(self, root: Path) -> None:
        self.repo = root / "repo"
        self.repo.mkdir(parents=True)
        git(self.repo, "init", "-q")
        config = self.repo / ".git" / "config"
        config.write_text(config.read_text(encoding="utf-8") + REPOSITORY_CONFIG, encoding="utf-8")

    def commit(self, files: dict[str, str]) -> str:
        for relative, text in files.items():
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="")
        git(self.repo, "add", "--all")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "change")
        return git(self.repo, "rev-parse", "HEAD")

    def candidates(self, base: str, head: str) -> tuple[dict[str, list[str]], list[str]]:
        envelope = analyze(
            {
                "repo_path": str(self.repo),
                "base_ref": base,
                "head_ref": head,
                "output_format": "json",
            }
        )
        found: dict[str, list[str]] = {}
        for obligation in envelope["test_obligations"]:
            for test in obligation["candidate_existing_tests"]:
                found[test["path"]] = test["match_reasons"]
        return found, envelope["warnings"]


def _start(history: History, config: str = "") -> None:
    history.commit(
        {
            "src/shop/__init__.py": "",
            "src/shop/money.py": MONEY.format(10),
            "tests/test_billing.py": BILLING.format(10),
            "tests/test_other.py": BILLING.format(0),
            **({".semantic-diff-weaver.yaml": config} if config else {}),
        }
    )


def _rework(history: History, version: int) -> str:
    return history.commit(
        {
            "src/shop/money.py": MONEY.format(version),
            "tests/test_billing.py": BILLING.format(version),
        }
    )


def _change(history: History) -> str:
    return history.commit({"src/shop/money.py": MONEY.format(99).replace("<", "<=")})


def test_a_test_file_that_changed_with_the_module_before_the_base_is_its_candidate(
    tmp_path: Path,
) -> None:
    history = History(tmp_path)
    _start(history)
    _rework(history, 11)
    base = _rework(history, 12)
    head = _change(history)
    found, warnings = history.candidates(base, head)
    # The first commit changed both too, and `test_other.py` only there: a coincidence.
    assert found == {
        "tests/test_billing.py": ["changed with src/shop/money.py in 3 of its last 3 commits"]
    }
    assert warnings == []


def test_the_change_under_review_is_no_evidence_about_itself(tmp_path: Path) -> None:
    history = History(tmp_path)
    _start(history)
    base = history.commit({"tests/test_other.py": BILLING.format(1)})
    _rework(history, 11)
    _rework(history, 12)
    head = _change(history)
    found, _ = history.candidates(base, head)
    assert "tests/test_billing.py" not in found


def test_how_many_commits_are_read_is_configured(tmp_path: Path) -> None:
    for depth, expected in ((0, False), (1, False), (2, True)):
        history = History(tmp_path / f"depth-{depth}")
        _start(history, f"version: 1\nrules:\n  max_history_commits: {depth}\n")
        _rework(history, 11)
        base = _rework(history, 12)
        head = _change(history)
        found, _ = history.candidates(base, head)
        assert ("tests/test_billing.py" in found) is expected, depth


def test_a_history_past_its_bound_is_a_warning_not_a_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(git_limits, "MAX_HISTORY_BYTES", 16)
    history = History(tmp_path)
    _start(history)
    _rework(history, 11)
    base = _rework(history, 12)
    head = _change(history)
    found, warnings = history.candidates(base, head)
    assert "tests/test_billing.py" not in found
    assert (
        "The commits up to the base could not be read whole; tests that changed with the code "
        "there may be missing."
    ) in warnings
