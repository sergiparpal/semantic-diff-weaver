"""Configuration files a repository names in `paths.config_files` are compared key by key: named,
they are analyzed whatever `include` says, and one left unread leaves the analysis incomplete."""

from __future__ import annotations

import json
from typing import Any

import semantic_diff_weaver.git_diff.limits as git_limits
from semantic_diff_weaver.service import analyze

NAMED = (
    "version: 1\n"
    "paths:\n"
    "  include: ['src/**/*.py']\n"
    "  config_files: ['config/**', 'settings.toml', 'tests/**/*.yaml']\n"
)
OLD_APP = "http:\n  max_retries: 3\n  base_url: https://api.example\nfeatures:\n  checkout: false\n"
NEW_APP = "http:\n  max_retries: 5\n  base_url: https://api2.example\nfeatures:\n  checkout: true\n"
SETTINGS_TESTS = (
    "def test_retries_are_bounded(settings):\n    assert settings['http']['max_retries'] == 5\n"
)
OPENAPI = "openapi: 3.0.3\ninfo: {title: Shop, version: '1'}\npaths: {}\nservers: [{url: /v%d}]\n"


def _analysis(repo_factory, old: dict[str, str], new: dict[str, str], config: str = NAMED):
    repo, base, head = repo_factory(
        {".semantic-diff-weaver.yaml": config, **old}, {".semantic-diff-weaver.yaml": config, **new}
    )
    envelope = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "both"}
    )
    assert envelope["success"] is True
    return envelope["analysis"], envelope["markdown"]


def _findings(analysis: dict[str, Any]) -> dict[tuple[str, str], str]:
    return {
        (item["evidence"][0]["path"], item["evidence"][0]["symbol"]): item["category"]
        for item in analysis["behavior_changes"]
    }


def test_named_configuration_files_are_compared_whatever_include_says(repo_factory) -> None:
    analysis, markdown = _analysis(
        repo_factory,
        {
            "config/app.yaml": OLD_APP,
            "config/notes.ini": "[a]\nb = 1\n",
            "settings.toml": "[database]\ntimeout = 30\n",
            "deploy/values.yaml": "replicas: 2\n",
            "tests/test_settings.py": SETTINGS_TESTS,
        },
        {
            "config/app.yaml": NEW_APP,
            "config/notes.ini": "[a]\nb = 2\n",
            "settings.toml": "[database]\ntimeout = 60\n",
            "deploy/values.yaml": "replicas: 3\n",
        },
    )
    scope = analysis["scope"]
    assert scope["analyzed_files"] == ["config/app.yaml", "settings.toml"]
    # The values file is named by no glob and outside `include`; the notes are named, in a format
    # no comparison reads.
    assert scope["excluded_counts"] == {"path_filter": 1, "unsupported_config_format": 1}
    assert scope["truncated"] is False
    assert _findings(analysis) == {
        ("config/app.yaml", "features.checkout"): "default_behavior_change",
        ("config/app.yaml", "http.base_url"): "dependency_interaction_change",
        ("config/app.yaml", "http.max_retries"): "retry_timeout_change",
        ("settings.toml", "database.timeout"): "retry_timeout_change",
    }
    retries = next(
        item
        for item in analysis["behavior_changes"]
        if item["evidence"][0]["symbol"] == "http.max_retries"
    )
    assert (retries["evidence"][0]["old"], retries["evidence"][0]["new"]) == (
        "http.max_retries: 3",
        "http.max_retries: 5",
    )
    candidates = {
        test["symbol"]
        for obligation in analysis["test_obligations"]
        if retries["id"] in obligation["behavior_change_ids"]
        for test in obligation["candidate_existing_tests"]
    }
    assert candidates == {"test_retries_are_bounded"}
    assert (
        "Only committed Python source and configuration files at the resolved refs were inspected."
        in analysis["limitations"]
    )
    assert "1 named configuration file(s) in a format not compared" in markdown


def test_python_source_a_glob_names_is_still_python(repo_factory) -> None:
    config = "version: 1\npaths:\n  config_files: ['config/**']\n"
    analysis, _ = _analysis(
        repo_factory,
        {"config/settings.py": "def allowed(x):\n    return x < 5\n", "config/app.yaml": "a: 1\n"},
        {"config/settings.py": "def allowed(x):\n    return x <= 5\n", "config/app.yaml": "a: 2\n"},
        config,
    )
    assert analysis["scope"]["analyzed_files"] == ["config/app.yaml", "config/settings.py"]
    assert _findings(analysis) == {
        ("config/app.yaml", "a"): "default_behavior_change",
        ("config/settings.py", "allowed"): "boundary_change",
    }


def test_exclude_and_test_roots_still_apply_to_a_named_file(repo_factory) -> None:
    config = NAMED + "  exclude: ['config/local/**']\n"
    analysis, _ = _analysis(
        repo_factory,
        {"config/local/dev.yaml": "a: 1\n", "tests/fixtures/app.yaml": "a: 1\n"},
        {"config/local/dev.yaml": "a: 2\n", "tests/fixtures/app.yaml": "a: 2\n"},
        config,
    )
    assert analysis["scope"]["excluded_counts"] == {"path_filter": 1, "test_file": 1}
    assert analysis["scope"]["analyzed_files"] == []
    assert (
        "The bounded diff contained no included changed Python source, API schema, or "
        "configuration file." in analysis["limitations"]
    )


def test_a_named_file_that_cannot_be_read_leaves_the_analysis_incomplete(repo_factory) -> None:
    config = NAMED + "rules:\n  max_file_bytes: 1024\n"
    big = json.dumps({"keys": ["x" * 64] * 32})
    analysis, markdown = _analysis(
        repo_factory, {"config/big.json": "{}"}, {"config/big.json": big}, config
    )
    scope = analysis["scope"]
    assert scope["excluded_counts"] == {"unreadable_config_file": 1}
    assert scope["truncated"] is True and analysis["summary"]["verdict"] != "pass"
    assert (
        "1 configuration file(s) named by `paths.config_files` could not be read as bounded text."
        in analysis["limitations"]
    )
    assert "1 named configuration file(s) not readable as bounded text" in markdown


def test_a_named_file_that_does_not_parse_is_a_finding_of_its_own(repo_factory) -> None:
    analysis, _ = _analysis(
        repo_factory, {"settings.toml": "a = 1\n"}, {"settings.toml": "a = [\n"}
    )
    assert _findings(analysis) == {("settings.toml", "<document>"): "unknown_semantic_change"}
    assert {"reason": "parse_incomplete_config_files", "count": 1} in analysis["scope"]["omitted"]
    assert analysis["scope"]["truncated"] is True
    assert (
        "Could not parse the configuration file 'settings.toml' within its budgets."
        in (analysis["warnings"])
    )


def test_a_named_file_is_configuration_even_when_it_is_an_api_schema(repo_factory) -> None:
    analysis, _ = _analysis(
        repo_factory, {"config/openapi.yaml": OPENAPI % 1}, {"config/openapi.yaml": OPENAPI % 2}
    )
    assert _findings(analysis) == {
        ("config/openapi.yaml", "servers"): "dependency_interaction_change"
    }


def test_a_change_of_layout_alone_sets_no_value_differently(repo_factory) -> None:
    analysis, _ = _analysis(
        repo_factory,
        {"config/app.yaml": "a: 1\nb: [1, 2]\n"},
        {"config/app.yaml": "# reordered\nb:\n  - 1\n  - 2\na: 1\n"},
    )
    assert analysis["behavior_changes"] == []
    assert "The included configuration change set no value differently." in analysis["limitations"]


def test_named_files_come_first_when_documents_run_past_their_count(
    repo_factory, monkeypatch
) -> None:
    monkeypatch.setattr(git_limits, "MAX_DOCUMENTS", 1)
    config = (
        "version: 1\npaths:\n  config_files: ['z/app.yaml']\n"
        "critical_paths:\n  - pattern: 'a/**'\n    weight: 90\n"
    )
    analysis, _ = _analysis(
        repo_factory,
        {"a/openapi.yaml": OPENAPI % 1, "z/app.yaml": "a: 1\n"},
        {"a/openapi.yaml": OPENAPI % 2, "z/app.yaml": "a: 2\n"},
        config,
    )
    assert analysis["scope"]["analyzed_files"] == ["z/app.yaml"]
    assert {"reason": "document_limit", "count": 1} in analysis["scope"]["omitted"]
    assert analysis["scope"]["truncated"] is True
