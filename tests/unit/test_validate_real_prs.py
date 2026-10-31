"""`scripts/validate_real_prs.py` samples each merged pull request once, squashed or merged."""

from __future__ import annotations

from pathlib import Path

from scripts import validate_real_prs
from tests.conftest import git


def _commit(repo: Path, name: str, message: str) -> str:
    (repo / name).write_text(f"{name.upper().replace('.PY', '')} = 1\n", encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def _history(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A default branch with a squash merge, a merge commit, and a branch merged into it.

    `#3` is merged into `stable`, which `#4` then merges into `main`: the old harness took every
    two-parent merge reachable from `main` and so reported `#3` on its own and again inside `#4`.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    shas = {"initial": _commit(repo, "a.py", "Initial commit")}
    shas["squash"] = _commit(repo, "b.py", "Fix the value (#1)")
    shas["direct"] = _commit(repo, "c.py", "Tidy without a pull request")
    git(repo, "switch", "-q", "-c", "feature")
    shas["feature"] = _commit(repo, "d.py", "Add d")
    git(repo, "switch", "-q", "main")
    _commit(repo, "e.py", "Advance main")
    git(repo, "merge", "-q", "--no-ff", "-m", "Merge pull request #2 from owner/feature", "feature")
    shas["merge"] = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-q", "-c", "stable")
    git(repo, "switch", "-q", "-c", "fix")
    _commit(repo, "f.py", "Fix f")
    git(repo, "switch", "-q", "stable")
    git(repo, "merge", "-q", "--no-ff", "-m", "Fix f (#3)", "fix")
    shas["stable"] = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-q", "main")
    _commit(repo, "g.py", "Advance main again")
    git(repo, "merge", "-q", "--no-ff", "-m", "Merge stable into main (#4)", "stable")
    return repo, shas


def test_each_pull_request_is_sampled_once_with_its_own_range(tmp_path: Path) -> None:
    repo, shas = _history(tmp_path)

    sampled = validate_real_prs.pull_requests(repo, 10)

    assert [(number, base, head) for base, head, number, _ in sampled] == [
        ("4", shas["merge"], shas["stable"]),
        ("2", shas["direct"], shas["feature"]),
        ("1", shas["initial"], shas["squash"]),
    ]


def test_sampling_stops_at_the_count(tmp_path: Path) -> None:
    repo, _ = _history(tmp_path)

    assert [number for _, _, number, _ in validate_real_prs.pull_requests(repo, 2)] == ["4", "2"]
