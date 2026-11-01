"""API schemas: bounded loading, what a document is, and what changed between two revisions."""

from __future__ import annotations

import json
from typing import Any

import pytest

from semantic_diff_weaver.models import BehaviorCategory, RulesConfig
from semantic_diff_weaver.schema_diff import analyze_schemas
from semantic_diff_weaver.schema_diff.compare import (
    MAX_DOCUMENT_DELTAS,
    MAX_SCHEMA_DEPTH,
    MAX_SHOWN_CHARS,
    MAX_SYMBOL_CHARS,
    compare_documents,
    document_kind,
)
from semantic_diff_weaver.schema_diff.load import DocumentError, load_document
from semantic_diff_weaver.semantic_candidates import classify_delta
from semantic_diff_weaver.source import SourceRevisionPair


def _openapi(paths: dict[str, Any] | None = None, **root: Any) -> dict[str, Any]:
    return {
        "openapi": "3.0.3",
        "info": {"title": "Shop", "version": "1"},
        "paths": paths or {},
        **root,
    }


def _operation(**fields: Any) -> dict[str, Any]:
    return {"responses": {"200": {"description": "ok"}}, **fields}


def _kinds(old: Any, new: Any, kind: str = "openapi") -> list[tuple[str, str, str]]:
    deltas, truncated = compare_documents("api.yaml", old, new, kind)
    assert truncated is False
    return [(item.symbol, item.kind, item.metadata["location"]) for item in deltas]


def test_yaml_and_json_load_as_plain_data_with_string_keys() -> None:
    assert load_document("a.yaml", "responses:\n  200: {description: ok}\n") == {
        "responses": {"200": {"description": "ok"}}
    }
    assert load_document("a.json", '{"a": [1, true, null]}') == {"a": [1, True, None]}
    # A timestamp is compared by its text, not as an object.
    assert load_document("a.yml", "when: 2026-11-01\n") == {"when": "2026-11-01"}


@pytest.mark.parametrize(
    "text",
    [
        "a: [1, 2\n",
        "!!python/object/apply:os.system ['true']\n",
        "first: 1\n---\nsecond: 2\n",
        "a: &x [1]\n" + "".join(f"b{index}: [*x, *x]\n" for index in range(120)),
        "a: " + "[" * 70 + "]" * 70 + "\n",
    ],
    ids=["syntax", "python-tag", "two-documents", "aliases", "depth"],
)
def test_a_document_that_does_not_read_within_its_budgets_is_an_error(text: str) -> None:
    with pytest.raises(DocumentError):
        load_document("a.yaml", text)


def test_a_json_document_is_bounded_in_depth_and_nodes() -> None:
    with pytest.raises(DocumentError, match="depth"):
        load_document("a.json", "[" * 80 + "]" * 80)
    with pytest.raises(DocumentError, match="node"):
        load_document("a.json", json.dumps(list(range(200_001))))


@pytest.mark.parametrize(
    ("root", "kind"),
    [
        (_openapi(), "openapi"),
        ({"openapi": 3.1, "components": {}}, "openapi"),
        ({"swagger": "2.0", "paths": {}}, "swagger"),
        ({"$schema": "https://json-schema.org/draft/2020-12/schema"}, "json_schema"),
        ({"$schema": "http://json-schema.org/draft-07/schema#"}, "json_schema"),
        ({"$schema": "https://spec.openapis.org/oas/3.1/dialect/base"}, "json_schema"),
        # A configuration file names the schema that validates it, and is no schema itself.
        ({"$schema": "https://json.schemastore.org/tsconfig", "compilerOptions": {}}, None),
        ({"$schema": "https://docs.renovatebot.com/renovate-schema.json"}, None),
        ({"openapi": True}, None),
        ({"openapi": "3.0.0"}, None),
        ({"name": "shop", "version": "1.0.0"}, None),
        (["openapi"], None),
        (None, None),
    ],
)
def test_a_document_is_an_api_schema_only_as_its_root_says(root: Any, kind: str | None) -> None:
    assert document_kind(root) == kind


def test_operations_are_compared_by_method_and_route() -> None:
    old = _openapi({"/orders/{id}": {"get": _operation(), "delete": _operation()}})
    new = _openapi({"/orders/{id}": {"get": _operation()}, "/carts": {"post": _operation()}})
    assert _kinds(old, new) == [
        ("DELETE /orders/{id}", "api_operation_removed", "operation"),
        ("POST /carts", "api_operation_added", "operation"),
    ]


def test_parameters_are_compared_by_where_and_what_they_are() -> None:
    limit = {"name": "limit", "in": "query", "schema": {"type": "integer", "maximum": 50}}
    old = _openapi(
        {
            "/orders": {
                "parameters": [{"name": "tenant", "in": "header", "required": True}],
                "get": _operation(parameters=[limit, {"name": "sort", "in": "query"}]),
            }
        }
    )
    new = _openapi(
        {
            "/orders": {
                "parameters": [{"name": "tenant", "in": "header", "required": False}],
                "get": _operation(
                    parameters=[
                        {**limit, "schema": {"type": "integer", "maximum": 100}},
                        {"name": "page", "in": "query", "required": True},
                    ]
                ),
            }
        }
    )
    deltas, _ = compare_documents("api.yaml", old, new, "openapi")
    assert [(item.kind, item.metadata["location"]) for item in deltas] == [
        ("api_parameter_removed", "parameter query sort"),
        ("api_parameter_added", "parameter query page"),
        ("api_parameter_required_change", "parameter header tenant required"),
        ("schema_bound_change", "parameter query limit maximum"),
    ]
    assert deltas[1].metadata["required"] is True
    assert {item.symbol for item in deltas} == {"GET /orders"}
    assert {item.metadata["context"] for item in deltas} == {"request"}


def test_a_swagger_parameter_holds_its_schema_inline() -> None:
    old = {
        "swagger": "2.0",
        "paths": {
            "/orders": {
                "get": _operation(
                    parameters=[
                        {
                            "name": "limit",
                            "in": "query",
                            "type": "integer",
                            "maximum": 50,
                            "description": "a",
                        }
                    ]
                )
            }
        },
    }
    new = {
        "swagger": "2.0",
        "paths": {
            "/orders": {
                "get": _operation(
                    parameters=[
                        {
                            "name": "limit",
                            "in": "query",
                            "type": "integer",
                            "maximum": 100,
                            "description": "b",
                        }
                    ]
                )
            }
        },
    }
    assert _kinds(old, new, "swagger") == [
        ("GET /orders", "schema_bound_change", "parameter query limit maximum")
    ]


def test_request_bodies_responses_and_media_types_are_compared() -> None:
    body = {"required": True, "content": {"application/json": {"schema": {"type": "object"}}}}
    old = _openapi(
        {
            "/orders": {
                "post": {
                    "requestBody": body,
                    "responses": {
                        "201": {
                            "description": "made",
                            "content": {"application/json": {"schema": {"type": "object"}}},
                        },
                        "409": {"description": "taken"},
                    },
                }
            }
        }
    )
    new = _openapi(
        {
            "/orders": {
                "post": {
                    "requestBody": {**body, "required": False, "content": {"application/xml": {}}},
                    "responses": {
                        "201": {
                            "description": "made",
                            "content": {"application/json": {"schema": {"type": "array"}}},
                        },
                        "422": {"description": "invalid"},
                    },
                }
            }
        }
    )
    assert _kinds(old, new) == [
        ("POST /orders", "api_request_body_required_change", "request body required"),
        ("POST /orders", "api_media_type_removed", "request body application/json"),
        ("POST /orders", "api_media_type_added", "request body application/xml"),
        ("POST /orders", "api_response_removed", "response 409"),
        ("POST /orders", "api_response_added", "response 422"),
        ("POST /orders", "schema_type_change", "response 201 application/json type"),
    ]


def test_a_request_body_that_appears_or_a_swagger_response_schema_is_compared() -> None:
    old = {
        "swagger": "2.0",
        "paths": {"/a": {"put": {"responses": {"200": {"description": "ok"}}}}},
    }
    new = {
        "swagger": "2.0",
        "paths": {
            "/a": {
                "put": {
                    "requestBody": {"content": {}},
                    "responses": {
                        "200": {
                            "description": "ok",
                            "schema": {"type": "string"},
                            "headers": {"X-Id": {}},
                        }
                    },
                }
            }
        },
    }
    assert _kinds(old, new, "swagger") == [
        ("PUT /a", "api_request_body_change", "request body"),
        ("PUT /a", "schema_added", "response 200"),
        ("PUT /a", "schema_keyword_change", "response 200 headers"),
    ]


def test_security_and_servers_are_compared_where_they_apply() -> None:
    old = _openapi(
        {"/orders": {"get": _operation()}},
        security=[{"oauth": []}],
        servers=[{"url": "/v1"}],
        components={"securitySchemes": {"oauth": {"type": "oauth2"}}},
    )
    new = _openapi(
        {"/orders": {"get": _operation(security=[])}},
        security=[{"apiKey": []}],
        servers=[{"url": "/v2"}],
        components={"securitySchemes": {"apiKey": {"type": "apiKey"}}},
    )
    assert _kinds(old, new) == [
        ("GET /orders", "api_security_change", "security"),
        ("<security>", "api_security_change", "global security"),
        ("<security>", "api_security_change", "security scheme apiKey"),
        ("<security>", "api_security_change", "security scheme oauth"),
        ("<servers>", "api_server_change", "servers"),
    ]


def test_schema_keywords_are_compared_one_by_one_through_nested_properties() -> None:
    order = {
        "type": "object",
        "required": ["id"],
        "properties": {
            "id": {"type": "string", "pattern": "^o-"},
            "status": {"type": "string", "enum": ["open", "closed"], "default": "open"},
            "lines": {
                "type": "array",
                "items": {"type": "object", "properties": {"sku": {"maxLength": 8}}},
            },
            "note": {"type": "string"},
            "tags": {"type": "object", "additionalProperties": {"type": "string"}},
        },
    }
    changed = json.loads(json.dumps(order))
    changed["required"] = ["id", "status"]
    changed["properties"]["id"]["pattern"] = "^ord-"
    changed["properties"]["status"]["enum"] = ["open", "closed", "void"]
    changed["properties"]["status"]["default"] = "closed"
    changed["properties"]["lines"]["items"]["properties"]["sku"]["maxLength"] = 12
    del changed["properties"]["note"]
    changed["properties"]["total"] = {"type": "number"}
    changed["properties"]["tags"]["additionalProperties"] = {"type": "integer"}
    old = _openapi(components={"schemas": {"Order": order}})
    new = _openapi(components={"schemas": {"Order": changed}})
    deltas, _ = compare_documents("api.yaml", old, new, "openapi")
    assert [(item.kind, item.metadata["location"]) for item in deltas] == [
        ("schema_required_change", "required"),
        ("schema_property_removed", "note"),
        ("schema_property_added", "total"),
        ("schema_pattern_change", "id pattern"),
        ("schema_bound_change", "lines[].sku maxLength"),
        ("schema_enum_change", "status enum"),
        ("schema_default_change", "status default"),
        ("schema_type_change", "tags.* type"),
    ]
    assert deltas[0].metadata["became_required"] == ["status"]
    assert deltas[0].metadata["became_optional"] == []
    assert {item.symbol for item in deltas} == {"Order"}
    assert {item.metadata["context"] for item in deltas} == {"component"}


def test_words_for_people_and_extensions_change_nothing() -> None:
    old = _openapi(
        {
            "/orders": {
                "get": _operation(summary="List", description="All", tags=["a"], operationId="x")
            }
        },
        components={
            "schemas": {
                "Order": {"type": "object", "title": "An order", "example": {}, "x-db": "t"}
            }
        },
    )
    new = _openapi(
        {
            "/orders": {
                "get": _operation(summary="Show", description="Every", tags=["b"], operationId="y")
            }
        },
        components={
            "schemas": {
                "Order": {"type": "object", "title": "One order", "example": {"a": 1}, "x-db": "u"}
            }
        },
    )
    assert _kinds(old, new) == []


def test_refs_unknown_keywords_and_whole_schemas_are_compared_as_text() -> None:
    old = _openapi(
        components={
            "schemas": {
                "Order": {"$ref": "#/components/schemas/V1", "nullable": False},
                "Gone": {"type": "string"},
                "Flag": True,
            }
        }
    )
    new = _openapi(
        components={
            "schemas": {
                "Order": {"$ref": "#/components/schemas/V2", "nullable": True},
                "Fresh": {"type": "string"},
                "Flag": False,
            }
        }
    )
    assert _kinds(old, new) == [
        ("Flag", "schema_keyword_change", "schema"),
        ("Fresh", "schema_added", "schema"),
        ("Gone", "schema_removed", "schema"),
        ("Order", "schema_ref_change", "$ref"),
        ("Order", "schema_keyword_change", "nullable"),
    ]


def test_parts_of_a_document_that_are_not_what_they_should_be_are_compared_as_values() -> None:
    old = _openapi(
        {
            "/orders": {
                "get": _operation(
                    parameters=["junk", {"name": "a", "in": "query"}],
                    responses={
                        "200": {"description": "ok"},
                        "404": {"$ref": "#/components/responses/Missing"},
                    },
                )
            },
            "/stale": None,
        },
        components={"schemas": {"Tags": {"additionalProperties": False}}},
    )
    new = _openapi(
        {
            "/orders": {
                "get": _operation(
                    parameters=[{"name": "a", "in": "query"}, 7],
                    responses={
                        "200": "ok",
                        "404": {"$ref": "#/components/responses/Gone"},
                    },
                )
            },
            "/stale": "gone",
        },
        components={"schemas": {"Tags": {"additionalProperties": {"type": "string"}}}},
    )
    assert _kinds(old, new) == [
        ("GET /orders", "schema_keyword_change", "response 200"),
        ("GET /orders", "schema_ref_change", "response 404 $ref"),
        ("Tags", "schema_keyword_change", "additionalProperties"),
    ]


def test_a_json_schema_is_compared_by_its_root_and_definitions() -> None:
    old = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"age": {"minimum": 0}},
        "$defs": {"Name": {"type": "string", "maxLength": 20}},
    }
    new = {
        **old,
        "properties": {"age": {"minimum": 18}},
        "$defs": {"Name": {"type": "string", "maxLength": 40}},
    }
    deltas, _ = compare_documents("schema.json", old, new, "json_schema")
    assert [(item.symbol, item.kind, item.metadata["location"]) for item in deltas] == [
        ("<root>", "schema_bound_change", "age minimum"),
        ("Name", "schema_bound_change", "maxLength"),
    ]
    assert {item.metadata["context"] for item in deltas} == {"schema"}


def test_a_new_json_schema_is_its_root_and_definitions_added() -> None:
    new = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "$defs": {"Name": {"type": "string"}},
    }
    assert _kinds(None, new, "json_schema") == [
        ("<root>", "schema_added", "schema"),
        ("Name", "schema_added", "schema"),
    ]


def test_a_new_document_is_everything_added() -> None:
    new = _openapi(
        {"/orders": {"get": _operation()}}, components={"schemas": {"Order": {"type": "object"}}}
    )
    assert _kinds(None, new) == [
        ("GET /orders", "api_operation_added", "operation"),
        ("Order", "schema_added", "schema"),
    ]


def test_deltas_are_bounded_per_document_and_by_schema_depth() -> None:
    old = _openapi(
        {f"/r{index}": {"get": _operation()} for index in range(MAX_DOCUMENT_DELTAS + 5)}
    )
    deltas, truncated = compare_documents("api.yaml", old, _openapi(), "openapi")
    assert len(deltas) == MAX_DOCUMENT_DELTAS and truncated is True
    deep_old: dict[str, Any] = {"type": "string"}
    deep_new: dict[str, Any] = {"type": "integer"}
    for _ in range(MAX_SCHEMA_DEPTH + 3):
        deep_old = {"properties": {"a": deep_old}}
        deep_new = {"properties": {"a": deep_new}}
    found = _kinds(
        _openapi(components={"schemas": {"D": deep_old}}),
        _openapi(components={"schemas": {"D": deep_new}}),
    )
    assert [kind for _, kind, _ in found] == ["schema_keyword_change"]


def test_what_a_delta_shows_and_names_is_bounded() -> None:
    route = "/" + "a" * (MAX_SYMBOL_CHARS * 2)
    secret = "ghp_" + "A" * 40
    old = _openapi(
        {route: {"get": _operation()}},
        components={"schemas": {"Big": {"default": [secret, *(["x" * 50] * 2000)]}}},
    )
    new = _openapi(components={"schemas": {"Big": {"default": []}}})
    deltas, _ = compare_documents("api.yaml", old, new, "openapi")
    removed, changed = deltas
    assert len(removed.symbol) == MAX_SYMBOL_CHARS and removed.symbol.endswith("...")
    assert removed.symbol.startswith("GET /aaa")
    assert changed.old is not None and len(changed.old) <= MAX_SHOWN_CHARS
    assert secret not in changed.old and "[REDACTED]" in changed.old


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


def test_only_documents_one_revision_calls_a_schema_are_compared() -> None:
    schema = "openapi: 3.0.0\npaths:\n  /a:\n    get: {responses: {'200': {description: ok}}}\n"
    result = analyze_schemas(
        [
            _pair("package.json", '{"version": "1"}', '{"version": "2"}'),
            _pair("api.yaml", schema, None),
            _pair("new.yaml", None, schema),
            _pair("ci.yml", "jobs: [", "jobs: []"),
            _pair(
                "renovate.json",
                '{"$schema": "https://docs.renovatebot.com/renovate-schema.json", "a": [',
                '{"$schema": "https://docs.renovatebot.com/renovate-schema.json", "a": []}',
            ),
        ]
    )
    assert (result.not_schema, result.parse_incomplete) == (3, 0)
    assert result.analyzed_paths == ["api.yaml", "new.yaml"]
    assert [(item.path, item.kind) for item in result.deltas] == [
        ("api.yaml", "api_operation_removed"),
        ("new.yaml", "api_operation_added"),
    ]
    assert result.changed_symbols == 2


def test_a_schema_that_no_longer_parses_is_a_finding_not_a_silence() -> None:
    result = analyze_schemas(
        [_pair("api.yaml", "openapi: 3.0.0\npaths: {}\n", "openapi: 3.0.0\npaths: {\n")]
    )
    assert result.parse_incomplete == 1 and result.not_schema == 0
    (delta,) = result.deltas
    assert (delta.symbol, delta.kind, delta.parser_complete) == (
        "<document>",
        "document_parse_incomplete",
        False,
    )
    assert result.warnings == ["Could not parse the API schema 'api.yaml' within its budgets."]


@pytest.mark.parametrize(
    "text",
    [
        '{"openapi":"3.1.0","paths":{',
        '{\n  "swagger": "2.0",\n  "paths": [\n',
        '{\n  "$schema": "http://json-schema.org/draft-07/schema#",\n  "type": [\n',
    ],
)
def test_a_json_schema_that_does_not_parse_is_told_by_its_opening_key(text: str) -> None:
    result = analyze_schemas([_pair("api.json", "{}", text)])
    assert (result.parse_incomplete, result.not_schema) == (1, 0)


def test_a_document_past_its_delta_budget_says_so() -> None:
    many = json.dumps(
        _openapi({f"/r{index}": {"get": _operation()} for index in range(MAX_DOCUMENT_DELTAS + 1)})
    )
    result = analyze_schemas([_pair("api.json", many, json.dumps(_openapi()))])
    assert result.truncated_documents == 1
    assert result.warnings == ["Only the first changes of the API schema 'api.json' were compared."]


@pytest.mark.parametrize(
    ("kind", "context", "metadata", "category"),
    [
        ("api_operation_removed", "response", {}, BehaviorCategory.OUTPUT_CONTRACT),
        ("api_operation_added", "response", {}, BehaviorCategory.UNKNOWN),
        ("api_parameter_added", "request", {"required": True}, BehaviorCategory.VALIDATION),
        ("api_parameter_added", "request", {"required": False}, BehaviorCategory.DEFAULT_BEHAVIOR),
        ("api_parameter_removed", "request", {}, BehaviorCategory.VALIDATION),
        ("api_parameter_required_change", "request", {}, BehaviorCategory.VALIDATION),
        ("api_request_body_change", "request", {}, BehaviorCategory.VALIDATION),
        ("api_request_body_required_change", "request", {}, BehaviorCategory.VALIDATION),
        ("api_response_added", "response", {}, BehaviorCategory.OUTPUT_CONTRACT),
        ("api_response_removed", "response", {}, BehaviorCategory.OUTPUT_CONTRACT),
        ("api_media_type_added", "request", {}, BehaviorCategory.VALIDATION),
        ("api_media_type_removed", "response", {}, BehaviorCategory.OUTPUT_CONTRACT),
        ("api_security_change", "request", {}, BehaviorCategory.AUTHORIZATION),
        ("api_server_change", "response", {}, BehaviorCategory.OUTPUT_CONTRACT),
        ("schema_property_removed", "request", {}, BehaviorCategory.VALIDATION),
        ("schema_property_removed", "component", {}, BehaviorCategory.OUTPUT_CONTRACT),
        ("schema_property_added", "schema", {}, BehaviorCategory.VALIDATION),
        ("schema_property_added", "response", {}, BehaviorCategory.OUTPUT_CONTRACT),
        ("schema_required_change", "component", {}, BehaviorCategory.VALIDATION),
        ("schema_enum_change", "request", {}, BehaviorCategory.VALIDATION),
        ("schema_enum_change", "response", {}, BehaviorCategory.OUTPUT_CONTRACT),
        ("schema_type_change", "schema", {}, BehaviorCategory.VALIDATION),
        ("schema_type_change", "component", {}, BehaviorCategory.OUTPUT_CONTRACT),
        ("schema_bound_change", "response", {}, BehaviorCategory.BOUNDARY),
        ("schema_pattern_change", "request", {}, BehaviorCategory.VALIDATION),
        ("schema_default_change", "request", {}, BehaviorCategory.DEFAULT_BEHAVIOR),
        ("schema_ref_change", "request", {}, BehaviorCategory.VALIDATION),
        ("schema_ref_change", "response", {}, BehaviorCategory.OUTPUT_CONTRACT),
        ("schema_removed", "schema", {}, BehaviorCategory.VALIDATION),
        ("schema_removed", "component", {}, BehaviorCategory.OUTPUT_CONTRACT),
        ("schema_added", "request", {}, BehaviorCategory.VALIDATION),
        ("schema_added", "response", {}, BehaviorCategory.OUTPUT_CONTRACT),
        ("schema_added", "component", {}, BehaviorCategory.UNKNOWN),
        ("schema_keyword_change", "component", {}, BehaviorCategory.UNKNOWN),
        ("document_parse_incomplete", "", {}, BehaviorCategory.UNKNOWN),
    ],
)
def test_a_schema_delta_is_classified_by_its_kind_and_where_it_is(
    kind: str, context: str, metadata: dict[str, Any], category: BehaviorCategory
) -> None:
    from semantic_diff_weaver.ast_diff import StructuralDelta

    delta = StructuralDelta(
        path="api.yaml",
        symbol="GET /orders",
        kind=kind,
        old="a",
        new="b",
        old_lines=None,
        new_lines=None,
        hunk_id=None,
        metadata={"context": context, **metadata},
    )
    found, _, _, confidence, assumptions, rule = classify_delta(delta, RulesConfig())
    assert found is category
    assert 0.45 <= confidence <= 0.9 and assumptions and rule.startswith("SDW-RULE-")
