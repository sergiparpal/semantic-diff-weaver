"""The test files a change can affect, through the analysis, its brief, and `select`."""

from __future__ import annotations

import json
import warnings
from collections.abc import Callable
from pathlib import Path

import pytest

from semantic_diff_weaver.cli import EXIT_ARGUMENT_ERROR, EXIT_SUCCESS, EXIT_WHOLE_SUITE, main
from semantic_diff_weaver.models import AnalysisResult, SelectedTest, TestSelection
from semantic_diff_weaver.renderer import MAX_BRIEF_TESTS, render_markdown
from semantic_diff_weaver.service import analyze

Factory = Callable[..., tuple[Path, str, str]]

SHOP = {
    "shop/__init__.py": "",
    "shop/money.py": "def cents(amount):\n    return amount < 100\n",
    "shop/report.py": "def render(rows):\n    return rows\n",
    "tests/test_money.py": "from shop.money import cents\n\n\ndef test_cents():\n    assert cents(1)\n",
    "tests/test_report.py": "from shop import report\n\n\ndef test_render():\n    assert report\n",
}
CHANGED = {"shop/money.py": "def cents(amount):\n    return amount <= 100\n"}


def _analysis(repo_factory: Factory, new: dict[str, str]) -> AnalysisResult:
    repo, base, head = repo_factory(SHOP, new)
    envelope = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}
    )
    return AnalysisResult.model_validate(envelope)


def test_the_analysis_names_the_tests_its_change_can_affect(repo_factory: Factory) -> None:
    result = _analysis(repo_factory, CHANGED)
    assert result.test_selection == TestSelection(
        run_all=False,
        tests=[
            SelectedTest(
                path="tests/test_money.py", reasons=["It loads shop/money.py, which changed."]
            )
        ],
    )
    markdown = render_markdown(result)
    assert (
        "### Tests to run\n\n1 test file(s) the change can affect, by their imports:\n\n"
        "- `tests/test_money.py`: It loads shop/money\\.py, which changed\\."
    ) in markdown


def test_a_changed_test_is_selected_though_the_analysis_leaves_it_out(
    repo_factory: Factory,
) -> None:
    """Test files are no behavior under review, but a changed one is a test to run."""
    changed = SHOP["tests/test_report.py"] + "\n\ndef test_more():\n    pass\n"
    result = _analysis(repo_factory, {"tests/test_report.py": changed})
    assert result.behavior_changes == []
    assert result.test_selection is not None
    assert [item.path for item in result.test_selection.tests] == ["tests/test_report.py"]


def test_the_brief_says_when_the_whole_suite_runs_and_why(repo_factory: Factory) -> None:
    result = _analysis(repo_factory, {**CHANGED, "README.md": "Changed.\n"})
    assert result.test_selection == TestSelection(
        run_all=True, reasons=["Tests may read changed files that are not Python: README.md."]
    )
    assert (
        "### Tests to run\n\nThe whole suite:\n"
        "- Tests may read changed files that are not Python: README\\.md\\."
    ) in render_markdown(result)


def test_the_brief_lists_a_long_selection_in_part(repo_factory: Factory) -> None:
    result = _analysis(repo_factory, CHANGED)
    count = MAX_BRIEF_TESTS + 3
    result.test_selection = TestSelection(
        run_all=False,
        tests=[
            SelectedTest(path=f"tests/test_{index:02}.py", reasons=["It changed."])
            for index in range(count)
        ],
    )
    markdown = render_markdown(result)
    assert f"{count} test file(s) the change can affect" in markdown
    assert markdown.count("`: It changed\\.") == MAX_BRIEF_TESTS
    assert "- and 3 more, in the analysis's `test_selection`." in markdown


def test_the_brief_says_when_no_test_can_be_affected(repo_factory: Factory) -> None:
    result = _analysis(repo_factory, CHANGED)
    result.test_selection = TestSelection(run_all=False)
    assert "### Tests to run\n\nNone: no test file imports a changed Python file" in (
        render_markdown(result)
    )
    # An analysis read from before selections existed has no section at all.
    result.test_selection = None
    assert "### Tests to run" not in render_markdown(result)


def _select(
    capsys: pytest.CaptureFixture[str], repo: Path, base: str, head: str, *extra: str
) -> tuple[int, str, str]:
    status = main(["select", "--repo", str(repo), "--base", base, "--head", head, *extra])
    captured = capsys.readouterr()
    return status, captured.out, captured.err


def test_select_prints_the_test_files_to_run(
    repo_factory: Factory, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS")
    repo, base, head = repo_factory(SHOP, CHANGED)
    assert _select(capsys, repo, base, head) == (EXIT_SUCCESS, "tests/test_money.py\n", "")
    status, out, _ = _select(capsys, repo, base, head, "--format", "json")
    assert status == EXIT_SUCCESS
    assert json.loads(out) == {
        "run_all": False,
        "reasons": [],
        "tests": [
            {"path": "tests/test_money.py", "reasons": ["It loads shop/money.py, which changed."]}
        ],
    }


def test_select_prints_nothing_when_no_test_can_be_affected(
    repo_factory: Factory, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, base, head = repo_factory(
        {**SHOP, ".semantic-diff-weaver.yaml": "version: 1\nselection:\n  ignore: ['**/*.md']\n"},
        {"docs/guide.md": "New.\n"},
    )
    assert _select(capsys, repo, base, head) == (EXIT_SUCCESS, "", "")


def test_select_exits_to_say_the_whole_suite_runs(
    repo_factory: Factory, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    repo, base, head = repo_factory(SHOP, {**CHANGED, "README.md": "Changed.\n"})
    status, out, err = _select(capsys, repo, base, head)
    assert (status, out) == (EXIT_WHOLE_SUITE, "")
    assert err == (
        "semantic-diff-weaver: run the whole suite: Tests may read changed files that are not "
        "Python: README.md.\n"
    )
    # A risk profile can say no test reads it.
    profile = tmp_path / "profile.yaml"
    profile.write_text("version: 1\nselection:\n  ignore: [README.md]\n", encoding="utf-8")
    status, out, _ = _select(capsys, repo, base, head, "--risk-profile", str(profile))
    assert (status, out) == (EXIT_SUCCESS, "tests/test_money.py\n")


def test_select_fails_like_the_analysis_does(
    repo_factory: Factory, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, base, _ = repo_factory(SHOP, CHANGED)
    status, out, err = _select(capsys, repo, base, "no-such-ref")
    assert (status, out) == (1, "")
    assert err.startswith("semantic-diff-weaver: invalid_ref: ")
    assert main(["select", "--repo", str(repo)]) == EXIT_ARGUMENT_ERROR
    assert main(["select", "--help"]) == EXIT_SUCCESS
    capsys.readouterr()


def test_select_refuses_to_widen_the_operator_s_roots(
    repo_factory: Factory, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    repo, base, head = repo_factory(SHOP, CHANGED)
    status, out, err = _select(capsys, repo, base, head, "--allow-root", str(tmp_path))
    assert (status, out) == (EXIT_ARGUMENT_ERROR, "")
    assert "cannot widen" in err


def test_a_test_file_s_invalid_escapes_raise_no_warning(repo_factory: Factory) -> None:
    """The analysis parses the repository's tests to index and select them.

    A regular expression written without the `r` prefix, here `"\\d+"`, makes Python warn while
    parsing it, and the warning went to the analyzer's stderr, where it read as the tool's own.
    """
    pattern = '"' + chr(92) + 'd+"'
    test = (
        "import re\n\nfrom shop.money import cents\n\n\ndef test_cents():\n"
        f"    assert cents(1) and re.match({pattern}, '1')\n"
    )
    repo, base, head = repo_factory({**SHOP, "tests/test_money.py": test}, CHANGED)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        envelope = analyze(
            {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}
        )

    selected = AnalysisResult.model_validate(envelope).test_selection
    assert "tests/test_money.py" in [item.path for item in selected.tests]
    assert [item for item in caught if item.filename == "<unknown>"] == []
