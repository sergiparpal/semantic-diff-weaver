"""A skeleton's analysis is untrusted input: it may write comments, never code or paths."""

from __future__ import annotations

import ast
import io
import json
import os
import tokenize
from pathlib import Path
from typing import Any

import pytest

from semantic_diff_weaver.errors import WeaverError
from semantic_diff_weaver.path_policy import ALLOWED_ROOTS_ENV
from semantic_diff_weaver.skeletons import generate_skeletons
from tests.conftest import git

CART = "def total(items, limit):\n    return sum(items) if len(items) <= limit else 0\n"
TEST_CART = "def test_total_sums():\n    assert True\n"
MARKER = "INJECTED"
# Every character that ends a line for Python, Git, or an editor, or reorders one for a reader.
HOSTILE = "\n\r\r\n\x00\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029\u202a\u202e\u2066\u2069\u200b"


def _hostile(text: str) -> str:
    return f'{text}{HOSTILE}"""{MARKER}()\n{MARKER} = 1  # \\\nimport os; os.system("{MARKER}")'


def _document(head: str, **overrides: Any) -> dict[str, Any]:
    obligation = {
        "id": "to-001",
        "behavior_change_ids": ["bc-001"],
        "type": "boundary",
        "priority": 90,
        "title": _hostile("Exercise the boundary"),
        "given": _hostile("Given"),
        "when": _hostile("When"),
        "then": _hostile("Then"),
        "candidate_existing_tests": [
            {"path": "tests/test_cart.py", "symbol": _hostile("test_total_sums")}
        ],
    }
    return {
        "schema_version": "1.2",
        "analysis_id": "sdw_crafted",
        "repository": {"head_commit": head},
        "behavior_changes": [
            {
                "id": "bc-001",
                "category": "boundary_change",
                "risk": "high",
                "evidence": [{"path": "src/shop/cart.py", "symbol": _hostile("Cart.total")}],
            }
        ],
        "test_obligations": [{**obligation, **overrides}],
    }


def _crafted(tmp_path: Path, document: Any) -> Path:
    path = tmp_path / "crafted.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _generate(repo: Path, path: Path, framework: str = "pytest") -> dict[str, Any]:
    return generate_skeletons(
        {
            "repo_path": str(repo),
            "analysis_path": str(path),
            "obligation_ids": ["to-001"],
            "framework": framework,
        }
    )


def _applied(repo: Path, patch: str) -> str:
    patch_file = repo.parent / f"{repo.name}.patch"
    patch_file.write_bytes(patch.encode("utf-8"))
    git(repo, "apply", str(patch_file))
    return (repo / "tests" / "test_cart.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("framework", ["pytest", "unittest"])
def test_crafted_text_reaches_the_test_file_only_as_comments(
    repo_factory, tmp_path, framework
) -> None:
    repo, _, head = repo_factory({"src/shop/cart.py": CART}, {"tests/test_cart.py": TEST_CART})
    result = _generate(repo, _crafted(tmp_path, _document(head)), framework)
    text = _applied(repo, result["patch"])
    added = text.removeprefix(TEST_CART)
    for character in HOSTILE.replace("\n", ""):
        assert character not in added, repr(character)
    # The marker appears, escaped, only inside comments: no token of code carries it.
    tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    assert any(MARKER in token.string for token in tokens)
    assert all(token.type == tokenize.COMMENT for token in tokens if MARKER in token.string)
    tree = ast.parse(text)
    new = tree.body[1:]
    if framework == "pytest":
        assert all(isinstance(node, ast.FunctionDef) for node in new)
        bodies = [node.body for node in new if isinstance(node, ast.FunctionDef)]
    else:
        assert [type(node) for node in new] == [ast.Import, ast.ClassDef]
        assert isinstance(new[1], ast.ClassDef)
        bodies = [item.body for item in new[1].body if isinstance(item, ast.FunctionDef)]
    expected = {
        "pytest": "raise NotImplementedError('Write the test for obligation to-001.')",
        "unittest": "self.fail('Write the test for obligation to-001.')",
    }[framework]
    assert [[ast.unparse(statement) for statement in body] for body in bodies] == [[expected]]


def test_a_crafted_candidate_cannot_direct_a_skeleton_into_production_code(
    repo_factory, tmp_path
) -> None:
    helper = "def helper():\n    return 1\n"
    repo, _, head = repo_factory(
        {"src/shop/cart.py": CART}, {"src/shop/test_cart.py": helper, "README.md": "x\n"}
    )
    document = _document(
        head, candidate_existing_tests=[{"path": "src/shop/test_cart.py", "symbol": "<module>"}]
    )
    result = _generate(repo, _crafted(tmp_path, document))
    assert [(item["path"], item["new_file"]) for item in result["skeletons"]] == [
        ("tests/test_cart.py", True)
    ]


@pytest.mark.parametrize(
    "candidate",
    [
        {"path": "../outside/test_cart.py", "symbol": "test_x"},
        {"path": "/tmp/test_cart.py", "symbol": "test_x"},
        {"path": "tests\\test_cart.py", "symbol": "test_x"},
    ],
)
def test_a_crafted_candidate_path_outside_the_repository_is_refused(
    repo_factory, tmp_path, candidate
) -> None:
    repo, _, head = repo_factory({"src/shop/cart.py": CART}, {"tests/test_cart.py": TEST_CART})
    document = _document(head, candidate_existing_tests=[candidate])
    with pytest.raises(WeaverError) as caught:
        _generate(repo, _crafted(tmp_path, document))
    assert caught.value.code.value == "analysis_unreadable"


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need privileges on Windows")
def test_a_symlinked_test_file_is_never_written_through(repo_factory, tmp_path) -> None:
    repo, _, _ = repo_factory({"src/shop/cart.py": CART}, {"README.md": "x\n"})
    (repo / "tests").mkdir()
    (repo / "tests" / "test_cart.py").symlink_to(Path("..") / "src" / "shop" / "cart.py")
    git(repo, "add", "tests/test_cart.py")
    git(repo, "commit", "-q", "-m", "link")
    head = git(repo, "rev-parse", "HEAD")
    result = _generate(repo, _crafted(tmp_path, _document(head)))
    assert result["skeletons"] == [] and result["patch"] == ""
    assert result["skipped"] == [
        {
            "obligation_id": "to-001",
            "reason": "its test file tests/test_cart.py is not a regular file at the analyzed commit",
        }
    ]


@pytest.mark.skipif(os.name == "nt", reason="Windows file names cannot hold control characters")
def test_a_path_a_patch_cannot_name_as_it_is_is_reported_escaped(repo_factory, tmp_path) -> None:
    hostile = "tests/\x1b[2J\u202e/test_cart.py"
    repo, _, head = repo_factory({"src/shop/cart.py": CART}, {hostile: TEST_CART})
    result = _generate(repo, _crafted(tmp_path, _document(head)))
    (skipped,) = result["skipped"]
    assert "\x1b" not in skipped["reason"] and "\u202e" not in skipped["reason"]
    assert skipped["reason"] == (
        "its test file tests/\\x1b[2J\\u202e/test_cart.py is not a path a patch names as it is"
    )


def test_an_excluded_test_file_takes_no_skeleton_and_has_no_twin(repo_factory, tmp_path) -> None:
    repo, _, head = repo_factory(
        {"src/shop/cart.py": CART}, {"tests/vendor/test_cart.py": TEST_CART}
    )
    result = _generate(repo, _crafted(tmp_path, _document(head)))
    assert result["skipped"] == [
        {
            "obligation_id": "to-001",
            "reason": "its test file tests/vendor/test_cart.py is excluded from analysis",
        }
    ]


def test_a_test_file_that_is_not_utf8_takes_no_skeleton(repo_factory, tmp_path) -> None:
    repo, _, _ = repo_factory({"src/shop/cart.py": CART}, {"README.md": "x\n"})
    (repo / "tests").mkdir()
    (repo / "tests" / "test_cart.py").write_bytes(b"# caf\xe9\ndef test_x():\n    pass\n")
    git(repo, "add", "tests/test_cart.py")
    git(repo, "commit", "-q", "-m", "latin-1")
    result = _generate(repo, _crafted(tmp_path, _document(git(repo, "rev-parse", "HEAD"))))
    assert [item["reason"] for item in result["skipped"]] == [
        "its test file tests/test_cart.py is not UTF-8 text within the file size limit"
    ]


def test_a_module_named_unlike_an_identifier_is_tested_in_its_safe_name(
    repo_factory, tmp_path
) -> None:
    repo, _, head = repo_factory({"src/my-module.py": CART}, {"tests/test_my_module.py": TEST_CART})
    document = _document(head)
    document["behavior_changes"][0]["evidence"][0]["path"] = "src/my-module.py"
    result = _generate(repo, _crafted(tmp_path, document))
    assert [(item["path"], item["new_file"]) for item in result["skeletons"]] == [
        ("tests/test_my_module.py", False)
    ]


def test_a_test_root_a_patch_cannot_name_creates_no_file(repo_factory, tmp_path) -> None:
    config = "version: 1\npaths:\n  test_roots:\n    - 'my tests'\n"
    repo, _, head = repo_factory({"src/shop/cart.py": CART}, {".semantic-diff-weaver.yaml": config})
    result = _generate(repo, _crafted(tmp_path, _document(head)))
    assert result["skipped"] == [
        {
            "obligation_id": "to-001",
            "reason": "its new test file my tests/test_cart.py is not a path a patch names as it is",
        }
    ]


def test_an_analysis_outside_the_authorized_roots_is_refused(
    repo_factory, tmp_path_factory, monkeypatch
) -> None:
    repo, _, head = repo_factory({"src/shop/cart.py": CART}, {"tests/test_cart.py": TEST_CART})
    outside = _crafted(tmp_path_factory.mktemp("outside"), _document(head))
    monkeypatch.setenv(ALLOWED_ROOTS_ENV, str(repo))
    with pytest.raises(WeaverError) as caught:
        _generate(repo, outside)
    assert caught.value.code.value == "path_outside_repository"


def test_an_analysis_of_another_repository_is_refused(repo_factory, tmp_path) -> None:
    repo, _, _ = repo_factory({"src/shop/cart.py": CART}, {"tests/test_cart.py": TEST_CART})
    with pytest.raises(WeaverError) as caught:
        _generate(repo, _crafted(tmp_path, _document("b" * 40)))
    assert caught.value.code.value == "invalid_ref"
    assert caught.value.safe_message == "The analysis's head commit is not in this repository."


def test_a_branch_named_like_the_head_commit_does_not_stand_in_for_it(
    repo_factory, tmp_path
) -> None:
    repo, base, _ = repo_factory({"src/shop/cart.py": CART}, {"tests/test_cart.py": TEST_CART})
    named = "c" * 64
    git(repo, "branch", named, base)
    if len(base) == 40:
        # Sixty-four digits name no object of a SHA-1 repository, so Git resolves the branch, and
        # only comparing the commit it returns with the one the analysis names refuses it. Forty
        # would be read as an object ID, and fail to resolve before that comparison.
        assert git(repo, "rev-parse", "--verify", f"{named}^{{commit}}") == base
    with pytest.raises(WeaverError) as caught:
        _generate(repo, _crafted(tmp_path, _document(named)))
    assert caught.value.code.value == "invalid_ref"
    assert caught.value.safe_message == "The analysis's head commit is not in this repository."


def test_the_analysis_is_bounded_by_the_baseline_limit(repo_factory, tmp_path) -> None:
    config = "version: 1\nrules:\n  max_baseline_bytes: 1024\n"
    repo, _, head = repo_factory({"src/shop/cart.py": CART}, {".semantic-diff-weaver.yaml": config})
    with pytest.raises(WeaverError, match="above the 1024-byte limit") as caught:
        _generate(repo, _crafted(tmp_path, _document(head)))
    assert caught.value.code.value == "analysis_unreadable"


@pytest.mark.parametrize(
    "overrides",
    [
        {"candidate_existing_tests": [{"path": "tests/test_x.py", "symbol": "t"}] * 26},
        {"title": "x" * 301},
        {"behavior_change_ids": []},
        {"type": "exploit"},
    ],
    ids=["candidates", "title", "behaviors", "type"],
)
def test_an_analysis_beyond_its_bounds_is_refused(repo_factory, tmp_path, overrides) -> None:
    repo, _, head = repo_factory({"src/shop/cart.py": CART}, {"tests/test_cart.py": TEST_CART})
    with pytest.raises(WeaverError) as caught:
        _generate(repo, _crafted(tmp_path, _document(head, **overrides)))
    assert caught.value.code.value == "analysis_unreadable"


def test_more_obligations_than_any_analysis_reports_are_refused(repo_factory, tmp_path) -> None:
    repo, _, head = repo_factory({"src/shop/cart.py": CART}, {"tests/test_cart.py": TEST_CART})
    document = _document(head)
    template = document["test_obligations"][0]
    document["test_obligations"] = [
        {**template, "id": f"to-{number:04d}"} for number in range(1, 1002)
    ]
    with pytest.raises(WeaverError) as caught:
        _generate(repo, _crafted(tmp_path, document))
    assert caught.value.code.value == "analysis_unreadable"


def _candidate(path: str) -> dict[str, str]:
    return {"path": path, "symbol": "<module>"}


def test_a_module_gets_one_new_test_file_wherever_its_candidates_are(
    repo_factory, tmp_path
) -> None:
    """Two files of one basename in two directories would make pytest refuse the second."""
    repo, _, head = repo_factory(
        {"src/shop/cart.py": CART},
        {"tests/unit/test_a.py": TEST_CART, "tests/integration/test_b.py": TEST_CART},
    )
    document = _document(head, candidate_existing_tests=[_candidate("tests/unit/test_a.py")])
    second = {
        **document["test_obligations"][0],
        "id": "to-002",
        "candidate_existing_tests": [_candidate("tests/integration/test_b.py")],
    }
    document["test_obligations"].append(second)
    result = generate_skeletons(
        {
            "repo_path": str(repo),
            "analysis_path": str(_crafted(tmp_path, document)),
            "obligation_ids": ["to-001", "to-002"],
        }
    )
    assert [(item["obligation_id"], item["path"]) for item in result["skeletons"]] == [
        ("to-001", "tests/unit/test_cart.py"),
        ("to-002", "tests/unit/test_cart.py"),
    ]


def test_only_a_test_file_outside_the_test_roots_places_a_new_one(repo_factory, tmp_path) -> None:
    """A colocated test places the new file beside it; a production module named so does not."""
    colocated = "def test_checkout():\n    assert True\n"
    helper = "def helper():\n    return 1\n"
    repo, _, head = repo_factory(
        {"src/shop/cart.py": CART},
        {"src/shop/test_checkout.py": colocated, "src/billing/test_invoice.py": helper},
    )
    beside = _document(head, candidate_existing_tests=[_candidate("src/shop/test_checkout.py")])
    result = _generate(repo, _crafted(tmp_path, beside))
    assert [item["path"] for item in result["skeletons"]] == ["src/shop/test_cart.py"]
    production = _document(
        head, candidate_existing_tests=[_candidate("src/billing/test_invoice.py")]
    )
    result = _generate(repo, _crafted(tmp_path, production))
    assert [item["path"] for item in result["skeletons"]] == ["tests/test_cart.py"]


def test_a_new_file_beside_a_candidate_never_overwrites_a_module(repo_factory, tmp_path) -> None:
    colocated = "def test_checkout():\n    assert True\n"
    helper = "def helper():\n    return 1\n"
    repo, _, head = repo_factory(
        {"src/shop/cart.py": CART},
        {"src/shop/test_checkout.py": colocated, "src/shop/test_cart.py": helper},
    )
    document = _document(head, candidate_existing_tests=[_candidate("src/shop/test_checkout.py")])
    result = _generate(repo, _crafted(tmp_path, document))
    assert result["skipped"] == [
        {
            "obligation_id": "to-001",
            "reason": "src/shop/test_cart.py exists and is not a test file a skeleton can be "
            "added to",
        }
    ]


def test_a_candidate_under_version_control_metadata_is_never_looked_up(
    repo_factory, tmp_path
) -> None:
    repo, _, head = repo_factory({"src/shop/cart.py": CART}, {"README.md": "x\n"})
    document = _document(head, candidate_existing_tests=[_candidate(".git/test_cart.py")])
    result = _generate(repo, _crafted(tmp_path, document))
    assert [item["path"] for item in result["skeletons"]] == ["tests/test_cart.py"]
