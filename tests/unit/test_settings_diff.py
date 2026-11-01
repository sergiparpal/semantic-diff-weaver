"""Configuration files a repository names: compared key by key, and classified by their keys."""

from __future__ import annotations

from typing import Any

import pytest

from semantic_diff_weaver.ast_diff import StructuralDelta
from semantic_diff_weaver.models import BehaviorCategory, RulesConfig
from semantic_diff_weaver.schema_diff import analyze_settings
from semantic_diff_weaver.schema_diff.deltas import MAX_DOCUMENT_DELTAS
from semantic_diff_weaver.schema_diff.load import DocumentError, load_document
from semantic_diff_weaver.schema_diff.settings import MAX_KEY_DEPTH, compare_settings, value_type
from semantic_diff_weaver.semantic_candidates import classify_delta
from semantic_diff_weaver.source import SourceRevisionPair


def _changes(old: Any, new: Any) -> list[tuple[str, str, str | None, str | None]]:
    deltas, truncated = compare_settings("config/app.yaml", old, new)
    assert truncated is False
    return [(item.symbol, item.kind, item.old, item.new) for item in deltas]


def test_a_toml_document_loads_as_plain_data() -> None:
    assert load_document("settings.toml", "when = 2026-11-01\n[db]\nport = 5432\n") == {
        "when": "2026-11-01",
        "db": {"port": 5432},
    }
    for text in ("a = [", "a = 1\na = 2\n", "a = " + "[" * 50_000 + "]" * 50_000):
        with pytest.raises(DocumentError):
            load_document("settings.toml", text)


def test_values_are_compared_by_their_dotted_key_paths() -> None:
    old = {"http": {"max_retries": 3, "hosts": ["a", "b"]}, "debug": False, "legacy": "on"}
    new = {"http": {"max_retries": 5, "hosts": ["a", "b"]}, "debug": True, "cache": {"ttl": 30}}
    assert _changes(old, new) == [
        ("cache.ttl", "config_key_added", None, "cache.ttl: 30"),
        ("debug", "config_value_change", "debug: false", "debug: true"),
        ("http.max_retries", "config_value_change", "http.max_retries: 3", "http.max_retries: 5"),
        ("legacy", "config_key_removed", 'legacy: "on"', None),
    ]


def test_a_list_is_one_value_and_a_switch_is_not_a_count() -> None:
    assert _changes(
        {"hosts": ["a", "b"], "workers": 1}, {"hosts": ["b", "a"], "workers": True}
    ) == [
        ("hosts", "config_value_change", 'hosts: ["a", "b"]', 'hosts: ["b", "a"]'),
        ("workers", "config_value_change", "workers: 1", "workers: true"),
    ]
    deltas, _ = compare_settings("app.yaml", {"workers": 1}, {"workers": True})
    assert (deltas[0].metadata["old_type"], deltas[0].metadata["new_type"]) == ("number", "boolean")


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        (True, "boolean"),
        (3, "number"),
        (0.5, "number"),
        ("x", "string"),
        ([1], "list"),
        ({"a": 1}, "mapping"),
        (None, "null"),
    ],
)
def test_a_value_is_typed_for_its_classification(value: Any, kind: str) -> None:
    assert value_type(value) == kind


def test_a_new_or_deleted_file_and_a_root_that_is_no_mapping() -> None:
    assert _changes(None, {"a": {"b": 1}}) == [("a.b", "config_key_added", None, "a.b: 1")]
    assert _changes({"a": 1}, None) == [("a", "config_key_removed", "a: 1", None)]
    assert _changes(["x"], ["y"]) == [
        ("<root>", "config_value_change", '<root>: ["x"]', '<root>: ["y"]')
    ]
    assert _changes({"a": {}}, {"a": {}}) == []


def test_keys_are_bounded_in_depth_and_deltas_in_number() -> None:
    deep_old: dict[str, Any] = {"leaf": 1}
    deep_new: dict[str, Any] = {"leaf": 2}
    for _ in range(MAX_KEY_DEPTH + 3):
        deep_old, deep_new = {"k": deep_old}, {"k": deep_new}
    ((symbol, kind, _, _),) = _changes(deep_old, deep_new)
    assert symbol.count(".") == MAX_KEY_DEPTH - 1 and kind == "config_value_change"
    many_old = {f"key{index}": index for index in range(MAX_DOCUMENT_DELTAS + 5)}
    deltas, truncated = compare_settings("app.yaml", many_old, {})
    assert len(deltas) == MAX_DOCUMENT_DELTAS and truncated is True


def _pair(path: str, old: str | None, new: str | None) -> SourceRevisionPair:
    return SourceRevisionPair(
        path=path,
        old_path=path if old is not None else None,
        new_path=path if new is not None else None,
        old_text=old,
        new_text=new,
        hunks=(),
        has_old_side=old is not None,
    )


def test_each_named_file_is_compared_and_one_that_does_not_parse_is_a_finding() -> None:
    result = analyze_settings(
        [
            _pair("config/app.yaml", "a: 1\n", "a: 2\n"),
            _pair("settings.toml", "a = 1\n", "a = [\n"),
            _pair("config/new.json", None, '{"b": true}'),
            _pair("config/same.yaml", "a: 1\n", "a: 1  # unchanged\n"),
        ]
    )
    assert result.analyzed_paths == [
        "config/app.yaml",
        "settings.toml",
        "config/new.json",
        "config/same.yaml",
    ]
    assert [(item.path, item.symbol, item.kind) for item in result.deltas] == [
        ("config/app.yaml", "a", "config_value_change"),
        ("settings.toml", "<document>", "config_parse_incomplete"),
        ("config/new.json", "b", "config_key_added"),
    ]
    assert result.deltas[1].parser_complete is False
    assert (result.parse_incomplete, result.not_schema, result.changed_symbols) == (1, 0, 3)
    assert result.warnings == [
        "Could not parse the configuration file 'settings.toml' within its budgets."
    ]


def test_a_file_past_its_delta_budget_says_so() -> None:
    many = "".join(f"key{index}: {index}\n" for index in range(MAX_DOCUMENT_DELTAS + 1))
    result = analyze_settings([_pair("config/app.yaml", many, "{}\n")])
    assert result.truncated_documents == 1
    assert result.warnings == [
        "Only the first changes of the configuration file 'config/app.yaml' were compared."
    ]


@pytest.mark.parametrize(
    ("key", "kind", "types", "category", "rule"),
    [
        ("auth.admin_roles", "config_value_change", ("list", "list"), "AUTHORIZATION", "AUTH"),
        (
            "http.timeout_seconds",
            "config_value_change",
            ("number", "number"),
            "RETRY_TIMEOUT",
            "RETRY",
        ),
        ("worker.max_attempts", "config_key_added", (None, "number"), "RETRY_TIMEOUT", "RETRY"),
        ("pool.max_size", "config_value_change", ("number", "number"), "BOUNDARY", "BOUNDARY"),
        # A limit that is no number is no boundary to test below and above.
        ("pool.max_size", "config_value_change", ("string", "number"), "DEFAULT_BEHAVIOR", "VALUE"),
        (
            "upload.allowed_types",
            "config_value_change",
            ("list", "list"),
            "VALIDATION",
            "VALIDATION",
        ),
        ("debug", "config_value_change", ("boolean", "boolean"), "DEFAULT_BEHAVIOR", "FLAG"),
        ("features.checkout", "config_key_removed", ("string", None), "DEFAULT_BEHAVIOR", "FLAG"),
        (
            "billing.base_url",
            "config_value_change",
            ("string", "string"),
            "DEPENDENCY_INTERACTION",
            "DEPENDENCY",
        ),
        ("log_level", "config_value_change", ("string", "string"), "DEFAULT_BEHAVIOR", "VALUE"),
        ("log_level", "config_key_removed", ("string", None), "DEFAULT_BEHAVIOR", "VALUE"),
        ("log_level", "config_key_added", (None, "string"), "UNKNOWN", "VALUE"),
        ("<document>", "config_parse_incomplete", (None, None), "UNKNOWN", "PARSE-INCOMPLETE"),
    ],
)
def test_a_configuration_delta_is_classified_by_its_key_and_value(
    key: str,
    kind: str,
    types: tuple[str | None, str | None],
    category: str,
    rule: str,
) -> None:
    delta = StructuralDelta(
        path="config/app.yaml",
        symbol=key,
        kind=kind,
        old="a",
        new="b",
        old_lines=None,
        new_lines=None,
        hunk_id=None,
        metadata={"context": "configuration", "old_type": types[0], "new_type": types[1]},
    )
    found, summary, _, confidence, assumptions, rule_id = classify_delta(delta, RulesConfig())
    assert found is BehaviorCategory[category]
    assert rule_id == f"SDW-RULE-CONFIG-{rule}"
    assert 0.5 <= confidence <= 0.8 and assumptions
    if kind == "config_key_added" and rule != "VALUE":
        assert summary.endswith("was added.")


def test_a_key_that_was_added_is_less_certain_than_one_that_changed() -> None:
    def confidence(kind: str) -> float:
        delta = StructuralDelta(
            path="config/app.yaml",
            symbol="http.timeout",
            kind=kind,
            old=None,
            new="b",
            old_lines=None,
            new_lines=None,
            hunk_id=None,
            metadata={"context": "configuration", "old_type": None, "new_type": "number"},
        )
        return classify_delta(delta, RulesConfig())[3]

    assert confidence("config_key_added") < confidence("config_value_change")
