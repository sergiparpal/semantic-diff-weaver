"""What changed between two revisions of an API schema, as structural deltas.

An OpenAPI 3 or Swagger 2 document is compared operation by operation — its parameters, request
body, responses, and the security it requires — then by its global security, its servers, and its
reusable schemas; a JSON Schema by its root schema and its definitions. Within a schema, the
keywords that decide what is accepted or returned are compared one by one: properties, `required`,
`enum`, `type`, the numeric and length bounds, `pattern` and `format`, `default`, and `$ref`,
which is compared as text and never resolved. Words for people — descriptions, titles, examples —
and `x-` extensions change no behavior, so nothing reads them.

Each delta names where in the document it is, and whether that place is part of a request, a
response, a reusable component, or a standalone JSON Schema: one change means different things
there, so the classification reads it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..ast_diff import StructuralDelta
from ..path_policy import redact_text
from .load import JsonValue

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
# Words for people and for tooling: none changes what an API accepts or returns.
DOCUMENTATION_KEYWORDS = frozenset(
    {
        "$comment",
        "deprecated",
        "description",
        "example",
        "examples",
        "externalDocs",
        "operationId",
        "summary",
        "tags",
        "title",
    }
)
BOUND_KEYWORDS = (
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "minProperties",
    "maxProperties",
    "multipleOf",
)
PATTERN_KEYWORDS = ("pattern", "format")
# Compared by rules of their own; every other keyword of a schema is compared as a whole.
COMPARED_KEYWORDS = frozenset(
    {
        "$ref",
        "type",
        "enum",
        "default",
        "required",
        "properties",
        "items",
        "additionalProperties",
        *BOUND_KEYWORDS,
        *PATTERN_KEYWORDS,
    }
)
# What a Swagger 2 parameter carries beside the schema keywords it holds inline.
PARAMETER_FIELDS = frozenset(
    {
        "allowEmptyValue",
        "allowReserved",
        "content",
        "explode",
        "in",
        "name",
        "required",
        "schema",
        "style",
    }
)
# Where an API is served from: a change moves every operation for its clients.
SERVER_FIELDS = ("servers", "host", "basePath", "schemes")
# The meta-schemas a JSON Schema names as its `$schema`. A configuration file names there the
# schema it is validated by instead, `https://json.schemastore.org/tsconfig`, and is no schema.
META_SCHEMA_RE = re.compile(
    r"https?://(?:json-schema\.org/|spec\.openapis\.org/oas/3\.\d+/dialect/)"
)
MAX_DOCUMENT_DELTAS = 500
MAX_SCHEMA_DEPTH = 32
MAX_SHOWN_CHARS = 300
# A symbol is one of the document's own keys, `GET /orders/{id}` or `Order`, and every delta of
# it repeats it in the output, so a long one is cut.
MAX_SYMBOL_CHARS = 200


class _Absent:
    """A key a mapping does not have, which is not the same as one holding `null`."""


ABSENT: Any = _Absent()


def document_kind(root: JsonValue) -> str | None:
    """`openapi`, `swagger`, or `json_schema` for a document its root says is one; else None."""
    if not isinstance(root, dict):
        return None
    if str(root.get("openapi", "")).startswith("3") and any(
        key in root for key in ("paths", "components", "webhooks")
    ):
        return "openapi"
    if str(root.get("swagger", "")).startswith("2") and any(
        key in root for key in ("paths", "definitions")
    ):
        return "swagger"
    schema = root.get("$schema")
    if isinstance(schema, str) and META_SCHEMA_RE.match(schema):
        return "json_schema"
    return None


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _names(value: Any) -> frozenset[str]:
    return (
        frozenset(item for item in value if isinstance(item, str))
        if isinstance(value, list)
        else frozenset()
    )


def _shown(location: str, value: Any) -> str | None:
    """The value as evidence text: where it is, then its JSON, bounded and redacted."""
    if value is ABSENT:
        return None
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(", ", ": "))
    # Cut well past what is shown before redacting: a large subtree then costs no more to redact
    # than a small one, and a credential that starts within what is shown stays whole enough for
    # its pattern to match.
    return redact_text(f"{location}: {text}"[: MAX_SHOWN_CHARS * 4], max_chars=MAX_SHOWN_CHARS)


def _location(prefix: str, pointer: str, keyword: str = "") -> str:
    place = f"{prefix}: {pointer}" if prefix and pointer else prefix or pointer
    return f"{place} {keyword}".strip() if keyword else place or "schema"


def _child(pointer: str, name: str) -> str:
    return f"{pointer}.{name}" if pointer else name


@dataclass
class _Deltas:
    path: str
    items: list[StructuralDelta] = field(default_factory=list)
    truncated: bool = False

    def add(
        self,
        symbol: str,
        kind: str,
        old: Any,
        new: Any,
        *,
        location: str,
        context: str,
        **metadata: Any,
    ) -> None:
        if len(self.items) >= MAX_DOCUMENT_DELTAS:
            self.truncated = True
            return
        if len(symbol) > MAX_SYMBOL_CHARS:
            symbol = symbol[: MAX_SYMBOL_CHARS - 3] + "..."
        self.items.append(
            StructuralDelta(
                path=self.path,
                symbol=symbol,
                kind=kind,
                old=_shown(location, old),
                new=_shown(location, new),
                old_lines=None,
                new_lines=None,
                hunk_id=None,
                metadata={"location": location, "context": context, **metadata},
            )
        )


def _compare_schema(
    symbol: str,
    old: Any,
    new: Any,
    *,
    prefix: str,
    pointer: str,
    context: str,
    deltas: _Deltas,
    depth: int = 0,
) -> None:
    if old == new:
        return
    location = _location(prefix, pointer)
    if old is ABSENT:
        deltas.add(symbol, "schema_added", ABSENT, new, location=location, context=context)
        return
    if new is ABSENT:
        deltas.add(symbol, "schema_removed", old, ABSENT, location=location, context=context)
        return
    if depth > MAX_SCHEMA_DEPTH or not isinstance(old, dict) or not isinstance(new, dict):
        # A boolean JSON Schema, a malformed one, or one nested past the bound, as a whole.
        deltas.add(
            symbol,
            "schema_keyword_change",
            old,
            new,
            location=location,
            context=context,
            keyword="schema",
        )
        return

    def keyword_delta(keyword: str, kind: str) -> None:
        before, after = old.get(keyword, ABSENT), new.get(keyword, ABSENT)
        if before != after:
            deltas.add(
                symbol,
                kind,
                before,
                after,
                location=_location(prefix, pointer, keyword),
                context=context,
                keyword=keyword,
            )

    keyword_delta("$ref", "schema_ref_change")
    keyword_delta("type", "schema_type_change")
    keyword_delta("enum", "schema_enum_change")
    keyword_delta("default", "schema_default_change")
    for keyword in BOUND_KEYWORDS:
        keyword_delta(keyword, "schema_bound_change")
    for keyword in PATTERN_KEYWORDS:
        keyword_delta(keyword, "schema_pattern_change")
    old_required, new_required = _names(old.get("required")), _names(new.get("required"))
    if old_required != new_required:
        deltas.add(
            symbol,
            "schema_required_change",
            sorted(old_required),
            sorted(new_required),
            location=_location(prefix, pointer, "required"),
            context=context,
            keyword="required",
            became_required=sorted(new_required - old_required),
            became_optional=sorted(old_required - new_required),
        )
    old_properties, new_properties = (
        _mapping(old.get("properties")),
        _mapping(new.get("properties")),
    )
    for name in sorted(old_properties.keys() - new_properties.keys()):
        deltas.add(
            symbol,
            "schema_property_removed",
            old_properties[name],
            ABSENT,
            location=_location(prefix, _child(pointer, name)),
            context=context,
            required=name in old_required,
        )
    for name in sorted(new_properties.keys() - old_properties.keys()):
        deltas.add(
            symbol,
            "schema_property_added",
            ABSENT,
            new_properties[name],
            location=_location(prefix, _child(pointer, name)),
            context=context,
            required=name in new_required,
        )
    for name in sorted(old_properties.keys() & new_properties.keys()):
        _compare_schema(
            symbol,
            old_properties[name],
            new_properties[name],
            prefix=prefix,
            pointer=_child(pointer, name),
            context=context,
            deltas=deltas,
            depth=depth + 1,
        )
    for keyword, nested in (
        ("items", f"{pointer}[]"),
        ("additionalProperties", _child(pointer, "*")),
    ):
        before, after = old.get(keyword, ABSENT), new.get(keyword, ABSENT)
        if before == after:
            continue
        if isinstance(before, dict) and isinstance(after, dict):
            _compare_schema(
                symbol,
                before,
                after,
                prefix=prefix,
                pointer=nested,
                context=context,
                deltas=deltas,
                depth=depth + 1,
            )
        else:
            keyword_delta(keyword, "schema_keyword_change")
    for keyword in sorted((old.keys() | new.keys()) - COMPARED_KEYWORDS - DOCUMENTATION_KEYWORDS):
        if not keyword.startswith("x-"):
            keyword_delta(keyword, "schema_keyword_change")


def _compare_named(
    old: dict[str, Any], new: dict[str, Any], *, context: str, deltas: _Deltas
) -> None:
    """Schemas a document names, compared by name: each name is the symbol of its deltas."""
    for name in sorted(old.keys() | new.keys()):
        _compare_schema(
            name,
            old.get(name, ABSENT),
            new.get(name, ABSENT),
            prefix="",
            pointer="",
            context=context,
            deltas=deltas,
        )


def _compare_ref(
    symbol: str,
    old: dict[str, Any],
    new: dict[str, Any],
    *,
    location: str,
    context: str,
    deltas: _Deltas,
) -> None:
    before, after = old.get("$ref", ABSENT), new.get("$ref", ABSENT)
    if before != after:
        deltas.add(
            symbol,
            "schema_ref_change",
            before,
            after,
            location=f"{location} $ref",
            context=context,
            keyword="$ref",
        )


def _compare_content(
    symbol: str, old: Any, new: Any, *, prefix: str, context: str, deltas: _Deltas
) -> None:
    """An OpenAPI 3 `content` map: media types added or removed, and each one's schema."""
    old_media, new_media = _mapping(old), _mapping(new)
    for media in sorted(old_media.keys() - new_media.keys()):
        deltas.add(
            symbol,
            "api_media_type_removed",
            old_media[media],
            ABSENT,
            location=f"{prefix} {media}",
            context=context,
        )
    for media in sorted(new_media.keys() - old_media.keys()):
        deltas.add(
            symbol,
            "api_media_type_added",
            ABSENT,
            new_media[media],
            location=f"{prefix} {media}",
            context=context,
        )
    for media in sorted(old_media.keys() & new_media.keys()):
        before, after = _mapping(old_media[media]), _mapping(new_media[media])
        _compare_schema(
            symbol,
            before.get("schema", ABSENT),
            after.get("schema", ABSENT),
            prefix=f"{prefix} {media}",
            pointer="",
            context=context,
            deltas=deltas,
        )


def _parameters(value: Any) -> dict[str, dict[str, Any]]:
    """Parameters by where and what they are, `query limit`, or by the `$ref` standing for one."""
    parameters: dict[str, dict[str, Any]] = {}
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        key = f"$ref {item['$ref']}" if "$ref" in item else f"{item.get('in')} {item.get('name')}"
        parameters[key] = item
    return parameters


def _parameter_schema(parameter: dict[str, Any]) -> Any:
    """An OpenAPI 3 parameter's `schema`, or the keywords a Swagger 2 one holds inline."""
    if "schema" in parameter:
        return parameter["schema"]
    inline = {
        key: value
        for key, value in parameter.items()
        if key not in PARAMETER_FIELDS and key not in DOCUMENTATION_KEYWORDS
    }
    return inline or ABSENT


def _operations(root: Any) -> dict[str, dict[str, Any]]:
    """Each operation by its method and route, `GET /orders/{id}`, with its parameters merged
    over those its path declares for every method."""
    operations: dict[str, dict[str, Any]] = {}
    for route, item in _mapping(_mapping(root).get("paths")).items():
        if not isinstance(item, dict):
            continue
        shared = _parameters(item.get("parameters"))
        for method in HTTP_METHODS:
            operation = item.get(method)
            if isinstance(operation, dict):
                operations[f"{method.upper()} {route}"] = {
                    **operation,
                    "parameters": {**shared, **_parameters(operation.get("parameters"))},
                }
    return operations


def _compare_parameters(
    symbol: str, old: dict[str, dict[str, Any]], new: dict[str, dict[str, Any]], deltas: _Deltas
) -> None:
    for key in sorted(old.keys() - new.keys()):
        deltas.add(
            symbol,
            "api_parameter_removed",
            old[key],
            ABSENT,
            location=f"parameter {key}",
            context="request",
            required=old[key].get("required") is True,
        )
    for key in sorted(new.keys() - old.keys()):
        deltas.add(
            symbol,
            "api_parameter_added",
            ABSENT,
            new[key],
            location=f"parameter {key}",
            context="request",
            required=new[key].get("required") is True,
        )
    for key in sorted(old.keys() & new.keys()):
        before, after = old[key], new[key]
        if (before.get("required") is True) != (after.get("required") is True):
            deltas.add(
                symbol,
                "api_parameter_required_change",
                before.get("required", False),
                after.get("required", False),
                location=f"parameter {key} required",
                context="request",
                required=after.get("required") is True,
            )
        _compare_schema(
            symbol,
            _parameter_schema(before),
            _parameter_schema(after),
            prefix=f"parameter {key}",
            pointer="",
            context="request",
            deltas=deltas,
        )
        _compare_content(
            symbol,
            before.get("content"),
            after.get("content"),
            prefix=f"parameter {key}",
            context="request",
            deltas=deltas,
        )


def _compare_request_body(symbol: str, old: Any, new: Any, deltas: _Deltas) -> None:
    if old == new:
        return
    if not isinstance(old, dict) or not isinstance(new, dict):
        deltas.add(
            symbol, "api_request_body_change", old, new, location="request body", context="request"
        )
        return
    if (old.get("required") is True) != (new.get("required") is True):
        deltas.add(
            symbol,
            "api_request_body_required_change",
            old.get("required", False),
            new.get("required", False),
            location="request body required",
            context="request",
        )
    _compare_ref(symbol, old, new, location="request body", context="request", deltas=deltas)
    _compare_content(
        symbol,
        old.get("content"),
        new.get("content"),
        prefix="request body",
        context="request",
        deltas=deltas,
    )


def _compare_responses(symbol: str, old: Any, new: Any, deltas: _Deltas) -> None:
    old_responses, new_responses = _mapping(old), _mapping(new)
    for status in sorted(old_responses.keys() - new_responses.keys()):
        deltas.add(
            symbol,
            "api_response_removed",
            old_responses[status],
            ABSENT,
            location=f"response {status}",
            context="response",
        )
    for status in sorted(new_responses.keys() - old_responses.keys()):
        deltas.add(
            symbol,
            "api_response_added",
            ABSENT,
            new_responses[status],
            location=f"response {status}",
            context="response",
        )
    for status in sorted(old_responses.keys() & new_responses.keys()):
        before, after = old_responses[status], new_responses[status]
        if before == after:
            continue
        if not isinstance(before, dict) or not isinstance(after, dict):
            deltas.add(
                symbol,
                "schema_keyword_change",
                before,
                after,
                location=f"response {status}",
                context="response",
                keyword="response",
            )
            continue
        prefix = f"response {status}"
        _compare_ref(symbol, before, after, location=prefix, context="response", deltas=deltas)
        _compare_content(
            symbol,
            before.get("content"),
            after.get("content"),
            prefix=prefix,
            context="response",
            deltas=deltas,
        )
        # Swagger 2 gives a response its schema directly.
        _compare_schema(
            symbol,
            before.get("schema", ABSENT),
            after.get("schema", ABSENT),
            prefix=prefix,
            pointer="",
            context="response",
            deltas=deltas,
        )
        if before.get("headers", ABSENT) != after.get("headers", ABSENT):
            deltas.add(
                symbol,
                "schema_keyword_change",
                before.get("headers", ABSENT),
                after.get("headers", ABSENT),
                location=f"{prefix} headers",
                context="response",
                keyword="headers",
            )


def _compare_operation(
    symbol: str, old: dict[str, Any], new: dict[str, Any], deltas: _Deltas
) -> None:
    _compare_parameters(symbol, old["parameters"], new["parameters"], deltas)
    _compare_request_body(
        symbol, old.get("requestBody", ABSENT), new.get("requestBody", ABSENT), deltas
    )
    _compare_responses(symbol, old.get("responses"), new.get("responses"), deltas)
    before, after = old.get("security", ABSENT), new.get("security", ABSENT)
    if before != after:
        deltas.add(
            symbol, "api_security_change", before, after, location="security", context="request"
        )


def _security_schemes(root: Any, kind: str) -> dict[str, Any]:
    if kind == "openapi":
        return _mapping(_mapping(_mapping(root).get("components")).get("securitySchemes"))
    return _mapping(_mapping(root).get("securityDefinitions"))


def _compare_document_level(old: Any, new: Any, kind: str, deltas: _Deltas) -> None:
    """What applies to every operation: the security required by default, the schemes it names,
    and where the API is served from."""
    before, after = _mapping(old).get("security", ABSENT), _mapping(new).get("security", ABSENT)
    if before != after:
        deltas.add(
            "<security>",
            "api_security_change",
            before,
            after,
            location="global security",
            context="request",
        )
    old_schemes, new_schemes = _security_schemes(old, kind), _security_schemes(new, kind)
    for name in sorted(old_schemes.keys() | new_schemes.keys()):
        before, after = old_schemes.get(name, ABSENT), new_schemes.get(name, ABSENT)
        if before != after:
            deltas.add(
                "<security>",
                "api_security_change",
                before,
                after,
                location=f"security scheme {name}",
                context="request",
            )
    for name in SERVER_FIELDS:
        before, after = _mapping(old).get(name, ABSENT), _mapping(new).get(name, ABSENT)
        if before != after:
            deltas.add(
                "<servers>", "api_server_change", before, after, location=name, context="response"
            )


def _components(root: Any, kind: str) -> dict[str, Any]:
    if kind == "openapi":
        return _mapping(_mapping(_mapping(root).get("components")).get("schemas"))
    return _mapping(_mapping(root).get("definitions"))


def _json_schemas(root: Any) -> dict[str, Any]:
    """A JSON Schema's root, as `<root>`, and each schema it defines under `$defs` or
    `definitions`, by name."""
    if not isinstance(root, dict):
        return {}
    schemas: dict[str, Any] = {
        "<root>": {key: value for key, value in root.items() if key not in {"$defs", "definitions"}}
    }
    for key in ("definitions", "$defs"):
        schemas.update(_mapping(root.get(key)))
    return schemas


def compare_documents(
    path: str, old: JsonValue | None, new: JsonValue | None, kind: str
) -> tuple[list[StructuralDelta], bool]:
    """The deltas between two revisions of one API schema, and whether their budget ran out.

    A side that is None — absent, or not a document of this kind — is compared as empty, so
    everything on the other side is added or removed.
    """
    deltas = _Deltas(path)
    if kind == "json_schema":
        _compare_named(_json_schemas(old), _json_schemas(new), context="schema", deltas=deltas)
        return deltas.items, deltas.truncated
    old_operations, new_operations = _operations(old), _operations(new)
    for symbol in sorted(old_operations.keys() | new_operations.keys()):
        before, after = old_operations.get(symbol), new_operations.get(symbol)
        if after is None:
            deltas.add(
                symbol,
                "api_operation_removed",
                symbol,
                ABSENT,
                location="operation",
                context="response",
            )
        elif before is None:
            deltas.add(
                symbol,
                "api_operation_added",
                ABSENT,
                symbol,
                location="operation",
                context="response",
            )
        elif before != after:
            _compare_operation(symbol, before, after, deltas)
    _compare_document_level(old, new, kind, deltas)
    _compare_named(
        _components(old, kind), _components(new, kind), context="component", deltas=deltas
    )
    return deltas.items, deltas.truncated
