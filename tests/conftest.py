from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from hermes_semantic_diff_weaver.path_policy import ALLOWED_ROOTS_ENV

# Identity supplied through the environment so repositories need no `git config` child processes.
GIT_ENVIRONMENT = {
    "GIT_AUTHOR_NAME": "Semantic Diff Tests",
    "GIT_AUTHOR_EMAIL": "tests@example.invalid",
    "GIT_COMMITTER_NAME": "Semantic Diff Tests",
    "GIT_COMMITTER_EMAIL": "tests@example.invalid",
}
# Written straight into the repository config so the analyzer's own Git calls inherit it too.
REPOSITORY_CONFIG = "[core]\n\tautocrlf = false\n"


@pytest.fixture(autouse=True)
def authorized_test_roots(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Authorize the workspace plus the whole pytest temp root, including shared corpora."""
    roots = os.pathsep.join(
        (str(Path.cwd().resolve()), str(tmp_path_factory.getbasetemp().resolve()))
    )
    monkeypatch.setenv(ALLOWED_ROOTS_ENV, roots)


def git(repo: Path, *arguments: str) -> str:
    executable = shutil.which("git")
    if executable is None:
        pytest.skip("Git is unavailable")
    completed = subprocess.run(
        [executable, *arguments],
        cwd=repo,
        capture_output=True,
        check=True,
        encoding="utf-8",
        errors="strict",
        shell=False,
        env={**os.environ, **GIT_ENVIRONMENT},
    )
    return completed.stdout.strip()


def _repository_creator(root: Path) -> Callable[..., tuple[Path, str, str]]:
    counter = 0

    def create(
        old_files: dict[str, str],
        new_files: dict[str, str],
        *,
        remove: tuple[str, ...] = (),
    ) -> tuple[Path, str, str]:
        nonlocal counter
        counter += 1
        repo = root / f"repo-{counter}"
        repo.mkdir()
        git(repo, "init", "-q")
        config = repo / ".git" / "config"
        config.write_text(config.read_text(encoding="utf-8") + REPOSITORY_CONFIG, encoding="utf-8")
        for relative, text in old_files.items():
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="")
        git(repo, "add", "--all")
        git(repo, "commit", "-q", "-m", "base")
        base = git(repo, "rev-parse", "HEAD")
        for relative in remove:
            path = repo / relative
            if path.exists():
                path.unlink()
        for relative, text in new_files.items():
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="")
        git(repo, "add", "--all")
        git(repo, "commit", "-q", "-m", "head")
        head = git(repo, "rev-parse", "HEAD")
        return repo, base, head

    return create


@pytest.fixture
def repo_factory(tmp_path: Path) -> Callable[..., tuple[Path, str, str]]:
    return _repository_creator(tmp_path)


@pytest.fixture(scope="module")
def module_repo_factory(
    tmp_path_factory: pytest.TempPathFactory,
) -> Callable[..., tuple[Path, str, str]]:
    """Build repositories once per module for corpus suites that would otherwise rebuild them."""
    return _repository_creator(tmp_path_factory.mktemp("corpus"))
