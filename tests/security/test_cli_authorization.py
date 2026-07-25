"""Adversarial regressions for the command line's authorization rule.

The CLI populates `ALLOWED_ROOTS_ENV` from `--repo` because a human typed it. Every test
here pins the boundary of that concession: an operator's existing bound is never widened,
a filesystem root never authorizes anything, and containment still holds for configuration
files and symlinks.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from semantic_diff_weaver.cli import EXIT_ANALYSIS_ERROR, EXIT_ARGUMENT_ERROR, EXIT_SUCCESS, main
from semantic_diff_weaver.path_policy import ALLOWED_ROOTS_ENV

BOUNDARY_OLD = {"api.py": "def allowed(x):\n    return x < 5\n"}
BOUNDARY_NEW = {"api.py": "def allowed(x):\n    return x <= 5\n"}


def test_an_operator_set_bound_is_never_widened_by_allow_root(
    repo_factory, capsys, monkeypatch, tmp_path
) -> None:
    repo, base, head = repo_factory(BOUNDARY_OLD, BOUNDARY_NEW)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv(ALLOWED_ROOTS_ENV, str(elsewhere))
    code = main(
        [
            "--repo",
            str(repo),
            "--base",
            base,
            "--head",
            head,
            "--allow-root",
            str(repo),
        ]
    )
    captured = capsys.readouterr()
    assert code == EXIT_ARGUMENT_ERROR
    assert "cannot widen" in captured.err
    assert captured.out == ""
    # The operator's bound is left exactly as they set it.
    import os

    assert os.environ[ALLOWED_ROOTS_ENV] == str(elsewhere)


def test_an_operator_set_bound_is_never_widened_by_repo(
    repo_factory, capsys, monkeypatch, tmp_path
) -> None:
    """A `--repo` outside the operator's bound fails exactly as it does under Hermes."""
    repo, base, head = repo_factory(BOUNDARY_OLD, BOUNDARY_NEW)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv(ALLOWED_ROOTS_ENV, str(elsewhere))
    code = main(["--repo", str(repo), "--base", base, "--head", head])
    captured = capsys.readouterr()
    assert code == EXIT_ANALYSIS_ERROR
    assert "path_outside_repository" in captured.err
    assert captured.out == ""


def test_a_filesystem_root_is_refused_as_an_authorization_root(
    repo_factory, capsys, monkeypatch
) -> None:
    repo, base, head = repo_factory(BOUNDARY_OLD, BOUNDARY_NEW)
    monkeypatch.delenv(ALLOWED_ROOTS_ENV, raising=False)
    filesystem_root = str(Path(Path(repo).resolve().anchor))
    code = main(
        [
            "--repo",
            str(repo),
            "--base",
            base,
            "--head",
            head,
            "--allow-root",
            filesystem_root,
        ]
    )
    captured = capsys.readouterr()
    assert code == EXIT_ARGUMENT_ERROR
    assert "filesystem root" in captured.err
    assert captured.out == ""


def test_a_filesystem_root_as_repo_is_refused(capsys, monkeypatch) -> None:
    monkeypatch.delenv(ALLOWED_ROOTS_ENV, raising=False)
    filesystem_root = str(Path(Path.cwd().resolve().anchor))
    code = main(["--repo", filesystem_root, "--base", "HEAD"])
    captured = capsys.readouterr()
    assert code == EXIT_ARGUMENT_ERROR
    assert "filesystem root" in captured.err


def test_risk_profile_outside_the_authorized_roots_is_refused(
    repo_factory, capsys, monkeypatch, tmp_path
) -> None:
    repo, base, head = repo_factory(BOUNDARY_OLD, BOUNDARY_NEW)
    outside = tmp_path / "outside"
    outside.mkdir()
    profile = outside / "profile.yaml"
    profile.write_text("version: 1\n", encoding="utf-8")
    monkeypatch.delenv(ALLOWED_ROOTS_ENV, raising=False)
    code = main(
        [
            "--repo",
            str(repo),
            "--base",
            base,
            "--head",
            head,
            "--risk-profile",
            str(profile),
        ]
    )
    captured = capsys.readouterr()
    assert code == EXIT_ANALYSIS_ERROR
    assert "path_outside_repository" in captured.err
    assert captured.out == ""


def test_allow_root_admits_an_explicitly_authorized_profile(
    repo_factory, capsys, monkeypatch, tmp_path
) -> None:
    """The escape hatch works, and only when the operator names the directory."""
    repo, base, head = repo_factory(BOUNDARY_OLD, BOUNDARY_NEW)
    outside = tmp_path / "profiles"
    outside.mkdir()
    profile = outside / "profile.yaml"
    profile.write_text(
        "version: 1\ncritical_paths:\n  - pattern: 'api.py'\n    weight: 100\n",
        encoding="utf-8",
    )
    monkeypatch.delenv(ALLOWED_ROOTS_ENV, raising=False)
    code = main(
        [
            "--repo",
            str(repo),
            "--base",
            base,
            "--head",
            head,
            "--format",
            "json",
            "--risk-profile",
            str(profile),
            "--allow-root",
            str(outside),
        ]
    )
    assert code == EXIT_SUCCESS
    assert json.loads(capsys.readouterr().out)["success"] is True


def test_symlink_escape_from_inside_the_repository_is_refused(
    repo_factory, capsys, monkeypatch, tmp_path
) -> None:
    repo, base, head = repo_factory(BOUNDARY_OLD, BOUNDARY_NEW)
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "profile.yaml"
    secret.write_text("version: 1\n", encoding="utf-8")
    link = Path(repo) / "link.yaml"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):  # pragma: no cover - unprivileged Windows
        pytest.skip("symlink creation is unavailable on this platform")
    monkeypatch.delenv(ALLOWED_ROOTS_ENV, raising=False)
    code = main(
        [
            "--repo",
            str(repo),
            "--base",
            base,
            "--head",
            head,
            "--risk-profile",
            str(link),
        ]
    )
    captured = capsys.readouterr()
    assert code == EXIT_ANALYSIS_ERROR
    assert "path_outside_repository" in captured.err
    assert captured.out == ""


def test_the_cli_restores_the_environment_it_did_not_own(repo_factory, monkeypatch, capsys) -> None:
    """A variable the CLI set for one analysis must not leak into the next process step."""
    import os

    repo, base, head = repo_factory(BOUNDARY_OLD, BOUNDARY_NEW)
    monkeypatch.delenv(ALLOWED_ROOTS_ENV, raising=False)
    assert main(["--repo", str(repo), "--base", base, "--head", head]) == EXIT_SUCCESS
    capsys.readouterr()
    assert ALLOWED_ROOTS_ENV not in os.environ


def test_a_nonexistent_repo_is_an_argument_error(capsys, monkeypatch, tmp_path) -> None:
    monkeypatch.delenv(ALLOWED_ROOTS_ENV, raising=False)
    code = main(["--repo", str(tmp_path / "absent"), "--base", "HEAD"])
    assert code == EXIT_ARGUMENT_ERROR
    assert "not an accessible path" in capsys.readouterr().err


def test_a_file_is_refused_as_a_repository_root(repo_factory, capsys, monkeypatch) -> None:
    repo, base, head = repo_factory(BOUNDARY_OLD, BOUNDARY_NEW)
    monkeypatch.delenv(ALLOWED_ROOTS_ENV, raising=False)
    code = main(["--repo", str(Path(repo) / "api.py"), "--base", base, "--head", head])
    assert code == EXIT_ARGUMENT_ERROR
    assert "not a directory" in capsys.readouterr().err


@pytest.mark.parametrize("configured", ["", os.pathsep, os.pathsep * 3])
def test_an_empty_operator_bound_names_the_variable_that_caused_it(
    repo_factory, capsys, monkeypatch, configured: str
) -> None:
    """A set-but-empty bound authorizes nothing, and must say so.

    Falling through to `authorized_roots` reported "no authorized workspace root is
    configured" — accurate, but it points at the invocation rather than at the variable that
    actually emptied the bound. The bound is still never widened from `--repo`.
    """
    repo, base, head = repo_factory(BOUNDARY_OLD, BOUNDARY_NEW)
    monkeypatch.setenv(ALLOWED_ROOTS_ENV, configured)

    code = main(["--repo", str(repo), "--base", base, "--head", head])

    captured = capsys.readouterr()
    assert code == EXIT_ARGUMENT_ERROR
    assert ALLOWED_ROOTS_ENV in captured.err
    assert "set but empty" in captured.err
    assert captured.out == ""
    assert os.environ[ALLOWED_ROOTS_ENV] == configured
