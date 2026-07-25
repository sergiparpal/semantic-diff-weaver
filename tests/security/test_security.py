from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import semantic_diff_weaver.git_diff.repository as repository_module
from semantic_diff_weaver.errors import ErrorCode, WeaverError
from semantic_diff_weaver.git_diff import GitRepository
from semantic_diff_weaver.path_policy import ALLOWED_ROOTS_ENV
from semantic_diff_weaver.plugin import handle_analyze_semantic_diff


def test_inline_and_filename_secrets_never_appear_in_output(repo_factory) -> None:
    old_token = "sk-abcdefghijklmnopqrstuvwxyz123456"
    new_token = "sk-zyxwvutsrqponmlkjihgfedcba654321"
    repo, base, head = repo_factory(
        {"safe.py": f"def token():\n    return '{old_token}'\n", ".env": "PASSWORD=old\n"},
        {"safe.py": f"def token():\n    return '{new_token}'\n", ".env": "PASSWORD=new-secret\n"},
    )
    rendered = handle_analyze_semantic_diff(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head}
    )
    assert old_token not in rendered
    assert new_token not in rendered
    assert "new-secret" not in rendered


def test_contextual_and_uri_credentials_never_reach_output(repo_factory) -> None:
    old_secret = "client-secret-old-123456789"
    new_secret = "client-secret-new-987654321"
    old_password = "database-password-old"
    new_password = "database-password-new"
    repo, base, head = repo_factory(
        {
            "safe.py": (
                "def endpoint():\n"
                f"    CLIENT_SECRET = {old_secret!r}\n"
                f"    return 'postgres://user:{old_password}@db/internal'\n"
            )
        },
        {
            "safe.py": (
                "def endpoint():\n"
                f"    CLIENT_SECRET = {new_secret!r}\n"
                f"    return 'postgres://user:{new_password}@db/internal'\n"
            )
        },
    )
    rendered = handle_analyze_semantic_diff(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head}
    )
    assert all(
        value not in rendered for value in (old_secret, new_secret, old_password, new_password)
    )
    assert "[REDACTED]" in rendered


def test_source_is_not_executed_or_imported(repo_factory, tmp_path: Path) -> None:
    marker = tmp_path / "executed.txt"
    old = f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\ndef f():\n    return 1\n"
    new = f"from pathlib import Path\nPath({str(marker)!r}).write_text('worse')\ndef f():\n    return 2\n"
    repo, base, head = repo_factory({"danger.py": old}, {"danger.py": new})
    result = json.loads(
        handle_analyze_semantic_diff(
            {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}
        )
    )
    assert result["success"] is True
    assert not marker.exists()


def test_unicode_spaces_and_shell_metacharacters_are_data(repo_factory) -> None:
    path = "src/naïve $[value].py"
    repo, base, head = repo_factory(
        {path: "def f(x):\n    return x < 1\n"},
        {path: "def f(x):\n    return x <= 1\n"},
    )
    result = json.loads(
        handle_analyze_semantic_diff(
            {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}
        )
    )
    assert result["success"] is True
    assert result["scope"]["analyzed_files"] == [path]


def test_repository_outside_host_allowlist_is_rejected(
    repo_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, base, head = repo_factory({"a.py": "x = 1\n"}, {"a.py": "x = 2\n"})
    allowed = tmp_path / "different-workspace"
    allowed.mkdir()
    monkeypatch.setenv(ALLOWED_ROOTS_ENV, str(allowed))
    result = json.loads(
        handle_analyze_semantic_diff({"repo_path": str(repo), "base_ref": base, "head_ref": head})
    )
    assert result["success"] is False
    assert result["error"] == "path_outside_repository"


def test_subprocess_timeout_maps_to_safe_error(tmp_path: Path, monkeypatch) -> None:
    repo = GitRepository(tmp_path, "git")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1)

    monkeypatch.setattr(repository_module, "run_bounded_process", timeout)
    with pytest.raises(WeaverError) as caught:
        repo.run(["status"])
    assert caught.value.code is ErrorCode.NOT_A_GIT_REPOSITORY
    assert str(tmp_path) not in caught.value.safe_message


def test_error_transport_does_not_echo_environment(monkeypatch) -> None:
    secret = "do-not-echo-environment-secret"
    monkeypatch.setenv("SENSITIVE_TEST_VALUE", secret)
    result = handle_analyze_semantic_diff({"repo_path": "missing", "base_ref": "HEAD"})
    assert secret not in result
