"""Repository containment, path filtering, and secret redaction policy."""

from __future__ import annotations

import fnmatch
import os
import re
from collections.abc import Iterable
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .errors import ErrorCode, WeaverError

ALLOWED_ROOTS_ENV = "HERMES_SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS"
CONTROL_PARTS = {".git", ".hg", ".svn", ".bzr"}
CACHE_PARTS = {
    ".cache",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
}
SECRET_NAMES = {
    ".authinfo",
    ".authinfo.gpg",
    ".env",
    ".git-credentials",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "accesstokens.json",
    "application_default_credentials.json",
    "azureprofile.json",
    "credentials",
    "credentials.json",
    "git-credentials",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "secrets.yml",
    "secrets.yaml",
    "service-account.json",
    "service_account.json",
}
SECRET_SUFFIXES = {
    ".cer",
    ".crt",
    ".der",
    ".jks",
    ".key",
    ".keystore",
    ".p12",
    ".p7b",
    ".p7c",
    ".pem",
    ".pfx",
}
SECRET_DIRECTORY_PARTS = {".aws", ".azure", ".gnupg", ".ssh"}
WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
SENSITIVE_ASSIGNMENT = re.compile(
    r"""(?ix)
    (?P<label>
        (?<![A-Za-z0-9])
        [\"']?
        (?:[A-Za-z0-9]+[_-])*
        (?:
            api[_-]?key
            | access[_-]?token
            | auth(?:entication|orization)?[_-]?token
            | authorization
            | client[_-]?secret
            | credential(?:s)?
            | pass(?:word|wd)?
            | private[_-]?key
            | secret
            | token
        )
        (?:[_-][A-Za-z0-9]+)*
        [\"']?
        \s*[:=]\s*
    )
    (?:
        [rubf]*[\"'][^\"'\r\n]+[\"']
        | [^\s,}\]]+
    )
    """
)
CREDENTIAL_URI = re.compile(r"(?i)(?P<label>\b[a-z][a-z0-9+.-]*://[^\s/@:]+:)[^\s/@]+(?=@)")
AUTHORIZATION_VALUE = re.compile(
    r"(?i)(?P<label>\b(?:authorization|proxy-authorization)\b\s*[:=]\s*"
    r"[\"']?(?:basic|bearer)?\s*)[A-Za-z0-9+/_.=-]+"
)
BEARER_VALUE = re.compile(r"(?i)(?P<label>\bbearer\s+)[A-Za-z0-9._~+/=-]+")
REDACTIONS = (
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:.*?-----END [A-Z ]*PRIVATE KEY-----|.*\Z)",
        re.S,
    ),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,})\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:xox[baprs]-[A-Za-z0-9-]{20,}|AIza[0-9A-Za-z_-]{30,})\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
)


def path_error(message: str) -> WeaverError:
    return WeaverError(
        ErrorCode.PATH_OUTSIDE_REPOSITORY,
        message,
        "Use a repository-relative path that remains inside the resolved Git repository.",
    )


def normalize_repo_path(value: str) -> str:
    """Validate an untrusted repository-relative path and normalize it to POSIX form."""
    if not value or "\x00" in value:
        raise path_error("Git returned an empty or invalid repository path.")
    normalized = value.replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise path_error("An absolute or drive-relative repository path was rejected.")
    if ".." in posix.parts or any(part in {"", "."} for part in posix.parts):
        raise path_error("A repository path containing traversal was rejected.")
    if any(part.casefold() in CONTROL_PARTS for part in posix.parts):
        raise path_error("A version-control metadata path was rejected.")
    if os.name == "nt" and any(
        part.rstrip(". ").split(".", 1)[0].upper() in WINDOWS_RESERVED for part in posix.parts
    ):
        raise path_error("A reserved Windows device path was rejected.")
    return posix.as_posix()


def ensure_contained(root: Path, candidate: Path) -> Path:
    """Resolve a path and require it to remain within root (case-aware by platform)."""
    resolved_root = root.resolve(strict=True)
    resolved_candidate = candidate.resolve(strict=True)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise path_error("The resolved path is outside the Git repository.") from exc
    return resolved_candidate


def authorized_roots() -> tuple[Path, ...]:
    """Return host-approved local roots, defaulting securely to the process workspace."""
    configured = os.environ.get(ALLOWED_ROOTS_ENV)
    values = configured.split(os.pathsep) if configured is not None else [str(Path.cwd())]
    roots: list[Path] = []
    for value in values:
        if not value:
            continue
        try:
            root = Path(value).resolve(strict=True)
        except OSError as exc:
            raise path_error("An authorized workspace root is inaccessible.") from exc
        if not root.is_dir() or root == Path(root.anchor):
            raise path_error("An authorized workspace root must be a bounded directory.")
        roots.append(root)
    if not roots:
        raise path_error("No authorized workspace root is configured.")
    return tuple(dict.fromkeys(roots))


def ensure_authorized_path(candidate: Path) -> Path:
    """Resolve a caller-selected path and require host workspace authorization."""
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise path_error("The requested local path is inaccessible.") from exc
    for root in authorized_roots():
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            continue
    raise path_error("The requested local path is outside the authorized workspace roots.")


def exclusion_reason(path: str) -> str | None:
    """Return a mandatory exclusion class without echoing a sensitive filename."""
    parts = [part.casefold() for part in PurePosixPath(path).parts]
    name = parts[-1]
    suffix = PurePosixPath(name).suffix.casefold()
    if any(part in CONTROL_PARTS for part in parts):
        return "control_directory"
    if any(part in CACHE_PARTS for part in parts):
        return "cache_or_environment"
    if any(part in SECRET_DIRECTORY_PARTS for part in parts):
        return "secret_filename"
    if name == "config.json" and ".docker" in parts:
        return "secret_filename"
    if name == "config" and ".kube" in parts:
        return "secret_filename"
    if name == ".env" or name.startswith(".env."):
        return "secret_filename"
    if name in SECRET_NAMES or suffix in SECRET_SUFFIXES:
        return "secret_filename"
    if any(token in name for token in ("credential", "password", "private_key")):
        return "secret_filename"
    if any(token in name for token in ("secret", "token")) and suffix in {
        ".cfg",
        ".ini",
        ".json",
        ".toml",
        ".yaml",
        ".yml",
    }:
        return "secret_filename"
    return None


def glob_matches(path: str, pattern: str) -> bool:
    """Match a repository path using segment-aware ``*`` and recursive ``**`` globs."""
    normalized_path = path.replace("\\", "/")
    normalized_pattern = pattern.replace("\\", "/")
    while normalized_pattern.startswith("./"):
        normalized_pattern = normalized_pattern[2:]
    if not normalized_path or not normalized_pattern:
        return False
    path_parts = tuple(normalized_path.split("/"))
    pattern_parts = tuple(normalized_pattern.split("/"))
    if "" in path_parts or "" in pattern_parts:
        return False

    pending = [(0, 0)]
    visited: set[tuple[int, int]] = set()
    while pending:
        path_index, pattern_index = pending.pop()
        if (path_index, pattern_index) in visited:
            continue
        visited.add((path_index, pattern_index))
        if pattern_index == len(pattern_parts):
            if path_index == len(path_parts):
                return True
            continue
        segment = pattern_parts[pattern_index]
        if segment == "**":
            pending.append((path_index, pattern_index + 1))
            if path_index < len(path_parts):
                pending.append((path_index + 1, pattern_index))
            continue
        if path_index < len(path_parts) and fnmatch.fnmatchcase(path_parts[path_index], segment):
            pending.append((path_index + 1, pattern_index + 1))

    return False


def is_included(path: str, includes: list[str], excludes: list[str]) -> bool:
    return any(glob_matches(path, item) for item in includes) and not any(
        glob_matches(path, item) for item in excludes
    )


def critical_weight(path: str, critical_paths: list[Any], *, default: int = 0) -> int:
    """Return the highest configured critical-path weight matching a repository path."""
    return max(
        (item.weight for item in critical_paths if glob_matches(path, item.pattern)),
        default=default,
    )


def first_exclusion(paths: Iterable[str | None]) -> str | None:
    """Return the first mandatory exclusion reason among candidate paths."""
    for path in paths:
        if path and (reason := exclusion_reason(path)):
            return reason
    return None


def redact_text(text: str, *, max_chars: int = 2000) -> str:
    """Bound and redact obvious credentials before evidence leaves preprocessing."""
    redacted = CREDENTIAL_URI.sub(lambda match: f"{match.group('label')}[REDACTED]", text)
    redacted = AUTHORIZATION_VALUE.sub(lambda match: f"{match.group('label')}[REDACTED]", redacted)
    redacted = BEARER_VALUE.sub(lambda match: f"{match.group('label')}[REDACTED]", redacted)
    redacted = SENSITIVE_ASSIGNMENT.sub(lambda match: f"{match.group('label')}[REDACTED]", redacted)
    for pattern in REDACTIONS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted[:max_chars]
