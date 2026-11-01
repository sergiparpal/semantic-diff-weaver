"""Adversarial regressions for the MCP transport's authorization rule.

Under MCP a model chooses `repo_path` and every other path either tool takes, so the server
takes its bound from the host — the project directory Claude Code exports — and never from a
tool argument. Every test here pins
that: a path outside the project is refused, an operator's narrower bound is never widened
by the project directory, and refusal arrives as a tool error the model cannot argue past.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from semantic_diff_weaver.mcp_server import (
    PROJECT_DIR_ENV,
    SKELETONS_TOOL_NAME,
    TOOL_NAME,
    McpServer,
    _enter_project,
)
from semantic_diff_weaver.path_policy import ALLOWED_ROOTS_ENV

BOUNDARY_OLD = {"api.py": "def allowed(x):\n    return x < 5\n"}
BOUNDARY_NEW = {"api.py": "def allowed(x):\n    return x <= 5\n"}


def _call(arguments: dict[str, Any]) -> dict[str, Any]:
    response = McpServer().handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": TOOL_NAME, "arguments": arguments},
        }
    )
    assert response is not None
    return response["result"]


@pytest.fixture
def two_repositories(repo_factory) -> tuple[tuple[Path, str, str], tuple[Path, str, str]]:
    return repo_factory(BOUNDARY_OLD, BOUNDARY_NEW), repo_factory(BOUNDARY_OLD, BOUNDARY_NEW)


def test_a_repository_outside_the_project_is_refused(two_repositories, monkeypatch) -> None:
    (project, base, head), (other, _, _) = two_repositories
    monkeypatch.delenv(ALLOWED_ROOTS_ENV, raising=False)
    monkeypatch.chdir(Path.cwd())
    _enter_project({PROJECT_DIR_ENV: str(project)})

    inside = _call({"repo_path": ".", "base_ref": base, "head_ref": head})
    assert inside["isError"] is False

    for escape in (str(other), str(Path("..") / other.name), str(project.parent)):
        result = _call({"repo_path": escape, "base_ref": base, "head_ref": head})
        assert result["isError"] is True
        payload = json.loads(result["content"][0]["text"])
        assert payload["error"] == "path_outside_repository"
        assert str(other) not in payload["message"]
        # The refusal names the operator's remedy rather than "use a repository-relative path",
        # which cannot help when the repository itself is outside the workspace.
        assert ALLOWED_ROOTS_ENV in payload["remediation"]
        assert "repository-relative" not in payload["remediation"]


def test_an_external_risk_profile_outside_the_project_is_refused(
    two_repositories, monkeypatch, tmp_path
) -> None:
    (project, base, head), _ = two_repositories
    profile = tmp_path / "outside.yaml"
    profile.write_text("rules: {}\n", encoding="utf-8")
    monkeypatch.delenv(ALLOWED_ROOTS_ENV, raising=False)
    monkeypatch.chdir(Path.cwd())
    _enter_project({PROJECT_DIR_ENV: str(project)})
    result = _call(
        {"repo_path": ".", "base_ref": base, "head_ref": head, "risk_profile": str(profile)}
    )
    assert result["isError"] is True


def test_the_project_dir_never_widens_an_operator_bound(
    two_repositories, monkeypatch, tmp_path
) -> None:
    """The operator's variable wins even when the host's project lies outside it."""
    (project, base, head), _ = two_repositories
    elsewhere = tmp_path / "operator-bound"
    elsewhere.mkdir()
    monkeypatch.setenv(ALLOWED_ROOTS_ENV, str(elsewhere))
    monkeypatch.chdir(Path.cwd())
    _enter_project({PROJECT_DIR_ENV: str(project)})
    result = _call({"repo_path": ".", "base_ref": base, "head_ref": head})
    assert result["isError"] is True
    assert json.loads(result["content"][0]["text"])["error"] == "path_outside_repository"


def test_tool_arguments_cannot_name_authorization_roots(two_repositories, monkeypatch) -> None:
    """The schema forbids extra properties, so no argument can smuggle in a root."""
    (project, base, head), (other, _, _) = two_repositories
    monkeypatch.delenv(ALLOWED_ROOTS_ENV, raising=False)
    monkeypatch.chdir(Path.cwd())
    _enter_project({PROJECT_DIR_ENV: str(project)})
    result = _call(
        {
            "repo_path": str(other),
            "base_ref": base,
            "head_ref": head,
            "allowed_roots": [str(other)],
        }
    )
    assert result["isError"] is True


def _scaffold(arguments: dict[str, Any]) -> dict[str, Any]:
    response = McpServer().handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": SKELETONS_TOOL_NAME, "arguments": arguments},
        }
    )
    assert response is not None
    return response["result"]


def _error_code(result: dict[str, Any]) -> str:
    assert result["isError"] is True
    return str(json.loads(result["content"][0]["text"])["error"])


def test_the_skeleton_tool_keeps_to_the_project_too(
    two_repositories, monkeypatch, tmp_path
) -> None:
    """Each path the skeleton tool takes is authorized as the analysis tool's paths are."""
    (project, base, head), (other, _, _) = two_repositories
    saved = tmp_path / "analysis.json"
    saved.write_text("{}", encoding="utf-8")
    profile = tmp_path / "outside.yaml"
    profile.write_text("rules: {}\n", encoding="utf-8")
    monkeypatch.delenv(ALLOWED_ROOTS_ENV, raising=False)
    monkeypatch.chdir(Path.cwd())
    _enter_project({PROJECT_DIR_ENV: str(project)})
    ids = {"obligation_ids": ["to-001"]}

    inside = _scaffold({"repo_path": ".", "base_ref": base, "head_ref": head, **ids})
    assert inside["isError"] is False

    for arguments in (
        {"repo_path": str(other), "base_ref": base, "head_ref": head},
        {"repo_path": ".", "analysis_path": str(saved)},
        {"repo_path": ".", "base_ref": base, "head_ref": head, "risk_profile": str(profile)},
    ):
        assert _error_code(_scaffold({**arguments, **ids})) == "path_outside_repository"
    # No argument can name a root for this tool either: the request model forbids extra fields.
    smuggled = {"repo_path": str(other), "base_ref": base, "allowed_roots": [str(other)], **ids}
    assert _error_code(_scaffold(smuggled)) == "configuration_error"
