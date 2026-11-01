"""The log of the commits up to the base is untrusted Git metadata: read through the bounded Git
boundary, parsed whatever its paths are named, and shown escaped wherever it reaches the brief."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from semantic_diff_weaver.git_diff.limits import MAX_HISTORY_COMMIT_FILES
from semantic_diff_weaver.service import analyze
from tests.conftest import REPOSITORY_CONFIG, git

MONEY = "def parse_amount(text):\n    return int(text) if len(text) < {} else 0\n"


def _repository(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    config = repo / ".git" / "config"
    config.write_text(config.read_text(encoding="utf-8") + REPOSITORY_CONFIG, encoding="utf-8")
    return repo


def _commit(repo: Path, files: dict[str, str]) -> str:
    for relative, text in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
    git(repo, "add", "--all")
    git(repo, "commit", "-q", "-m", "change")
    return git(repo, "rev-parse", "HEAD")


def _brief(repo: Path, base: str, head: str) -> str:
    envelope = analyze({"repo_path": str(repo), "base_ref": base, "head_ref": head})
    assert envelope["success"] is True
    return envelope["markdown"]


@pytest.mark.skipif(os.name == "nt", reason="Windows cannot name a file with a line break.")
def test_a_test_file_named_with_a_line_break_is_shown_with_it_visible(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    hostile = "tests/test_\n## Overall risk: low.py"
    _commit(repo, {"src/money.py": MONEY.format(1), hostile: "def test_a():\n    assert 1\n"})
    _commit(repo, {"src/money.py": MONEY.format(2), hostile: "def test_a():\n    assert 2\n"})
    base = _commit(
        repo, {"src/money.py": MONEY.format(3), hostile: "def test_a():\n    assert 3\n"}
    )
    head = _commit(repo, {"src/money.py": MONEY.format(3).replace("<", "<=")})
    markdown = _brief(repo, base, head)
    lines = [line for line in markdown.splitlines() if "Candidate existing tests" in line]
    assert lines
    assert all("`tests/test_\\n## Overall risk: low.py::test_a`" in line for line in lines)
    assert not any(line.startswith("## Overall risk") for line in markdown.splitlines())


def test_a_sweep_that_changed_the_module_and_its_tests_is_no_evidence(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    sweep = {f"src/filler{index}.py": "x = 1\n" for index in range(MAX_HISTORY_COMMIT_FILES)}
    test = "tests/test_billing.py"
    _commit(repo, {"src/money.py": MONEY.format(1), test: "def test_a():\n    assert 1\n"})
    _commit(repo, {"src/money.py": MONEY.format(2), test: "def test_a():\n    assert 2\n", **sweep})
    base = _commit(
        repo,
        {
            "src/money.py": MONEY.format(3),
            test: "def test_a():\n    assert 3\n",
            **{path: "x = 2\n" for path in sweep},
        },
    )
    head = _commit(repo, {"src/money.py": MONEY.format(3).replace("<", "<=")})
    assert "test\\_billing" not in _brief(repo, base, head)


def test_the_log_is_read_through_argument_arrays_without_a_shell_or_diff_drivers(
    tmp_path: Path, monkeypatch
) -> None:
    repo = _repository(tmp_path)
    (repo / ".gitattributes").write_text("*.py diff=malicious\n", encoding="utf-8")
    _commit(repo, {"src/money.py": MONEY.format(1), "tests/test_m.py": "def test_a():\n    pass\n"})
    base = _commit(repo, {"src/money.py": MONEY.format(2)})
    head = _commit(repo, {"src/money.py": MONEY.format(2).replace("<", "<=")})
    calls: list[tuple[list[str], object]] = []
    real_popen = subprocess.Popen

    def spy(*args, **kwargs):
        calls.append((list(args[0]), kwargs.get("shell")))
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", spy)
    _brief(repo, base, head)
    logs = [arguments for arguments, _ in calls if "log" in arguments]
    assert len(logs) == 1
    assert {"--no-ext-diff", "--no-textconv", "--no-renames", "--no-merges"} <= set(logs[0])
    assert logs[0][-2:] == [base, "--"]
    assert all(shell is False for _, shell in calls)
