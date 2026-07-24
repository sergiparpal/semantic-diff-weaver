from __future__ import annotations

import json

import pytest

import semantic_diff_weaver.service as service
from semantic_diff_weaver.errors import ErrorCode, WeaverError, internal_error
from semantic_diff_weaver.plugin import handle_analyze_semantic_diff


def test_every_public_error_code_is_stable() -> None:
    assert {item.value for item in ErrorCode} == {
        "not_a_git_repository",
        "invalid_ref",
        "path_outside_repository",
        "unsupported_language",
        "diff_too_large",
        "parse_failure",
        "llm_unavailable",
        "llm_schema_failure",
        "configuration_error",
        "internal_error",
    }


def test_safe_error_shape() -> None:
    error = WeaverError(ErrorCode.INVALID_REF, "Invalid ref.", "Use a commit.")
    assert error.as_dict() == {
        "success": False,
        "error": "invalid_ref",
        "message": "Invalid ref.",
        "remediation": "Use a commit.",
    }
    assert "unexpected" in internal_error().safe_message


@pytest.mark.parametrize("code", list(ErrorCode))
def test_handler_preserves_every_typed_public_error(code: ErrorCode, monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "analyze",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            WeaverError(code, "Safe bounded failure.", "Retry safely.")
        ),
    )
    result = json.loads(handle_analyze_semantic_diff({"repo_path": ".", "base_ref": "HEAD"}))
    assert result == {
        "success": False,
        "error": code.value,
        "message": "Safe bounded failure.",
        "remediation": "Retry safely.",
    }


def test_handler_rejects_invalid_arguments_as_json() -> None:
    result = json.loads(handle_analyze_semantic_diff({"repo_path": "."}))
    assert result["success"] is False
    assert result["error"] == "configuration_error"


def test_unexpected_handler_failure_is_opaque_json(monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "analyze",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("secret body")),
    )
    result = json.loads(handle_analyze_semantic_diff({"repo_path": ".", "base_ref": "HEAD"}))
    assert result["error"] == "internal_error"
    assert "secret body" not in result["message"]
