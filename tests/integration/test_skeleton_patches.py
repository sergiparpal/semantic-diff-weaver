"""Test skeletons end to end: a real analysis, its patch, and `git apply` on the repository."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from semantic_diff_weaver.cli import EXIT_ANALYSIS_ERROR, EXIT_ARGUMENT_ERROR, EXIT_SUCCESS, main
from semantic_diff_weaver.service import analyze
from semantic_diff_weaver.skeletons import generate_skeletons
from tests.conftest import git

CART_OLD = "def total(items, limit):\n    return sum(items) if len(items) < limit else 0\n"
CART_NEW = "def total(items, limit):\n    return sum(items) if len(items) <= limit else 0\n"
TEST_CART = (
    "from shop.cart import total\n\n\ndef test_total_sums():\n    assert total([1, 2], 5) == 3\n"
)

Factory = Callable[..., tuple[Path, str, str]]


def _analyzed(
    factory: Factory, tmp_path: Path, files: dict[str, str]
) -> tuple[Path, Path, dict[str, Any]]:
    repo, base, head = factory(
        {**files, "src/shop/cart.py": CART_OLD}, {"src/shop/cart.py": CART_NEW}
    )
    analysis = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}
    )
    path = tmp_path / "analysis.json"
    path.write_text(json.dumps(analysis), encoding="utf-8")
    return repo, path, analysis


def _ids(analysis: dict[str, Any]) -> list[str]:
    return [item["id"] for item in analysis["test_obligations"]]


def _skeletons(repo: Path, path: Path, ids: list[str], framework: str = "pytest") -> dict:
    return generate_skeletons(
        {
            "repo_path": str(repo),
            "analysis_path": str(path),
            "obligation_ids": ids,
            "framework": framework,
        }
    )


def _apply(repo: Path, patch: str) -> None:
    patch_file = repo.parent / f"{repo.name}.patch"
    patch_file.write_bytes(patch.encode("utf-8"))
    git(repo, "apply", "--whitespace=nowarn", str(patch_file))


def _run(repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *arguments],
        cwd=repo,
        capture_output=True,
        encoding="utf-8",
        check=False,
        shell=False,
        env={**os.environ, "PYTHONPATH": "src", "PYTHONDONTWRITEBYTECODE": "1"},
    )


def test_skeletons_are_added_to_the_modules_test_file(repo_factory, tmp_path) -> None:
    repo, path, analysis = _analyzed(repo_factory, tmp_path, {"tests/test_cart.py": TEST_CART})
    result = _skeletons(repo, path, _ids(analysis))
    assert result["success"] is True
    assert result["skipped"] == [] and result["warnings"] == []
    assert {item["path"] for item in result["skeletons"]} == {"tests/test_cart.py"}
    assert all(item["new_file"] is False for item in result["skeletons"])
    _apply(repo, result["patch"])
    text = (repo / "tests" / "test_cart.py").read_text(encoding="utf-8")
    assert text.startswith(TEST_CART)
    defined = {node.name for node in ast.parse(text).body if isinstance(node, ast.FunctionDef)}
    assert {"test_total_sums", *(item["test"] for item in result["skeletons"])} == defined
    assert git(repo, "status", "--porcelain") == "M tests/test_cart.py"


def test_a_module_without_a_test_file_gets_one_in_the_first_test_root(
    repo_factory, tmp_path
) -> None:
    repo, path, analysis = _analyzed(repo_factory, tmp_path, {"README.md": "shop\n"})
    result = _skeletons(repo, path, _ids(analysis)[:1])
    (skeleton,) = result["skeletons"]
    assert (skeleton["path"], skeleton["new_file"]) == ("tests/test_cart.py", True)
    assert result["patch"].startswith(
        "diff --git a/tests/test_cart.py b/tests/test_cart.py\nnew file mode 100644\n"
    )
    _apply(repo, result["patch"])
    assert ast.parse((repo / "tests" / "test_cart.py").read_text(encoding="utf-8"))


def test_a_production_module_named_like_a_test_is_not_a_test_file(repo_factory, tmp_path) -> None:
    """`test_mapper.py` is production code here: a name is a test file's only once it tests."""
    helper = "def helper():\n    return 1\n"
    repo, path, analysis = _analyzed(repo_factory, tmp_path, {"src/shop/test_cart.py": helper})
    result = _skeletons(repo, path, _ids(analysis)[:1])
    assert [item["path"] for item in result["skeletons"]] == ["tests/test_cart.py"]
    _apply(repo, result["patch"])
    assert (repo / "src" / "shop" / "test_cart.py").read_text(encoding="utf-8") == helper


@pytest.mark.parametrize(
    "existing",
    [
        TEST_CART.rstrip("\n"),
        TEST_CART.replace("\n", "\r\n"),
        TEST_CART + "\n\n\n",
        "",
    ],
    ids=["no-final-line-feed", "crlf", "trailing-blank-lines", "empty"],
)
def test_the_patch_applies_to_any_test_file_ending(repo_factory, tmp_path, existing) -> None:
    repo, path, analysis = _analyzed(repo_factory, tmp_path, {"tests/test_cart.py": existing})
    result = _skeletons(repo, path, _ids(analysis))
    _apply(repo, result["patch"])
    raw = (repo / "tests" / "test_cart.py").read_bytes().decode("utf-8")
    # The existing text is kept as it was, gaining only the line feed its last line lacked.
    assert raw.startswith(existing if not existing or existing.endswith("\n") else f"{existing}\n")
    if "\r\n" in existing:
        assert raw.count("\n") == raw.count("\r\n")
    else:
        assert "\r" not in raw
    assert len(ast.parse(raw).body) >= len(result["skeletons"])


def test_pytest_skeletons_fail_until_they_are_written(repo_factory, tmp_path) -> None:
    repo, path, analysis = _analyzed(repo_factory, tmp_path, {"tests/test_cart.py": TEST_CART})
    result = _skeletons(repo, path, _ids(analysis))
    _apply(repo, result["patch"])
    run = _run(repo, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_cart.py")
    assert run.returncode == 1, run.stdout
    assert f"{len(result['skeletons'])} failed, 1 passed" in run.stdout
    assert "NotImplementedError: Write the test for obligation to-001." in run.stdout


def test_unittest_skeletons_fail_until_they_are_written(repo_factory, tmp_path) -> None:
    existing = (
        "import unittest\n\nfrom shop.cart import total\n\n\n"
        "class TestTotal(unittest.TestCase):\n"
        "    def test_sums(self):\n"
        "        self.assertEqual(total([1, 2], 5), 3)\n"
    )
    repo, path, analysis = _analyzed(repo_factory, tmp_path, {"tests/test_cart.py": existing})
    result = _skeletons(repo, path, _ids(analysis), framework="unittest")
    assert result["patch"].count("import unittest") == 0
    _apply(repo, result["patch"])
    run = _run(repo, "-m", "unittest", "discover", "-s", "tests")
    assert run.returncode == 1
    assert f"Ran {len(result['skeletons']) + 1} tests" in run.stderr
    assert f"FAILED (failures={len(result['skeletons'])})" in run.stderr
    assert "AssertionError: Write the test for obligation to-001." in run.stderr


def test_skeletons_follow_the_analysis_order_once_each(repo_factory, tmp_path) -> None:
    repo, path, analysis = _analyzed(repo_factory, tmp_path, {"tests/test_cart.py": TEST_CART})
    ids = _ids(analysis)
    result = _skeletons(repo, path, [*reversed(ids), ids[0]])
    assert [item["obligation_id"] for item in result["skeletons"]] == ids


def test_an_analysis_of_another_commit_than_head_is_named(repo_factory, tmp_path) -> None:
    repo, path, analysis = _analyzed(repo_factory, tmp_path, {"tests/test_cart.py": TEST_CART})
    (repo / "README.md").write_text("later\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-q", "-m", "later")
    result = _skeletons(repo, path, _ids(analysis)[:1])
    head = analysis["repository"]["head_commit"]
    assert result["warnings"] == [
        f"The analysis is of commit {head[:12]}, not of HEAD; the patch adds to that commit's "
        "test files."
    ]
    _apply(repo, result["patch"])


def test_an_unborn_head_needs_no_warning(repo_factory, tmp_path) -> None:
    repo, path, analysis = _analyzed(repo_factory, tmp_path, {"tests/test_cart.py": TEST_CART})
    git(repo, "checkout", "-q", "--orphan", "unborn")
    result = _skeletons(repo, path, _ids(analysis)[:1])
    assert result["warnings"] == []
    assert [item["path"] for item in result["skeletons"]] == ["tests/test_cart.py"]


def test_the_cli_prints_the_patch_and_writes_nothing(repo_factory, tmp_path, capsys) -> None:
    repo, path, analysis = _analyzed(repo_factory, tmp_path, {"tests/test_cart.py": TEST_CART})
    arguments = ["skeletons", "--repo", str(repo), "--analysis", str(path)]
    code = main([*arguments, "--obligation", _ids(analysis)[0]])
    captured = capsys.readouterr()
    assert code == EXIT_SUCCESS
    assert captured.out.startswith("diff --git a/tests/test_cart.py b/tests/test_cart.py\n")
    assert captured.err == ""
    assert git(repo, "status", "--porcelain") == ""
    _apply(repo, captured.out)


def test_the_cli_writes_a_warning_to_stderr_beside_the_patch(
    repo_factory, tmp_path, capsys
) -> None:
    repo, path, analysis = _analyzed(repo_factory, tmp_path, {"tests/test_cart.py": TEST_CART})
    (repo / "README.md").write_text("later\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-q", "-m", "later")
    arguments = ["skeletons", "--repo", str(repo), "--analysis", str(path)]
    code = main([*arguments, "--obligation", _ids(analysis)[0]])
    captured = capsys.readouterr()
    assert code == EXIT_SUCCESS
    assert captured.err.startswith("semantic-diff-weaver: The analysis is of commit ")
    assert captured.out.startswith("diff --git a/tests/test_cart.py b/tests/test_cart.py\n")


def test_the_cli_never_widens_an_operators_roots(repo_factory, tmp_path, capsys) -> None:
    """The suite's autouse fixture sets the roots, as an operator would."""
    repo, path, analysis = _analyzed(repo_factory, tmp_path, {"tests/test_cart.py": TEST_CART})
    arguments = ["skeletons", "--repo", str(repo), "--analysis", str(path)]
    code = main([*arguments, "--obligation", _ids(analysis)[0], "--allow-root", str(tmp_path)])
    assert code == EXIT_ARGUMENT_ERROR
    assert "--allow-root cannot widen" in capsys.readouterr().err


def test_the_cli_prints_the_json_result_on_request(repo_factory, tmp_path, capsys) -> None:
    repo, path, analysis = _analyzed(repo_factory, tmp_path, {"tests/test_cart.py": TEST_CART})
    arguments = ["skeletons", "--repo", str(repo), "--analysis", str(path), "--format", "json"]
    code = main([*arguments, "--obligation", _ids(analysis)[0], "--framework", "unittest"])
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_SUCCESS
    assert payload["framework"] == "unittest"
    assert payload["skeletons"][0]["test"].startswith("TestCartObligations.test_total_")
    assert payload["patch"].startswith("diff --git ")


def test_the_cli_reports_errors_with_their_stable_code(repo_factory, tmp_path, capsys) -> None:
    repo, path, _ = _analyzed(repo_factory, tmp_path, {"tests/test_cart.py": TEST_CART})
    arguments = ["skeletons", "--repo", str(repo), "--analysis", str(path)]
    assert main([*arguments, "--obligation", "to-999"]) == EXIT_ANALYSIS_ERROR
    assert (
        "configuration_error: The analysis lists no obligation to-999." in capsys.readouterr().err
    )
    assert main([*arguments, "--obligation", "1"]) == EXIT_ARGUMENT_ERROR
    assert "is not an obligation ID such as to-001" in capsys.readouterr().err
    many = [item for number in range(101) for item in ("--obligation", f"to-{number:03d}")]
    assert main([*arguments, *many]) == EXIT_ARGUMENT_ERROR
    assert "--obligation accepts at most 100 IDs." in capsys.readouterr().err
    assert main(["skeletons", "--repo", str(repo)]) == EXIT_ARGUMENT_ERROR


def test_the_cli_fails_when_no_obligation_gets_a_skeleton(repo_factory, tmp_path, capsys) -> None:
    repo, path, analysis = _analyzed(repo_factory, tmp_path, {"tests/test_cart.py": "def (:\n"})
    code = main(
        [
            "skeletons",
            "--repo",
            str(repo),
            "--analysis",
            str(path),
            "--obligation",
            _ids(analysis)[0],
        ]
    )
    captured = capsys.readouterr()
    assert code == EXIT_ANALYSIS_ERROR
    assert captured.out == ""
    assert (
        f"no skeleton for {_ids(analysis)[0]}: its test file tests/test_cart.py does not parse as "
        "Python."
    ) in captured.err
    assert "no approved obligation could be given a skeleton." in captured.err


def test_the_subcommand_leaves_the_analysis_command_as_it_was(repo_factory, capsys) -> None:
    repo, base, head = repo_factory({"src/shop/cart.py": CART_OLD}, {"src/shop/cart.py": CART_NEW})
    assert main(["--repo", str(repo), "--base", base, "--head", head]) == EXIT_SUCCESS
    assert "## Semantic Diff Test Brief" in capsys.readouterr().out
    assert main(["--help"]) == EXIT_SUCCESS
    assert "skeletons --help" in capsys.readouterr().out


def test_an_analysis_run_again_scaffolds_what_the_saved_one_does(repo_factory, tmp_path) -> None:
    """A deterministic analysis numbers its obligations alike on every run, so the IDs a saved
    analysis lists name the same obligations in one run again with the same arguments."""
    repo, path, analysis = _analyzed(repo_factory, tmp_path, {"tests/test_cart.py": TEST_CART})
    ids = _ids(analysis)
    saved = _skeletons(repo, path, ids)
    again = generate_skeletons(
        {
            "repo_path": str(repo),
            "base_ref": analysis["repository"]["base_commit"],
            "head_ref": analysis["repository"]["head_commit"],
            "obligation_ids": ids,
        }
    )
    assert again["skeletons"] == saved["skeletons"]
    assert again["analysis_id"] != saved["analysis_id"]
    # The patches differ only in the analysis each names as their source.
    assert again["patch"].replace(again["analysis_id"], "sdw_x") == saved["patch"].replace(
        saved["analysis_id"], "sdw_x"
    )
    _apply(repo, again["patch"])


def test_an_analysis_run_again_takes_the_arguments_that_shape_it(repo_factory, tmp_path) -> None:
    price_old = "def price(amount):\n    return amount if amount < 10 else amount * 0.9\n"
    price_new = "def price(amount):\n    return amount if amount <= 10 else amount * 0.9\n"
    repo, base, head = repo_factory(
        {"src/shop/cart.py": CART_OLD, "src/shop/price.py": price_old},
        {"src/shop/cart.py": CART_NEW, "src/shop/price.py": price_new},
    )
    narrowed = {"repo_path": str(repo), "base_ref": base, "head_ref": head}
    analysis = analyze({**narrowed, "include": ["src/shop/price.py"], "output_format": "json"})
    result = generate_skeletons(
        {**narrowed, "include": ["src/shop/price.py"], "obligation_ids": _ids(analysis)}
    )
    assert {item["path"] for item in result["skeletons"]} == {"tests/test_price.py"}
    assert [item["obligation_id"] for item in result["skeletons"]] == _ids(analysis)


def test_a_new_test_file_goes_beside_the_obligations_candidates(repo_factory, tmp_path) -> None:
    """The module's tests live in `tests/unit`, so its new test file does too, not in `tests`."""
    checkout = (
        "from shop.cart import total\n\n\n"
        "def test_checkout_totals_the_cart():\n    assert total([1, 2], 5) == 3\n"
    )
    repo, path, analysis = _analyzed(
        repo_factory, tmp_path, {"tests/unit/test_checkout.py": checkout}
    )
    first = analysis["test_obligations"][0]
    assert first["candidate_existing_tests"][0]["path"] == "tests/unit/test_checkout.py"
    result = _skeletons(repo, path, _ids(analysis))
    assert {(item["path"], item["new_file"]) for item in result["skeletons"]} == {
        ("tests/unit/test_cart.py", True)
    }
    _apply(repo, result["patch"])
    assert (repo / "tests" / "unit" / "test_cart.py").is_file()
