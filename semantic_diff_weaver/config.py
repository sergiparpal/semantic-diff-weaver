"""Safe configuration loading, precedence, normalization, and validation."""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import yaml
from pydantic import ValidationError

from .errors import ErrorCode, WeaverError
from .models import MAX_PATTERN_CHARS, AnalyzeRequest, WeaverConfig
from .path_policy import ensure_authorized_path, ensure_contained

MAX_CONFIG_BYTES = 256 * 1024
MAX_YAML_EVENTS = 20_000
MAX_YAML_DEPTH = 50
MAX_YAML_ALIASES = 100


def _configuration_error(message: str) -> WeaverError:
    return WeaverError(
        ErrorCode.CONFIGURATION_ERROR,
        message,
        "Correct the YAML configuration or remove the invalid override and retry.",
    )


def _validate_relative_pattern(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _configuration_error(f"{label} must contain non-empty relative patterns.")
    if len(value) > MAX_PATTERN_CHARS:
        raise _configuration_error(
            f"{label} contains a pattern longer than {MAX_PATTERN_CHARS} characters."
        )
    normalized = value.replace("\\", "/")
    windows = PureWindowsPath(value)
    posix = PurePosixPath(normalized)
    if "\x00" in value or windows.is_absolute() or posix.is_absolute() or windows.drive:
        raise _configuration_error(f"{label} contains an absolute or invalid pattern.")
    if ".." in posix.parts or re.match(r"^[A-Za-z]:", value):
        raise _configuration_error(f"{label} may not contain parent traversal or a drive path.")
    return normalized


def _validate_config_paths(data: dict[str, Any]) -> None:
    paths = data.get("paths", {})
    if isinstance(paths, dict):
        for field in ("include", "exclude", "test_roots"):
            if field not in paths:
                continue
            values = paths[field] or []
            if isinstance(values, list):
                paths[field] = [
                    _validate_relative_pattern(value, f"paths.{field}") for value in values
                ]
    for item in data.get("critical_paths", []) or []:
        if isinstance(item, dict) and "pattern" in item:
            item["pattern"] = _validate_relative_pattern(item["pattern"], "critical_paths.pattern")
    for item in data.get("mapping", []) or []:
        if not isinstance(item, dict):
            continue
        if "source" in item:
            item["source"] = _validate_relative_pattern(item["source"], "mapping.source")
        tests = item.get("tests", []) or []
        if isinstance(tests, list):
            item["tests"] = [_validate_relative_pattern(value, "mapping.tests") for value in tests]


def _read_yaml(path: Path, *, containment_root: Path | None = None) -> dict[str, Any]:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise _configuration_error(
            "The configuration file does not exist or is inaccessible."
        ) from exc
    if containment_root is not None:
        resolved = ensure_contained(containment_root, resolved)
    if resolved.suffix.lower() not in {".yaml", ".yml"} or not resolved.is_file():
        raise _configuration_error("Configuration must be a regular .yaml or .yml file.")
    if resolved.stat().st_size > MAX_CONFIG_BYTES:
        raise _configuration_error("The configuration file exceeds the 262144-byte limit.")
    try:
        content = resolved.read_text(encoding="utf-8")
        depth = 0
        aliases = 0
        for event_count, event in enumerate(yaml.parse(content, Loader=yaml.SafeLoader), start=1):
            if event_count > MAX_YAML_EVENTS:
                raise _configuration_error("The YAML configuration exceeds its event limit.")
            if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
                depth += 1
                if depth > MAX_YAML_DEPTH:
                    raise _configuration_error("The YAML configuration exceeds its depth limit.")
            elif isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
                depth -= 1
            elif isinstance(event, yaml.AliasEvent):
                aliases += 1
                if aliases > MAX_YAML_ALIASES:
                    raise _configuration_error("The YAML configuration exceeds its alias limit.")
        loaded = yaml.safe_load(content)
    except WeaverError:
        raise
    except (MemoryError, OSError, RecursionError, UnicodeError, yaml.YAMLError) as exc:
        raise _configuration_error("The configuration file is not valid safe UTF-8 YAML.") from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise _configuration_error("The configuration root must be a mapping.")
    _validate_config_paths(loaded)
    return loaded


def _merge(lower: dict[str, Any], upper: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(lower)
    for key, value in upper.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def load_config(repo_root: Path, request: AnalyzeRequest) -> tuple[WeaverConfig, list[str]]:
    """Load built-ins, repository config, explicit profile, and request overrides."""
    warnings: list[str] = []
    data = WeaverConfig().model_dump(mode="python")
    hermes_path = repo_root / ".hermes" / "semantic-diff-weaver.yaml"
    local_path = repo_root / ".semantic-diff-weaver.yaml"
    if hermes_path.is_file():
        data = _merge(data, _read_yaml(hermes_path, containment_root=repo_root))
        if local_path.is_file():
            warnings.append(
                "Ignored .semantic-diff-weaver.yaml because the .hermes configuration has precedence."
            )
    elif local_path.is_file():
        data = _merge(data, _read_yaml(local_path, containment_root=repo_root))
    if request.risk_profile:
        data = _merge(data, _read_yaml(ensure_authorized_path(Path(request.risk_profile))))
    request_override: dict[str, Any] = {"paths": {}}
    if request.include is not None:
        request_override["paths"]["include"] = [
            _validate_relative_pattern(value, "include") for value in request.include
        ]
    if request.exclude is not None:
        request_override["paths"]["exclude"] = [
            _validate_relative_pattern(value, "exclude") for value in request.exclude
        ]
    if request_override["paths"]:
        data = _merge(data, request_override)
    if data.get("language", {}).get("primary") != "python":
        raise WeaverError(
            ErrorCode.UNSUPPORTED_LANGUAGE,
            "The configured primary language is not supported by this MVP.",
            "Set language.primary to python or remove the unsupported language override.",
        )
    try:
        return WeaverConfig.model_validate(data), warnings
    except ValidationError as exc:
        field = ".".join(str(part) for part in exc.errors()[0].get("loc", ())) or "configuration"
        raise _configuration_error(f"Invalid configuration value at {field}.") from exc
