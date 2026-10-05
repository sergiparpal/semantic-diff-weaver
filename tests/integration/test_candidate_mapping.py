from __future__ import annotations

from semantic_diff_weaver.ast_diff import StructuralDelta
from semantic_diff_weaver.git_diff import GitRepository
from semantic_diff_weaver.models import LineRange, WeaverConfig
from semantic_diff_weaver.semantic_candidates import build_candidates
from semantic_diff_weaver.service import analyze
from semantic_diff_weaver.test_mapper import build_test_index, map_candidate_tests


def test_committed_test_index_maps_import_name_and_path(repo_factory) -> None:
    tests = """
from src.api import allowed

def test_allowed_boundary():
    assert allowed(5)
"""
    repo_path, _, head = repo_factory(
        {
            "src/api.py": "def allowed(x):\n    return x < 5\n",
            "tests/test_api.py": tests,
        },
        {
            "src/api.py": "def allowed(x):\n    return x <= 5\n",
            "tests/test_api.py": tests,
        },
    )
    candidate = build_candidates(
        [
            StructuralDelta(
                path="src/api.py",
                symbol="allowed",
                kind="comparison_change",
                old="x < 5",
                new="x <= 5",
                old_lines=LineRange(start=2, end=2),
                new_lines=LineRange(start=2, end=2),
                hunk_id="src/api.py#hunk-001",
            )
        ]
    )[0]
    config = WeaverConfig()
    index = build_test_index(GitRepository.open(str(repo_path)), head, config)
    mapped = map_candidate_tests([candidate], index, config)[0]
    assert mapped[0].path == "tests/test_api.py"
    assert mapped[0].symbol == "test_allowed_boundary"
    assert mapped[0].match_score >= 0.7
    assert mapped[0].verified is False


def _candidate_tests(result: dict) -> list[str]:
    return [
        test["path"]
        for obligation in result["test_obligations"]
        for test in obligation["candidate_existing_tests"]
    ]


def test_include_narrows_the_analysis_but_not_candidate_test_discovery(repo_factory) -> None:
    """The Claude Code skill narrows large diffs with `include` and never with `exclude`.

    That advice rests on this asymmetry: `include` limits only which *changed* files are
    analyzed, while `exclude` also hides matching files from candidate-test discovery, so
    excluding a tests directory silently drops every candidate test.
    """
    tests = "from src.api import allowed\n\ndef test_allowed_boundary():\n    assert allowed(5)\n"
    repo, base, head = repo_factory(
        {
            "src/api.py": "def allowed(x):\n    return x < 5\n",
            "tools/report.py": "def limit():\n    return 1\n",
            "tests/test_api.py": tests,
        },
        {
            "src/api.py": "def allowed(x):\n    return x <= 5\n",
            "tools/report.py": "def limit():\n    return 2\n",
            "tests/test_api.py": tests,
        },
    )
    request = {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}

    narrowed = analyze({**request, "include": ["src/**"]})
    assert narrowed["scope"]["analyzed_files"] == ["src/api.py"]
    assert "tests/test_api.py" in _candidate_tests(narrowed)

    excluded = analyze({**request, "exclude": ["tests/**"]})
    assert "src/api.py" in excluded["scope"]["analyzed_files"]
    assert excluded["test_obligations"]
    assert _candidate_tests(excluded) == []


# `import handlers` gives every test here the package-import signal for every handler module,
# as importing `semantic_diff_weaver` does in this repository's own suite: a module mention in
# a body must not tip such a test over the threshold.
HANDLER_TESTS = """import importlib

import handlers

register_cooked_meal = importlib.import_module("src.handlers.register_cooked_meal").HANDLER
delete_dish = importlib.import_module("src.handlers.delete_dish").HANDLER


def test_register_cooked_meal_rejects_too_many_portions():
    assert register_cooked_meal({"portions": 4})


def test_every_tool_answers():
    assert register_cooked_meal({"portions": 1})
    assert delete_dish({"name": "x"})


def test_delete_dish_returns_the_name():
    assert delete_dish({"name": "x"}) == "x"
"""


def test_a_test_named_after_a_handler_module_is_its_candidate(repo_factory) -> None:
    """One module per tool, each exporting `HANDLER`, loaded dynamically by the tests.

    The changed symbol is the generic `HANDLER` and nothing is imported statically, so no
    earlier signal reached the module's own tests; they are found by the module's name.
    """
    handler = "def HANDLER(args):\n    return args['portions'] {} 5\n"
    unchanged = {
        "src/handlers/delete_dish.py": "def HANDLER(args):\n    return args['name']\n",
        "test_integration.py": HANDLER_TESTS,
    }
    repo, base, head = repo_factory(
        {"src/handlers/register_cooked_meal.py": handler.format("<"), **unchanged},
        {"src/handlers/register_cooked_meal.py": handler.format("<="), **unchanged},
    )
    result = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}
    )
    candidates = {
        test["symbol"]: test["match_reasons"]
        for obligation in result["test_obligations"]
        for test in obligation["candidate_existing_tests"]
    }
    assert "changed module named in test name" in candidates.get(
        "test_register_cooked_meal_rejects_too_many_portions", []
    )
    # A mention in the body alone is not evidence, and a test named after another module
    # is that module's.
    assert "test_every_tool_answers" not in candidates
    assert "test_delete_dish_returns_the_name" not in candidates
