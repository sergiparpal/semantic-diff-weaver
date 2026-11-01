"""Test skeletons: names, comments, placement rules, and the diff that adds them."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from semantic_diff_weaver.errors import ErrorCode, WeaverError
from semantic_diff_weaver.skeletons import (
    MAX_LISTED_BEHAVIORS,
    MAX_TEST_NAME_CHARS,
    Framework,
    _Analysis,
    _analysis_arguments,
    _comment,
    _escaped,
    _file_diff,
    _load_analysis,
    _module_bindings,
    _module_stem,
    _new_path,
    _render,
    _request,
    _Target,
    _test_file_names,
    _test_name,
    generate_skeletons,
)

HEAD = "a" * 40


def _behavior(symbol: str | None = "Cart.total", path: str = "src/shop/cart.py") -> dict[str, Any]:
    return {
        "id": "bc-001",
        "category": "boundary_change",
        "risk": "high",
        "evidence": [{"path": path, "symbol": symbol, "kind": "comparison_change"}],
    }


def _obligation(**overrides: Any) -> dict[str, Any]:
    return {
        "id": "to-001",
        "behavior_change_ids": ["bc-001"],
        "type": "boundary",
        "priority": 87,
        "title": "Exercise the exact changed boundary",
        "given": "An input exactly at the changed boundary",
        "when": "The changed operation is invoked",
        "then": "The newly inferred inclusive or exclusive outcome is observed",
        "candidate_existing_tests": [],
        **overrides,
    }


def _document(*obligations: dict[str, Any], behaviors: list[dict[str, Any]] | None = None) -> dict:
    return {
        "schema_version": "1.2",
        "analysis_id": "sdw_unit",
        "repository": {"head_commit": HEAD},
        "behavior_changes": behaviors if behaviors is not None else [_behavior()],
        "test_obligations": list(obligations or [_obligation()]),
    }


def _parsed(document: dict[str, Any]) -> _Analysis:
    return _Analysis.model_validate(document)


def _target(existing: str | None, *obligations: dict[str, Any]) -> _Target:
    analysis = _parsed(_document(*obligations))
    target = _Target("tests/test_cart.py", existing)
    behaviors = {item.id: item for item in analysis.behavior_changes}
    for item in analysis.test_obligations:
        target.obligations.append(
            (item, tuple(behaviors[behavior] for behavior in item.behavior_change_ids))
        )
    return target


@pytest.mark.parametrize(
    "character", ["\n", "\r", "\x00", "\x0b", "\x0c", "\x1c", "\x85", "\u2028", "\u202e", "\u2066"]
)
def test_a_character_that_could_end_or_reorder_a_comment_line_is_escaped(character: str) -> None:
    escaped = _escaped(f"before{character}after")
    assert character not in escaped
    assert escaped == f"before{ascii(character)[1:-1]}after"


def test_printable_text_is_kept_as_it_is() -> None:
    assert _escaped('Ünïcode café — ok # "quotes" and \\ backslash') == (
        'Ünïcode café — ok # "quotes" and \\ backslash'
    )


def test_every_comment_line_is_a_comment_and_nothing_else() -> None:
    text = "word " * 60 + "\nimport os\u2028os.system('x')"
    lines = _comment("    ", text)
    assert len(lines) > 1
    for line in lines:
        assert line.startswith("    # ")
        assert line.endswith("\n")
        assert line.count("\n") == 1
        assert len(line) <= 89


@pytest.mark.parametrize(
    ("path", "stem"),
    [
        ("src/shop/cart.py", "cart"),
        ("src/shop/_common.py", "common"),
        ("pkg/__main__.py", "main"),
        ("src/shop/__init__.py", "shop"),
        ("__init__.py", "init"),
    ],
)
def test_the_module_is_named_as_its_test_file_names_it(path: str, stem: str) -> None:
    assert _module_stem(path) == stem


def test_a_new_test_file_has_a_safe_name_in_its_directory() -> None:
    assert _new_path("tests/unit", "cart") == "tests/unit/test_cart.py"
    assert _new_path("tests", "my-module") == "tests/test_my_module.py"
    assert _new_path(".", "cart") == "test_cart.py"


def test_a_test_file_is_found_by_any_name_it_could_have() -> None:
    assert _test_file_names("cart") == ("test_cart.py", "cart_test.py")
    # A module whose name is no identifier is tested in the file a new one would be named.
    assert _test_file_names("my-module") == (
        "test_my-module.py",
        "my-module_test.py",
        "test_my_module.py",
    )


def test_a_test_name_joins_the_symbol_and_the_title_in_snake_case() -> None:
    analysis = _parsed(_document(_obligation()))
    obligation, behavior = analysis.test_obligations[0], analysis.behavior_changes[0]
    taken: set[str] = set()
    assert _test_name(obligation, behavior, taken) == (
        "test_cart_total_exercise_the_exact_changed_boundary"
    )
    # A second test of the same obligation and symbol never rebinds the first one's name.
    assert _test_name(obligation, behavior, taken) == (
        "test_cart_total_exercise_the_exact_changed_boundary_2"
    )


def test_a_test_name_is_bounded_and_ascii() -> None:
    analysis = _parsed(
        _document(
            _obligation(title="Vérifier " + "très " * 40),
            behaviors=[_behavior("RetryPolicy.should_retry")],
        )
    )
    name = _test_name(analysis.test_obligations[0], analysis.behavior_changes[0], set())
    assert name.startswith("test_retry_policy_should_retry_verifier_tres")
    assert len(name) <= MAX_TEST_NAME_CHARS
    assert name.isascii() and name.isidentifier()


def test_a_module_level_change_or_a_wordless_title_still_names_a_test() -> None:
    analysis = _parsed(
        _document(_obligation(title="¿¡ — !?"), behaviors=[_behavior(symbol="<module>")])
    )
    name = _test_name(analysis.test_obligations[0], analysis.behavior_changes[0], set())
    assert name == "test_obligation_001"


def test_the_bindings_of_a_module_include_how_it_names_test_case() -> None:
    names, test_case = _module_bindings(
        ast.parse(
            "import os, unittest as ut\nfrom x import y as z\nTOTAL = 1\n"
            "if os.name:\n    LATER = 2\n\ndef test_a(): pass\n"
        )
    )
    assert {"os", "ut", "z", "TOTAL", "test_a"} <= names
    assert test_case == "ut.TestCase"
    assert _module_bindings(ast.parse("from unittest import TestCase as Case\n"))[1] == "Case"
    assert _module_bindings(ast.parse("import unittest.mock\n"))[1] == "unittest.TestCase"
    assert _module_bindings(ast.parse("from unittest import mock\n"))[1] is None


def _only_skeleton_statements(source: str) -> list[ast.stmt]:
    """The statements each skeleton holds, after checking that is all the source defines."""
    tree = ast.parse(source)
    bodies: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            bodies.extend(node.body)
        elif isinstance(node, ast.ClassDef):
            assert all(isinstance(item, ast.FunctionDef) for item in node.body)
            bodies.extend(statement for item in node.body for statement in item.body)
        else:
            assert isinstance(node, ast.Import)
            assert [alias.name for alias in node.names] == ["unittest"]
    return bodies


def test_a_pytest_skeleton_fails_until_it_is_written() -> None:
    rendered = _render(_target(None, _obligation()), Framework.PYTEST, "sdw_unit")
    assert not isinstance(rendered, str)
    (statement,) = _only_skeleton_statements(rendered.addition)
    assert isinstance(statement, ast.Raise)
    assert (
        ast.unparse(statement)
        == "raise NotImplementedError('Write the test for obligation to-001.')"
    )
    assert rendered.skeletons[0].test == "test_cart_total_exercise_the_exact_changed_boundary"
    assert rendered.skeletons[0].new_file is True
    assert "# Given: An input exactly at the changed boundary\n" in rendered.addition
    assert "# No candidate existing test was found.\n" in rendered.addition
    assert "sdw_unit" in rendered.addition


def test_a_unittest_skeleton_fails_until_it_is_written() -> None:
    rendered = _render(_target(None, _obligation()), Framework.UNITTEST, "sdw_unit")
    assert not isinstance(rendered, str)
    (statement,) = _only_skeleton_statements(rendered.addition)
    assert ast.unparse(statement) == "self.fail('Write the test for obligation to-001.')"
    assert rendered.addition.count("import unittest\n") == 1
    assert "class TestCartObligations(unittest.TestCase):\n" in rendered.addition
    assert rendered.skeletons[0].test == (
        "TestCartObligations.test_cart_total_exercise_the_exact_changed_boundary"
    )


def test_unittest_skeletons_reuse_the_files_own_test_case_name() -> None:
    existing = "from unittest import TestCase\n\n\nclass TestCartObligations(TestCase):\n    pass\n"
    rendered = _render(_target(existing, _obligation()), Framework.UNITTEST, "sdw_unit")
    assert not isinstance(rendered, str)
    assert "import unittest" not in rendered.addition
    # The file's own class keeps its name; the new one takes the next.
    assert "class TestCartObligations_2(TestCase):\n" in rendered.addition


def test_a_skeleton_never_takes_an_existing_tests_name() -> None:
    existing = "def test_cart_total_exercise_the_exact_changed_boundary():\n    assert True\n"
    rendered = _render(_target(existing, _obligation()), Framework.PYTEST, "sdw_unit")
    assert not isinstance(rendered, str)
    assert rendered.skeletons[0].test == "test_cart_total_exercise_the_exact_changed_boundary_2"
    assert rendered.skeletons[0].new_file is False


def test_a_test_file_that_does_not_parse_takes_no_skeleton() -> None:
    assert _render(_target("def broken(:\n", _obligation()), Framework.PYTEST, "sdw_unit") == (
        "its test file tests/test_cart.py does not parse as Python"
    )


def test_the_comments_list_the_behaviors_and_candidates_of_an_obligation() -> None:
    behaviors = [
        _behavior(),
        {**_behavior("Cart.discount"), "id": "bc-002", "category": "validation_change"},
    ]
    candidates = [
        {"path": "tests/test_cart.py", "symbol": "test_total"},
        {"path": "tests/test_checkout.py", "symbol": "<module>"},
    ]
    document = _document(
        _obligation(behavior_change_ids=["bc-001", "bc-002"], candidate_existing_tests=candidates),
        behaviors=behaviors,
    )
    target = _Target("tests/test_cart.py", None)
    analysis = _parsed(document)
    target.obligations.append((analysis.test_obligations[0], tuple(analysis.behavior_changes)))
    rendered = _render(target, Framework.PYTEST, "sdw_unit")
    assert not isinstance(rendered, str)
    assert (
        "    # Behaviors:\n"
        "    #   bc-001, boundary_change at high risk, Cart.total in src/shop/cart.py\n"
        "    #   bc-002, validation_change at high risk, Cart.discount in src/shop/cart.py\n"
        "    # Candidate existing tests, unverified:\n"
        "    #   tests/test_cart.py::test_total\n"
        "    #   tests/test_checkout.py::<module>\n"
    ) in rendered.addition


def test_the_comments_list_a_bounded_number_of_behaviors() -> None:
    behaviors = [
        {**_behavior(f"Cart.step_{number}"), "id": f"bc-{number:03d}"}
        for number in range(1, MAX_LISTED_BEHAVIORS + 2)
    ]
    ids = [item["id"] for item in behaviors]
    analysis = _parsed(_document(_obligation(behavior_change_ids=ids), behaviors=behaviors))
    target = _Target("tests/test_cart.py", None)
    target.obligations.append((analysis.test_obligations[0], tuple(analysis.behavior_changes)))
    rendered = _render(target, Framework.PYTEST, "sdw_unit")
    assert not isinstance(rendered, str)
    assert "Cart.step_10 in" in rendered.addition
    assert "Cart.step_11" not in rendered.addition
    assert "    #   and 1 more\n" in rendered.addition


def test_an_invalid_request_is_refused_before_anything_is_read() -> None:
    request = {"repo_path": ".", "analysis_path": "a.json", "obligation_ids": ["to-001"]}
    with pytest.raises(WeaverError, match="Invalid skeleton argument at framework") as caught:
        generate_skeletons({**request, "framework": "nose"})
    assert caught.value.code is ErrorCode.CONFIGURATION_ERROR
    with pytest.raises(WeaverError, match="Invalid skeleton argument at obligation_ids"):
        generate_skeletons({**request, "obligation_ids": []})


@pytest.mark.parametrize(
    ("existing", "header"),
    [
        # Three lines of context, then two blank lines and the addition.
        ("a\nb\nc\nd\n", "@@ -2,3 +2,6 @@"),
        # The last line is removed and added back with its line feed.
        ("a\nb", "@@ -1,2 +1,5 @@"),
        ("", "@@ -0,0 +1,1 @@"),
        # One blank line is already there, so one more is added.
        ("a\n\n", "@@ -1,2 +1,4 @@"),
    ],
)
def test_the_hunk_counts_the_lines_it_keeps_removes_and_adds(existing: str, header: str) -> None:
    # Git applies these in the integration tests; this pins the hunk's arithmetic.
    diff = _file_diff("tests/test_x.py", existing, "x = 1\n")
    assert next(line for line in diff.splitlines() if line.startswith("@@")) == header


def test_a_file_without_a_final_line_feed_gains_one_before_the_skeletons() -> None:
    diff = _file_diff("tests/test_x.py", "a\nb", "x = 1\n")
    assert diff.endswith(" a\n-b\n\\ No newline at end of file\n+b\n+\n+\n+x = 1\n")


def test_a_crlf_file_is_added_to_in_crlf() -> None:
    diff = _file_diff("tests/test_x.py", "a\r\nb\r\n", "x = 1\n")
    assert diff.endswith(" a\r\n b\r\n+\r\n+\r\n+x = 1\r\n")


def test_a_new_file_is_created_whole() -> None:
    assert _file_diff("tests/test_x.py", None, "x = 1\ny = 2\n") == (
        "diff --git a/tests/test_x.py b/tests/test_x.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/tests/test_x.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+x = 1\n"
        "+y = 2\n"
    )


def _write(tmp_path: Path, payload: Any) -> Path:
    path = tmp_path / "analysis.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


def test_the_analysis_is_read_from_either_json_format(tmp_path: Path) -> None:
    document = _document(_obligation())
    assert _load_analysis(_write(tmp_path, document), 1_000_000).analysis_id == "sdw_unit"
    wrapped = {"success": True, "analysis": document, "markdown": "# brief"}
    assert _load_analysis(_write(tmp_path, wrapped), 1_000_000).analysis_id == "sdw_unit"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("{not json", "not valid JSON"),
        ({"success": True, "markdown": "# brief"}, "holds no analysis"),
        ({**_document(_obligation()), "schema_version": "2.0"}, "schema version '2.0'"),
        (_document(_obligation(behavior_change_ids=["bc-404"])), "expected shape"),
        (_document(_obligation(), _obligation()), "expected shape"),
        (_document(behaviors=[_behavior(), _behavior()]), "expected shape"),
        (_document(_obligation(id="to-1")), "expected shape"),
        (_document(behaviors=[_behavior(path="../outside.py")]), "expected shape"),
        (_document(behaviors=[_behavior(path="/etc/passwd")]), "expected shape"),
    ],
)
def test_an_unusable_analysis_is_a_stable_error(tmp_path: Path, payload: Any, message: str) -> None:
    with pytest.raises(WeaverError, match=message) as caught:
        _load_analysis(_write(tmp_path, payload), 1_000_000)
    assert caught.value.code is ErrorCode.ANALYSIS_UNREADABLE


def test_the_analysis_is_bounded_before_it_is_parsed(tmp_path: Path) -> None:
    with pytest.raises(WeaverError, match="above the 100-byte limit") as caught:
        _load_analysis(_write(tmp_path, _document(_obligation())), 100)
    assert caught.value.code is ErrorCode.ANALYSIS_UNREADABLE
    with pytest.raises(WeaverError, match="not a regular file"):
        _load_analysis(tmp_path, 1_000_000)


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ({}, "No analysis was named: pass analysis_path, or base_ref to analyze again."),
        ({"head_ref": "HEAD"}, "No analysis was named"),
        (
            {"analysis_path": "a.json", "base_ref": "main"},
            "A saved analysis takes no base_ref: pass analysis_path or the analysis arguments.",
        ),
        ({"analysis_path": "a.json", "include": ["src/**"]}, "A saved analysis takes no include"),
        ({"analysis_path": "a.json", "coverage_report": "c.json"}, "takes no coverage_report"),
    ],
)
def test_exactly_one_analysis_is_named(source: dict[str, Any], message: str) -> None:
    request = {"repo_path": ".", "obligation_ids": ["to-001"], **source}
    with pytest.raises(WeaverError, match=message) as caught:
        _request(request)
    assert caught.value.code is ErrorCode.CONFIGURATION_ERROR
    assert "analysis_path" in caught.value.remediation and "base_ref" in caught.value.remediation


def test_an_analysis_run_again_is_given_the_analysis_arguments_only() -> None:
    request = _request(
        {
            "repo_path": "repo",
            "obligation_ids": ["to-001"],
            "framework": "unittest",
            "base_ref": "main",
            "include": ["src/**"],
            "coverage_report": "coverage.json",
        }
    )
    assert _analysis_arguments(request) == {
        "repo_path": "repo",
        "output_format": "json",
        "base_ref": "main",
        "include": ["src/**"],
        "coverage_report": "coverage.json",
    }


def test_a_saved_analysis_and_one_run_again_are_both_requests() -> None:
    saved = _request({"repo_path": ".", "obligation_ids": ["to-001"], "analysis_path": "a.json"})
    again = _request({"repo_path": ".", "obligation_ids": ["to-001"], "base_ref": "main"})
    assert (saved.analysis_path, saved.base_ref) == ("a.json", None)
    assert (again.analysis_path, again.base_ref, again.head_ref) == (None, "main", None)
