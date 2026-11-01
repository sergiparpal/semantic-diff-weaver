"""A changed OpenAPI document is analyzed beside the Python change, and every YAML or JSON file
that is not one is accounted for without costing the analysis its completeness."""

from __future__ import annotations

import json
from typing import Any

import semantic_diff_weaver.git_diff.limits as git_limits
from semantic_diff_weaver.service import analyze
from semantic_diff_weaver.skeletons import generate_skeletons

OPENAPI = """openapi: 3.0.3
info: {{title: Shop, version: "1"}}
paths:
  /orders/{{id}}:
    get:
      responses:
        "200":
          description: The order
          content:
            application/json:
              schema: {{$ref: "#/components/schemas/Order"}}
{delete}components:
  schemas:
    Order:
      type: object
      properties:
        note: {{type: string, maxLength: {note}}}
"""
DELETE = """    delete:
      responses:
        "204": {description: Deleted}
"""
OLD_API = OPENAPI.format(delete=DELETE, note=200)
NEW_API = OPENAPI.format(delete="", note=100)
ORDER_TESTS = (
    "def test_delete_order(client):\n"
    "    assert client.delete('/orders/1').status_code == 204\n\n\n"
    "def test_get_order(client):\n"
    "    assert client.get('/orders/1').status_code == 200\n"
)
OLD_CODE = "def allowed(x):\n    return x < 5\n"
NEW_CODE = "def allowed(x):\n    return x <= 5\n"


def _analysis(repo_factory, old: dict[str, str], new: dict[str, str], **arguments: Any):
    repo, base, head = repo_factory(old, new)
    envelope = analyze({"repo_path": str(repo), "base_ref": base, "head_ref": head, **arguments})
    assert envelope["success"] is True
    return envelope


def _findings(analysis: dict[str, Any]) -> dict[tuple[str, str], str]:
    return {
        (item["evidence"][0]["path"], item["evidence"][0]["symbol"]): item["category"]
        for item in analysis["behavior_changes"]
    }


def _candidates(analysis: dict[str, Any], symbol: str) -> list[tuple[str, str]]:
    (behavior,) = (
        item for item in analysis["behavior_changes"] if item["evidence"][0]["symbol"] == symbol
    )
    return sorted(
        {
            (test["path"], test["symbol"])
            for obligation in analysis["test_obligations"]
            if behavior["id"] in obligation["behavior_change_ids"]
            for test in obligation["candidate_existing_tests"]
        }
    )


def test_an_api_schema_is_analyzed_beside_the_python_change(repo_factory) -> None:
    envelope = _analysis(
        repo_factory,
        {
            "openapi.yaml": OLD_API,
            "src/shop/api.py": OLD_CODE,
            "package.json": '{"name": "shop", "version": "1.0.0"}\n',
            "tests/test_orders.py": ORDER_TESTS,
        },
        {
            "openapi.yaml": NEW_API,
            "src/shop/api.py": NEW_CODE,
            "package.json": '{"name": "shop", "version": "1.0.1"}\n',
        },
    )
    analysis = envelope["analysis"]
    assert analysis["scope"]["analyzed_files"] == ["openapi.yaml", "src/shop/api.py"]
    assert analysis["scope"]["excluded_counts"] == {"not_api_schema": 1}
    assert analysis["scope"]["truncated"] is False
    assert _findings(analysis) == {
        ("openapi.yaml", "DELETE /orders/{id}"): "output_contract_change",
        ("openapi.yaml", "Order"): "boundary_change",
        ("src/shop/api.py", "allowed"): "boundary_change",
    }
    evidence = {
        item["evidence"][0]["symbol"]: item["evidence"][0] for item in analysis["behavior_changes"]
    }
    assert evidence["Order"]["kind"] == "schema_bound_change"
    assert (evidence["Order"]["old"], evidence["Order"]["new"]) == (
        "note maxLength: 200",
        "note maxLength: 100",
    )
    assert evidence["Order"]["old_lines"] is None and evidence["Order"]["hunk_id"] is None
    # The removed operation's tests request its route; the one calling only `get` is about the
    # operation that stays. A reusable schema names no route, so gets no candidate.
    assert _candidates(analysis, "DELETE /orders/{id}") == [
        ("tests/test_orders.py", "test_delete_order")
    ]
    assert _candidates(analysis, "Order") == []
    assert (
        "Only committed Python source and API schemas at the resolved refs were inspected."
        in analysis["limitations"]
    )
    markdown = envelope["markdown"]
    assert "1 YAML or JSON that is not an API schema" in markdown
    assert "DELETE /orders/" in markdown


def test_a_schema_change_of_words_alone_is_said_to_change_no_contract(repo_factory) -> None:
    envelope = _analysis(
        repo_factory,
        {"openapi.yaml": OLD_API},
        {"openapi.yaml": OLD_API.replace("The order", "One order")},
        output_format="json",
    )
    assert envelope["behavior_changes"] == []
    assert envelope["scope"]["analyzed_files"] == ["openapi.yaml"]
    assert any("contained no contract change" in item for item in envelope["limitations"])
    assert not any("no included changed Python" in item for item in envelope["limitations"])


def test_yaml_and_json_that_cannot_be_read_leave_the_analysis_complete(repo_factory) -> None:
    """A lockfile past the file size limit may not be a schema at all, so it is no reason for the
    analysis to withhold a pass, as a Python file it could not read would be."""
    config = "version: 1\nrules:\n  max_file_bytes: 1024\n"
    big = json.dumps({"lock": ["x" * 64] * 32})
    envelope = _analysis(
        repo_factory,
        {".semantic-diff-weaver.yaml": config, "api.py": OLD_CODE, "data/lock.json": "{}"},
        {".semantic-diff-weaver.yaml": config, "api.py": OLD_CODE, "data/lock.json": big},
        output_format="json",
    )
    scope = envelope["scope"]
    assert scope["excluded_counts"] == {"unreadable_document": 1}
    assert scope["truncated"] is False and scope["omitted"] == []
    assert envelope["summary"]["verdict"] == "pass"
    assert "Skipped bounded source parsing for 'data/lock.json'." in envelope["warnings"]


def test_a_schema_that_no_longer_parses_leaves_the_analysis_incomplete(repo_factory) -> None:
    envelope = _analysis(
        repo_factory,
        {"openapi.yaml": OLD_API},
        {"openapi.yaml": NEW_API + "  broken: [\n"},
        output_format="json",
    )
    assert envelope["scope"]["truncated"] is True
    assert {"reason": "parse_incomplete_documents", "count": 1} in envelope["scope"]["omitted"]
    assert _findings(envelope) == {("openapi.yaml", "<document>"): "unknown_semantic_change"}
    assert envelope["summary"]["verdict"] != "pass"
    assert (
        "Could not parse the API schema 'openapi.yaml' within its budgets."
        in (envelope["warnings"])
    )


def test_schemas_are_left_out_when_configured_narrowed_or_under_a_test_root(
    repo_factory,
) -> None:
    switched_off = "version: 1\nrules:\n  analyze_api_schemas: false\n"
    off = _analysis(
        repo_factory,
        {".semantic-diff-weaver.yaml": switched_off, "openapi.yaml": OLD_API, "api.py": OLD_CODE},
        {".semantic-diff-weaver.yaml": switched_off, "openapi.yaml": NEW_API, "api.py": NEW_CODE},
        output_format="json",
    )
    assert off["scope"]["excluded_counts"] == {"unsupported_extension": 1}
    assert off["scope"]["analyzed_files"] == ["api.py"]

    narrowed = _analysis(
        repo_factory,
        {"openapi.yaml": OLD_API, "src/api.py": OLD_CODE},
        {"openapi.yaml": NEW_API, "src/api.py": NEW_CODE},
        output_format="json",
        include=["src/**"],
    )
    assert narrowed["scope"]["excluded_counts"] == {"path_filter": 1}

    fixture = _analysis(
        repo_factory,
        {"tests/fixtures/openapi.yaml": OLD_API, "api.py": OLD_CODE},
        {"tests/fixtures/openapi.yaml": NEW_API, "api.py": NEW_CODE},
        output_format="json",
    )
    assert fixture["scope"]["excluded_counts"] == {"test_file": 1}
    assert fixture["scope"]["analyzed_files"] == ["api.py"]


def test_documents_past_their_count_are_omitted_critical_paths_first(
    repo_factory, monkeypatch
) -> None:
    monkeypatch.setattr(git_limits, "MAX_DOCUMENTS", 1)
    config = "version: 1\ncritical_paths:\n  - pattern: 'z/**'\n    weight: 90\n"
    envelope = _analysis(
        repo_factory,
        {
            ".semantic-diff-weaver.yaml": config,
            "a/openapi.yaml": OLD_API,
            "z/openapi.yaml": OLD_API,
        },
        {
            ".semantic-diff-weaver.yaml": config,
            "a/openapi.yaml": NEW_API,
            "z/openapi.yaml": NEW_API,
        },
        output_format="json",
    )
    scope = envelope["scope"]
    assert scope["analyzed_files"] == ["z/openapi.yaml"]
    assert scope["truncated"] is True
    assert {"reason": "document_limit", "count": 1} in scope["omitted"]
    assert (
        "Only 1 changed YAML, JSON, and TOML files were read, those named as configuration first."
        in envelope["warnings"]
    )
    assert envelope["summary"]["verdict"] != "pass"


def test_documents_past_their_byte_total_are_omitted(repo_factory, monkeypatch) -> None:
    monkeypatch.setattr(git_limits, "MAX_DOCUMENT_BLOB_BYTES", len(OLD_API) + 1)
    envelope = _analysis(
        repo_factory,
        {"a/openapi.yaml": OLD_API, "z/openapi.yaml": OLD_API},
        {"a/openapi.yaml": NEW_API, "z/openapi.yaml": NEW_API},
        output_format="json",
    )
    scope = envelope["scope"]
    assert scope["truncated"] is True
    assert {"reason": "document_limit", "count": 2} in scope["omitted"]
    assert scope["analyzed_files"] == [] and envelope["summary"]["verdict"] != "pass"


def test_a_skeleton_for_an_api_obligation_goes_beside_its_candidate(repo_factory) -> None:
    repo, base, head = repo_factory(
        {"openapi.yaml": OLD_API, "tests/test_orders.py": ORDER_TESTS},
        {"openapi.yaml": NEW_API},
    )
    arguments = {"repo_path": str(repo), "base_ref": base, "head_ref": head}
    analysis = analyze({**arguments, "output_format": "json"})
    (removed,) = (
        item["id"]
        for item in analysis["behavior_changes"]
        if item["evidence"][0]["symbol"] == "DELETE /orders/{id}"
    )
    obligation = next(
        item["id"]
        for item in analysis["test_obligations"]
        if removed in item["behavior_change_ids"]
    )
    result = generate_skeletons({**arguments, "obligation_ids": [obligation]})
    (skeleton,) = result["skeletons"]
    assert skeleton["path"] == "tests/test_openapi.py" and skeleton["new_file"] is True
    assert skeleton["test"].startswith("test_delete_orders_id")
    assert "tests/test_orders.py::test_delete_order" in result["patch"]
