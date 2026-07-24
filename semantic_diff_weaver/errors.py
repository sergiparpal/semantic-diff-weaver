"""Stable public errors and safe exception conversion."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    NOT_A_GIT_REPOSITORY = "not_a_git_repository"
    INVALID_REF = "invalid_ref"
    PATH_OUTSIDE_REPOSITORY = "path_outside_repository"
    UNSUPPORTED_LANGUAGE = "unsupported_language"
    DIFF_TOO_LARGE = "diff_too_large"
    PARSE_FAILURE = "parse_failure"
    LLM_UNAVAILABLE = "llm_unavailable"
    LLM_SCHEMA_FAILURE = "llm_schema_failure"
    CONFIGURATION_ERROR = "configuration_error"
    INTERNAL_ERROR = "internal_error"


class WeaverError(Exception):
    """An expected, safe-to-report analysis failure."""

    def __init__(self, code: ErrorCode, message: str, remediation: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.remediation = remediation

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": False,
            "error": self.code.value,
            "message": self.safe_message,
            "remediation": self.remediation,
        }


def internal_error() -> WeaverError:
    """Return a deliberately opaque public error for unexpected failures."""
    return WeaverError(
        ErrorCode.INTERNAL_ERROR,
        "The semantic diff analysis failed unexpectedly.",
        "Retry with a narrower diff and enable Hermes plugin debug logs for stage-level details.",
    )
