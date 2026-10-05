from __future__ import annotations

from pathlib import Path

import pytest

import semantic_diff_weaver.path_policy as path_policy
from semantic_diff_weaver.errors import ErrorCode, WeaverError
from semantic_diff_weaver.path_policy import (
    ALLOWED_ROOTS_ENV,
    ensure_authorized_path,
    ensure_contained,
    exclusion_reason,
    glob_matches,
    normalize_repo_path,
    redact_text,
)


@pytest.mark.parametrize(
    "path",
    ["../escape.py", "/etc/passwd", "C:\\outside\\file.py", ".git/config", "nul\x00.py"],
)
def test_untrusted_paths_are_rejected(path: str) -> None:
    with pytest.raises(WeaverError):
        normalize_repo_path(path)


def test_paths_normalize_and_globs_match_root() -> None:
    assert normalize_repo_path("src\\api.py") == "src/api.py"
    assert normalize_repo_path("line\nbreak.py") == "line\nbreak.py"
    assert glob_matches("api.py", "**/*.py")
    assert glob_matches("src/api.py", "**/*.py")


def test_globs_preserve_hidden_segments_and_do_not_cross_directories() -> None:
    assert glob_matches(".hidden.py", ".hidden.py")
    assert glob_matches(".github/workflows/check.py", ".github/**/*.py")
    assert glob_matches("src/api.py", "src/*.py")
    assert not glob_matches("src/nested/api.py", "src/*.py")
    assert glob_matches("src/nested/api.py", "src/**/*.py")
    assert glob_matches("/".join(["segment"] * 1500) + "/api.py", "**/*.py")


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        (".env.production", "secret_filename"),
        ("keys/id_rsa", "secret_filename"),
        ("certs/client.pem", "secret_filename"),
        (".venv/lib.py", "cache_or_environment"),
        (".git/config", "control_directory"),
        ("config/access_token.json", "secret_filename"),
        ("config/passwords.toml", "secret_filename"),
        (".git-credentials", "secret_filename"),
        (".docker/config.json", "secret_filename"),
        (".kube/config", "secret_filename"),
        (".ssh/config", "secret_filename"),
        ("certs/client.crt", "secret_filename"),
        ("cloud/service-account.json", "secret_filename"),
    ],
)
def test_mandatory_exclusions(path: str, reason: str) -> None:
    assert exclusion_reason(path) == reason


def test_inline_secrets_are_redacted_and_bounded() -> None:
    source = (
        "api_key = 'abcdefghijklmnopqrstuvwxyz123456'\n"
        "CLIENT_SECRET = 'client-secret-value-1234'\n"
        "AWS_SECRET_ACCESS_KEY = 'aws-secret-value-5678'\n"
        "PASSWORD = 'abc'\n"
        "database_url = 'postgres://user:database-password@db/internal'\n"
        "Authorization: Bearer authorization-token-9012\n"
        "Proxy-Authorization: Bearer xyz\n"
        "sk-abcdefghijklmnopqrstuvwxyz"
    )
    redacted = redact_text(source, max_chars=1000)
    assert "abcdefghijklmnopqrstuvwxyz" not in redacted
    assert "client-secret-value" not in redacted
    assert "aws-secret-value" not in redacted
    assert "abc" not in redacted
    assert "database-password" not in redacted
    assert "authorization-token" not in redacted
    assert "xyz" not in redacted
    assert "[REDACTED]" in redacted
    assert len(redact_text("x" * 100, max_chars=12)) == 12


def test_incomplete_private_key_is_redacted_before_truncation() -> None:
    text = "prefix -----BEGIN PRIVATE KEY-----\nsecret-material-without-end-marker"
    redacted = redact_text(text, max_chars=50)
    assert "secret-material" not in redacted
    assert "PRIVATE KEY" not in redacted
    assert "[REDACTED]" in redacted


def test_resolved_containment_accepts_inside_and_rejects_outside(tmp_path: Path) -> None:
    inside = tmp_path / "inside.py"
    inside.write_text("x = 1\n", encoding="utf-8")
    assert ensure_contained(tmp_path, inside) == inside.resolve()
    outside = tmp_path.parent / "outside.py"
    outside.write_text("x = 2\n", encoding="utf-8")
    with pytest.raises(WeaverError):
        ensure_contained(tmp_path, outside)


def test_caller_selected_paths_require_host_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    inside = allowed / "profile.yaml"
    inside.write_text("version: 1\n", encoding="utf-8")
    outside = tmp_path / "outside.yaml"
    outside.write_text("version: 1\n", encoding="utf-8")
    monkeypatch.setenv(ALLOWED_ROOTS_ENV, str(allowed))
    assert ensure_authorized_path(inside) == inside.resolve()
    with pytest.raises(WeaverError):
        ensure_authorized_path(outside)


def test_invalid_authorized_root_configuration_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for configured in (
        path_policy.os.pathsep,
        str(Path(tmp_path.anchor)),
        str(tmp_path / "missing-workspace-root"),
    ):
        monkeypatch.setenv(ALLOWED_ROOTS_ENV, configured)
        with pytest.raises(WeaverError):
            path_policy.authorized_roots()


def test_non_secret_token_source_and_windows_devices_are_handled(
    monkeypatch,
) -> None:
    assert exclusion_reason("src/token.py") is None
    monkeypatch.setattr(path_policy.os, "name", "nt")
    with pytest.raises(WeaverError):
        normalize_repo_path("CON.py")


def _raised(action) -> WeaverError:
    with pytest.raises(WeaverError) as raised:
        action()
    return raised.value


@pytest.mark.parametrize(
    ("configured", "remedy"),
    [
        pytest.param(lambda tmp: path_policy.os.pathsep, "at least one directory", id="no-root"),
        pytest.param(
            lambda tmp: str(Path(tmp.anchor)), "never a file or a filesystem root", id="fs-root"
        ),
        pytest.param(
            lambda tmp: str(tmp / "missing"), "existing, readable directory", id="missing"
        ),
    ],
)
def test_a_bad_root_configuration_says_how_to_fix_the_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured, remedy: str
) -> None:
    monkeypatch.setenv(ALLOWED_ROOTS_ENV, configured(tmp_path))
    error = _raised(path_policy.authorized_roots)
    assert error.code is ErrorCode.PATH_OUTSIDE_REPOSITORY
    assert ALLOWED_ROOTS_ENV in error.remediation
    assert remedy in error.remediation


def test_a_path_outside_the_roots_names_the_operator_remedy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refusing a repository outside the workspace used to say "use a repository-relative path".

    That cannot help: the repository itself is outside. The remedies are a path inside the
    workspace, or an operator widening it — the error code is unchanged.
    """
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv(ALLOWED_ROOTS_ENV, str(allowed))
    error = _raised(lambda: ensure_authorized_path(tmp_path))
    assert error.code is ErrorCode.PATH_OUTSIDE_REPOSITORY
    assert error.remediation == path_policy.OUTSIDE_ROOTS_REMEDIATION
    assert ALLOWED_ROOTS_ENV in error.remediation


def test_a_missing_path_says_to_check_that_it_exists(tmp_path: Path) -> None:
    error = _raised(lambda: ensure_authorized_path(tmp_path / "missing"))
    assert error.remediation == path_policy.INACCESSIBLE_PATH_REMEDIATION


def test_repository_path_errors_keep_the_repository_remedy(tmp_path: Path) -> None:
    inner = tmp_path / "inner"
    inner.mkdir()
    for action in (
        lambda: normalize_repo_path("../escape.py"),
        lambda: ensure_contained(inner, tmp_path),
    ):
        assert _raised(action).remediation == path_policy.REPOSITORY_PATH_REMEDIATION


def test_every_path_remediation_is_a_single_line() -> None:
    """The CLI prints a public error as exactly two lines: message, then remediation."""
    for name in dir(path_policy):
        if name.endswith("_REMEDIATION"):
            assert "\n" not in getattr(path_policy, name)
