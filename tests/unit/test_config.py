from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from semantic_diff_weaver.config import _read_yaml, _validate_config_paths, load_config
from semantic_diff_weaver.errors import ErrorCode, WeaverError
from semantic_diff_weaver.models import AnalyzeRequest
from semantic_diff_weaver.path_policy import is_included


def request(repo: Path, **kwargs: object) -> AnalyzeRequest:
    return AnalyzeRequest(repo_path=str(repo), base_ref="HEAD", **kwargs)


def test_repository_precedence_and_request_override(tmp_path: Path) -> None:
    (tmp_path / ".hermes").mkdir()
    (tmp_path / ".hermes" / "semantic-diff-weaver.yaml").write_text(
        "version: 1\nrules:\n  max_changed_files: 7\n", encoding="utf-8"
    )
    (tmp_path / ".semantic-diff-weaver.yaml").write_text(
        "version: 1\nrules:\n  max_changed_files: 9\n", encoding="utf-8"
    )
    config, warnings = load_config(tmp_path, request(tmp_path, include=["src/**/*.py"], exclude=[]))
    assert config.rules.max_changed_files == 7
    assert config.paths.include == ["src/**/*.py"]
    assert config.paths.exclude == []
    assert warnings


@pytest.mark.parametrize(
    "content",
    [
        "- not-a-mapping\n",
        "!!python/object/apply:os.system ['whoami']\n",
        "version: 2\n",
        "version: 1\nunknown: true\n",
        "version: 1\nrules:\n  max_diff_lines: -1\n",
        "version: 1\nverdict:\n  warn_at: severe\n",
        "version: 1\nverdict:\n  warn_at: high\n  review_required_at: medium\n",
        "version: 1\nmapping:\n  - source: src/**\n    tests: [tests/**]\n  - source: src/**\n    tests: [tests/unit/**]\n",
    ],
)
def test_invalid_yaml_and_values_are_rejected(tmp_path: Path, content: str) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text(content, encoding="utf-8")
    with pytest.raises(WeaverError) as caught:
        load_config(tmp_path, request(tmp_path, risk_profile=str(profile)))
    assert caught.value.code is ErrorCode.CONFIGURATION_ERROR


@pytest.mark.parametrize("pattern", ["../secret.py", "/absolute/*.py", "C:\\outside\\*.py"])
def test_unsafe_globs_are_rejected(tmp_path: Path, pattern: str) -> None:
    with pytest.raises(WeaverError) as caught:
        load_config(tmp_path, request(tmp_path, include=[pattern]))
    assert caught.value.code is ErrorCode.CONFIGURATION_ERROR


def test_unsupported_language_has_public_error(tmp_path: Path) -> None:
    profile = tmp_path / "other.yaml"
    profile.write_text("version: 1\nlanguage:\n  primary: javascript\n", encoding="utf-8")
    with pytest.raises(WeaverError) as caught:
        load_config(tmp_path, request(tmp_path, risk_profile=str(profile)))
    assert caught.value.code is ErrorCode.UNSUPPORTED_LANGUAGE


def test_oversized_profile_is_rejected(tmp_path: Path) -> None:
    profile = tmp_path / "large.yaml"
    profile.write_text("x" * (256 * 1024 + 1), encoding="utf-8")
    with pytest.raises(WeaverError):
        load_config(tmp_path, request(tmp_path, risk_profile=str(profile)))


def test_yaml_depth_and_alias_budgets_fail_closed(tmp_path: Path) -> None:
    deep = tmp_path / "deep.yaml"
    nested = "".join(f"{'  ' * depth}item_{depth}:\n" for depth in range(51))
    deep.write_text(f"{nested}{'  ' * 51}leaf: true\n", encoding="utf-8")
    with pytest.raises(WeaverError, match="depth limit"):
        _read_yaml(deep)

    aliases = tmp_path / "aliases.yaml"
    aliases.write_text(
        "anchor: &anchor safe\nitems: [" + ", ".join("*anchor" for _ in range(101)) + "]\n",
        encoding="utf-8",
    )
    with pytest.raises(WeaverError, match="alias limit"):
        _read_yaml(aliases)


def test_missing_wrong_extension_and_empty_profiles(tmp_path: Path) -> None:
    with pytest.raises(WeaverError):
        load_config(tmp_path, request(tmp_path, risk_profile=str(tmp_path / "missing.yaml")))
    wrong = tmp_path / "profile.txt"
    wrong.write_text("version: 1\n", encoding="utf-8")
    with pytest.raises(WeaverError):
        load_config(tmp_path, request(tmp_path, risk_profile=str(wrong)))
    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    config, warnings = load_config(tmp_path, request(tmp_path, risk_profile=str(empty)))
    assert config.version == 1
    assert warnings == []


def test_explicit_profile_overrides_repository_config(tmp_path: Path) -> None:
    (tmp_path / ".semantic-diff-weaver.yaml").write_text(
        "version: 1\nrules:\n  max_changed_files: 9\n", encoding="utf-8"
    )
    profile = tmp_path / "profile.yaml"
    profile.write_text("version: 1\nrules:\n  max_changed_files: 5\n", encoding="utf-8")
    config, _ = load_config(tmp_path, request(tmp_path, risk_profile=str(profile)))
    assert config.rules.max_changed_files == 5


def test_repository_config_symlink_may_not_escape_boundary(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.yaml"
    outside.write_text("version: 1\n", encoding="utf-8")
    link = repo / ".semantic-diff-weaver.yaml"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(WeaverError) as caught:
        load_config(repo, request(repo))
    assert caught.value.code is ErrorCode.PATH_OUTSIDE_REPOSITORY


def test_file_config_globs_are_normalized_and_redaction_cannot_be_disabled(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        "version: 1\npaths:\n  include: ['src\\**\\*.py']\n",
        encoding="utf-8",
    )
    config, _ = load_config(tmp_path, request(tmp_path, risk_profile=str(profile)))
    assert config.paths.include == ["src/**/*.py"]
    profile.write_text(
        "version: 1\nprivacy:\n  redact_patterns: false\n",
        encoding="utf-8",
    )
    with pytest.raises(WeaverError):
        load_config(tmp_path, request(tmp_path, risk_profile=str(profile)))


def test_malformed_path_sections_are_left_for_strict_model_validation() -> None:
    data = {
        "paths": "invalid",
        "critical_paths": ["invalid", {}],
        "mapping": ["invalid", {"tests": "invalid"}, {}],
    }
    _validate_config_paths(data)
    assert data["paths"] == "invalid"


def test_empty_pattern_and_single_hermes_config_are_covered(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        request(tmp_path, include=[""])
    (tmp_path / ".hermes").mkdir()
    (tmp_path / ".hermes" / "semantic-diff-weaver.yaml").write_text(
        "version: 1\n", encoding="utf-8"
    )
    config, warnings = load_config(tmp_path, request(tmp_path))
    assert config.version == 1
    assert warnings == []


@pytest.mark.parametrize(
    ("path", "analyzed"),
    [
        ("semantic_diff_weaver/cli.py", True),
        ("semantic_diff_weaver/ast_diff/extract.py", True),
        ("hermes_semantic_diff_weaver/service.py", True),
        ("scripts/verify_wheel.py", True),
        ("__init__.py", True),
        ("tests/unit/test_config.py", False),
        ("tests/fixtures/repositories/sample.py", False),
    ],
)
def test_this_repository_analyzes_only_its_production_code(path: str, analyzed: bool) -> None:
    """The repository's own `.semantic-diff-weaver.yaml`, read by the real loader.

    With the built-in `include` of every Python file, a change that only added a test was a
    medium-risk behavior change, and on large ranges the suite used up the changed-symbol budget
    before production code did. Candidate tests are unaffected: they are found under
    `test_roots`, which `include` does not narrow.
    """
    root = Path(__file__).resolve().parents[2]
    config, warnings = load_config(root, request(root))
    assert warnings == []
    assert is_included(path, config.paths.include, config.paths.exclude) is analyzed
    assert config.paths.test_roots == ["tests"]


def test_a_verdict_level_can_be_turned_off(tmp_path: Path) -> None:
    """`none` disables a level, so either threshold may be `none` whatever the other is."""
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        "version: 1\nverdict:\n  warn_at: none\n  review_required_at: medium\n", encoding="utf-8"
    )
    config, _ = load_config(tmp_path, request(tmp_path, risk_profile=str(profile)))
    assert (config.verdict.warn_at, config.verdict.review_required_at) == ("none", "medium")
