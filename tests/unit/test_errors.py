from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

import semantic_diff_weaver.service as service
from semantic_diff_weaver.errors import (
    ErrorCode,
    WeaverError,
    as_public_error,
    internal_error,
)
from semantic_diff_weaver.models import AnalyzeRequest
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
        "coverage_unreadable",
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


def test_public_error_mapping_covers_every_failure_class() -> None:
    """`as_public_error` is shared by the Hermes handler and the command line.

    The `ValidationError` arm is defensive: the service converts request-shaped problems to
    a `WeaverError` before they get here, so it guards a *result* that fails validation.
    """
    typed = WeaverError(ErrorCode.DIFF_TOO_LARGE, "Too large.", "Narrow the range.")
    assert as_public_error(typed) == typed.as_dict()

    with pytest.raises(ValidationError) as raised:
        AnalyzeRequest.model_validate({"repo_path": "."})
    mapped = as_public_error(raised.value)
    assert mapped["success"] is False
    assert mapped["error"] == "configuration_error"
    assert "schema validation" in mapped["message"]
    assert "base_ref" not in json.dumps(mapped)

    opaque = as_public_error(RuntimeError("secret body"))
    assert opaque["error"] == "internal_error"
    assert "secret body" not in json.dumps(opaque)

    assert as_public_error(KeyboardInterrupt())["error"] == "internal_error"
