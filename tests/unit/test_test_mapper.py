from __future__ import annotations

import pytest

import semantic_diff_weaver.test_mapper as test_mapper
from semantic_diff_weaver.ast_diff import StructuralDelta
from semantic_diff_weaver.errors import ErrorCode, WeaverError
from semantic_diff_weaver.git_diff import GitTreeEntry
from semantic_diff_weaver.models import LineRange, MappingRule, WeaverConfig
from semantic_diff_weaver.semantic_candidates import build_candidates
from semantic_diff_weaver.test_mapper import (
    IndexedTest,
    TestIndex,
    _index_source,
    _mirrored,
    build_test_index,
    defines_tests,
    map_candidate_tests,
)


def boundary_candidate():
    return build_candidates(
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


def test_terminology_alone_cannot_create_candidate() -> None:
    index = TestIndex(
        tests=[
            IndexedTest(
                path="tests/test_other.py",
                symbol="test_boundary",
                imports=frozenset(),
                name_tokens=frozenset({"test", "boundary"}),
                body_tokens=frozenset({"threshold"}),
            )
        ],
        incomplete=False,
        warnings=[],
    )
    assert map_candidate_tests([boundary_candidate()], index, WeaverConfig())[0] == []


def test_structural_features_rank_and_cap_stably() -> None:
    tests = [
        IndexedTest(
            path=f"tests/test_api_{index}.py",
            symbol=f"test_allowed_{index}",
            imports=frozenset({"src.api"}),
            name_tokens=frozenset({"test", "allowed", str(index)}),
            body_tokens=frozenset({"allowed", "boundary"}),
        )
        for index in range(8)
    ]
    mapped = map_candidate_tests(
        [boundary_candidate()],
        TestIndex(tests=tests, incomplete=False, warnings=[]),
        WeaverConfig(),
    )[0]
    assert len(mapped) == 5
    assert all(item.verified is False for item in mapped)
    assert mapped == sorted(mapped, key=lambda item: (-item.match_score, item.path, item.symbol))


def test_test_index_has_aggregate_file_and_byte_caps(monkeypatch) -> None:
    class FakeRepository:
        def list_files(self, commit: str) -> list[str]:
            return ["tests/test_a.py", "tests/test_b.py"]

        def tree_entries(self, commit: str, paths: list[str]):
            return {
                path: GitTreeEntry(mode="100644", object_id=f"{index + 1:040x}")
                for index, path in enumerate(paths)
            }

        def read_blob_objects(
            self,
            object_ids: set[str],
            max_bytes: int,
            *,
            max_total_bytes: int,
        ) -> dict[str, str | None]:
            source = "def test_value():\n    assert True\n"
            return {
                object_id: source if len(source.encode()) <= max_total_bytes else None
                for object_id in object_ids
            }

    monkeypatch.setattr(test_mapper, "MAX_TEST_INDEX_FILES", 1)
    index = build_test_index(FakeRepository(), "a" * 40, WeaverConfig())
    assert index.incomplete is True
    assert len(index.tests) == 1
    assert any("capped" in warning for warning in index.warnings)

    monkeypatch.setattr(test_mapper, "MAX_TEST_INDEX_FILES", 500)
    monkeypatch.setattr(test_mapper, "MAX_TEST_INDEX_BYTES", 1)
    byte_limited = build_test_index(FakeRepository(), "a" * 40, WeaverConfig())
    assert byte_limited.incomplete is True
    assert byte_limited.tests == []
    assert any("bounded source limits" in warning for warning in byte_limited.warnings)


def test_test_index_batches_reads_and_honors_excludes() -> None:
    class FakeRepository:
        def __init__(self) -> None:
            self.tree_calls = 0
            self.blob_calls = 0

        def list_files(self, commit: str) -> list[str]:
            return ["tests/test_keep.py", "tests/generated/test_skip.py"]

        def tree_entries(self, commit: str, paths: list[str]):
            self.tree_calls += 1
            return {path: GitTreeEntry(mode="100644", object_id="a" * 40) for path in paths}

        def read_blob_objects(self, object_ids, max_bytes, *, max_total_bytes):
            self.blob_calls += 1
            return {"a" * 40: "def test_keep():\n    assert True\n"}

    repository = FakeRepository()
    config = WeaverConfig()
    config.paths.exclude.append("tests/generated/**")
    index = build_test_index(repository, "b" * 40, config)
    assert [item.path for item in index.tests] == ["tests/test_keep.py"]
    assert repository.tree_calls == 1
    assert repository.blob_calls == 1


def test_test_discovery_limit_degrades_to_incomplete_mapping() -> None:
    class FakeRepository:
        def list_files(self, commit: str) -> list[str]:
            raise WeaverError(ErrorCode.DIFF_TOO_LARGE, "safe", "narrow")

    index = build_test_index(FakeRepository(), "b" * 40, WeaverConfig())
    assert index.incomplete is True
    assert index.tests == []
    assert any("safe Git boundary" in warning for warning in index.warnings)


def test_index_source_handles_import_forms_classes_and_async_tests() -> None:
    indexed = _index_source(
        "tests/test_sample.py",
        "import package\n"
        "from . import helper\n"
        "class TestSample:\n"
        "    def helper(self):\n"
        "        return None\n"
        "    async def test_value(self):\n"
        "        assert helper\n",
    )
    assert [item.symbol for item in indexed] == ["TestSample.test_value"]
    assert indexed[0].imports == frozenset({"package", "helper"})


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("def test_value():\n    assert True\n", True),
        ("class TestSample:\n    async def test_value(self):\n        assert True\n", True),
        # This repository's own `test_mapper.py`: a `Test*` class is not a test without one.
        ("class TestIndex:\n    tests: list\n\ndef build_test_index():\n    return []\n", False),
        ("def helper():\n    def test_nested():\n        pass\n", False),
        ("TEMPLATE = 'def test_value():'\n", False),
        ("def test_value(:\n", False),
    ],
)
def test_only_source_that_defines_a_test_is_a_test_module(source: str, expected: bool) -> None:
    assert defines_tests(source) is expected


def test_configured_mapping_is_structural_and_scoped_to_its_own_rule() -> None:
    """An explicit mapping must promote only tests matching that rule's own test patterns."""
    index = TestIndex(
        tests=[
            IndexedTest(
                path="tests/contract/test_api_contract.py",
                symbol="test_threshold",
                imports=frozenset(),
                name_tokens=frozenset({"test", "threshold"}),
                body_tokens=frozenset({"boundary"}),
            ),
            IndexedTest(
                path="tests/unrelated/test_other.py",
                symbol="test_threshold",
                imports=frozenset(),
                name_tokens=frozenset({"test", "threshold"}),
                body_tokens=frozenset({"boundary"}),
            ),
        ],
        incomplete=False,
        warnings=[],
    )
    config = WeaverConfig()
    config.mapping = [
        MappingRule(source="src/api.py", tests=["tests/contract/**"]),
        # A rule that matches no candidate must not lend its tests to one that does.
        MappingRule(source="src/other.py", tests=["tests/unrelated/**"]),
    ]
    mapped = map_candidate_tests([boundary_candidate()], index, config)[0]
    assert [item.path for item in mapped] == ["tests/contract/test_api_contract.py"]
    assert "explicit configured mapping" in mapped[0].match_reasons
    # Terminology alone still cannot promote the unmapped peer.
    assert map_candidate_tests([boundary_candidate()], index, WeaverConfig())[0] == []


def test_unmatched_configured_mapping_leaves_candidates_unmapped() -> None:
    index = TestIndex(
        tests=[
            IndexedTest(
                path="tests/contract/test_api_contract.py",
                symbol="test_threshold",
                imports=frozenset(),
                name_tokens=frozenset({"test", "threshold"}),
                body_tokens=frozenset({"boundary"}),
            )
        ],
        incomplete=False,
        warnings=[],
    )
    config = WeaverConfig()
    config.mapping = [MappingRule(source="src/nothing.py", tests=["tests/contract/**"])]
    assert map_candidate_tests([boundary_candidate()], index, config)[0] == []


def test_symbol_suffix_import_is_matched_through_the_leaf_index() -> None:
    """``from src.api import allowed`` indexes as a dotted import whose leaf is the symbol.

    The import signal is resolved from a leaf index plus a per-module scan rather than a scan
    per (module, symbol) pair, so this pins the leaf half of that split. Import (0.25) plus
    category terminology (0.10) is exactly the 0.35 floor; the import is what makes it
    structural, and terminology alone could never promote the same test.
    """
    index = TestIndex(
        tests=[
            IndexedTest(
                path="tests/test_unrelated.py",
                symbol="test_case",
                imports=frozenset({"src.api.allowed"}),
                name_tokens=frozenset({"test", "case"}),
                body_tokens=frozenset({"boundary"}),
            )
        ],
        incomplete=False,
        warnings=[],
    )
    mapped = map_candidate_tests([boundary_candidate()], index, WeaverConfig())[0]
    assert [item.path for item in mapped] == ["tests/test_unrelated.py"]
    assert "direct module or symbol import" in mapped[0].match_reasons


def test_a_bare_import_never_matches_on_the_symbol_alone() -> None:
    """Identical to the case above except the import has no dot, so it has no leaf to match."""
    index = TestIndex(
        tests=[
            IndexedTest(
                path="tests/test_unrelated.py",
                symbol="test_case",
                imports=frozenset({"allowed"}),
                name_tokens=frozenset({"test", "case"}),
                body_tokens=frozenset({"boundary"}),
            )
        ],
        incomplete=False,
        warnings=[],
    )
    assert map_candidate_tests([boundary_candidate()], index, WeaverConfig())[0] == []


@pytest.mark.parametrize(
    ("path", "stem"),
    [
        ("src/handlers/register_cooked_meal.py", "register_cooked_meal"),
        ("pkg/Path_Policy.py", "path_policy"),
        ("src/dii/store.py", None),
        ("src/handlers/_common.py", None),
        ("pkg/__init__.py", None),
    ],
)
def test_only_a_multi_word_public_module_name_is_distinctive(path: str, stem: str | None) -> None:
    """A single word such as `store` turns up in tests about anything; `_common` is private."""
    assert test_mapper._distinctive_stem(path) == stem


def handler_candidate():
    return build_candidates(
        [
            StructuralDelta(
                path="src/handlers/cooked_meal.py",
                symbol="HANDLER",
                kind="comparison_change",
                old="x < 5",
                new="x <= 5",
                old_lines=LineRange(start=2, end=2),
                new_lines=LineRange(start=2, end=2),
                hunk_id="src/handlers/cooked_meal.py#hunk-001",
            )
        ]
    )[0]


def module_name_reasons(source: str) -> dict[str, list[str]]:
    index = TestIndex(
        tests=_index_source("tests/test_meals.py", source), incomplete=False, warnings=[]
    )
    mapped = map_candidate_tests([handler_candidate()], index, WeaverConfig())[0]
    return {item.symbol: item.match_reasons for item in mapped}


def test_a_module_name_matches_a_test_name_only_as_whole_words() -> None:
    """`cooked_meal` names `test_register_cooked_meal`, never `test_uncooked_meals`."""
    reasons = module_name_reasons(
        "import handlers\n"
        "def test_cooked_meal_is_saved():\n    assert handlers\n"
        "def test_register_cooked_meal():\n    assert handlers\n"
        "class TestMeals:\n"
        "    def test_cooked_meal(self):\n        assert handlers\n"
        "def test_uncooked_meal_is_kept():\n    assert handlers\n"
        "def test_cooked_meals_are_listed():\n    assert handlers\n"
        "def test_precooked_meal_is_saved():\n    assert handlers\n"
    )
    named = "changed module named in test name"
    for symbol in (
        "test_cooked_meal_is_saved",
        "test_register_cooked_meal",
        "TestMeals.test_cooked_meal",
    ):
        assert named in reasons[symbol]
    for symbol in (
        "test_uncooked_meal_is_kept",
        "test_cooked_meals_are_listed",
        "test_precooked_meal_is_saved",
    ):
        assert named not in reasons.get(symbol, [])


def test_a_module_mention_in_the_body_adds_only_to_a_test_named_after_it() -> None:
    reasons = module_name_reasons(
        "import handlers\n"
        "def test_cooked_meal_is_saved():\n    assert handlers.cooked_meal\n"
        "def test_cooked_meal_is_listed():\n    assert handlers\n"
        "def test_every_handler_answers():\n    assert handlers.cooked_meal\n"
    )
    bodied = "changed module named in bounded test body"
    assert bodied in reasons["test_cooked_meal_is_saved"]
    assert bodied not in reasons["test_cooked_meal_is_listed"]
    assert bodied not in reasons.get("test_every_handler_answers", [])


def symbol_reasons(
    path: str, symbol: str, source: str, test_path: str = "tests/test_suite.py"
) -> dict[str, list[str]]:
    candidate = build_candidates(
        [
            StructuralDelta(
                path=path,
                symbol=symbol,
                kind="symbol_added",
                old=None,
                new="(text)",
                old_lines=None,
                new_lines=LineRange(start=1, end=2),
                hunk_id=f"{path}#hunk-001",
            )
        ]
    )[0]
    index = TestIndex(tests=_index_source(test_path, source), incomplete=False, warnings=[])
    mapped = map_candidate_tests([candidate], index, WeaverConfig())[0]
    return {item.symbol: item.match_reasons for item in mapped}


SYMBOL_IN_NAME = "changed symbol token in test name"
SYMBOL_IN_BODY = "changed symbol token in bounded test body"


def test_a_compound_symbol_name_matches_a_test_name_as_whole_words() -> None:
    """Split into single words, `pr_number` could never name `test_pr_number_rejects_zero`."""
    reasons = symbol_reasons(
        "src/cli.py",
        "pr_number",
        "import cli\n"
        "def test_pr_number_rejects_zero():\n    assert cli\n"
        "def test_a_parsed_pr_number_is_an_int():\n    assert cli\n"
        "def test_xpr_number_is_another_option():\n    assert cli\n"
        "def test_pr_numbers_are_listed():\n    assert cli\n",
    )
    assert SYMBOL_IN_NAME in reasons["test_pr_number_rejects_zero"]
    assert SYMBOL_IN_NAME in reasons["test_a_parsed_pr_number_is_an_int"]
    assert "test_xpr_number_is_another_option" not in reasons
    assert "test_pr_numbers_are_listed" not in reasons


@pytest.mark.parametrize("symbol", ["RunArgs", "_run_args", "Command.run_args"])
def test_a_class_or_private_name_reads_as_its_snake_case_words(symbol: str) -> None:
    reasons = symbol_reasons(
        "src/cli.py", symbol, "import cli\ndef test_options_become_run_args():\n    assert cli\n"
    )
    assert SYMBOL_IN_NAME in reasons["test_options_become_run_args"]


def test_a_single_word_names_a_symbol_only_in_a_test_that_uses_it() -> None:
    """`run` is a word of `test_options_become_run_args`, which tests something else.

    Its body calls `run_args_from`, which no longer reads as `run`, and its own name no longer
    counts as its body through the `def` line.
    """
    reasons = symbol_reasons(
        "src/cli.py",
        "run",
        "import cli\n"
        "def test_options_become_run_args():\n    assert cli.run_args_from([])\n"
        "def test_run_returns_its_exit_code():\n    assert cli.run([]) == 0\n",
    )
    assert "test_options_become_run_args" not in reasons
    assert reasons["test_run_returns_its_exit_code"] == [
        "direct module or symbol import",
        SYMBOL_IN_NAME,
        SYMBOL_IN_BODY,
    ]


def test_the_body_uses_a_symbol_by_its_whole_identifier_or_a_private_wrapper() -> None:
    reasons = symbol_reasons(
        "src/server.py",
        "serve",
        "import server\n"
        "def test_lines_are_answered():\n    assert _serve(b'')\n"
        "def test_one_line_is_answered():\n    assert server.serve_once(b'')\n",
    )
    assert SYMBOL_IN_BODY in reasons["test_lines_are_answered"]
    assert "test_one_line_is_answered" not in reasons


def test_a_dunder_method_is_not_named_by_a_word_of_a_test_name() -> None:
    reasons = symbol_reasons(
        "src/store.py",
        "Store.__init__",
        "import store\ndef test_init_resolves_aliases():\n    assert store.init()\n",
    )
    assert "test_init_resolves_aliases" not in reasons


@pytest.mark.parametrize(
    ("source_path", "test_path", "expected"),
    [
        ("src/pkg/__main__.py", "tests/test_main.py", True),
        ("src/pkg/_common.py", "tests/test_common.py", True),
        ("src/pkg/cli.py", "tests/test_cli.py", True),
        ("src/pkg/__main__.py", "tests/test_cli.py", False),
    ],
)
def test_a_test_file_names_a_module_without_its_underscores(
    source_path: str, test_path: str, expected: bool
) -> None:
    assert _mirrored(source_path, test_path) is expected


@pytest.mark.parametrize(
    ("source", "modules"),
    [
        (
            "import subprocess, sys\n"
            "def test_exit():\n"
            "    subprocess.run([sys.executable, '-P', '-m', 'pkg', 'evaluate'])\n",
            {"pkg"},
        ),
        ("def test_exit():\n    run('python -m pkg.tool --flag')\n", {"pkg.tool"}),
        ("import sys\ndef test_exit():\n    run(f'{sys.executable} -m pkg')\n", {"pkg"}),
        (
            "import runpy\ndef test_exit():\n    runpy.run_module('pkg', run_name='__main__')\n",
            {"pkg"},
        ),
        # A command built once for the whole file, as imports are.
        ("COMMAND = ('python', '-m', 'pkg')\ndef test_exit():\n    run(COMMAND)\n", {"pkg"}),
        ("def test_commit():\n    git(['commit', '-m', 'Fix the parser'])\n", set()),
        ("def test_commit():\n    git(\"commit -m 'Fix the parser'\")\n", set()),
    ],
)
def test_the_index_records_the_modules_a_test_file_runs_as_a_program(
    source: str, modules: set[str]
) -> None:
    [indexed] = _index_source("tests/test_main.py", source)
    assert indexed.modules_run == modules


def main_module_reasons(test_path: str, source: str, changed: str) -> dict[str, list[str]]:
    candidate = build_candidates(
        [
            StructuralDelta(
                path=changed,
                symbol="<module>",
                kind="symbol_added",
                old=None,
                new="module",
                old_lines=None,
                new_lines=LineRange(start=1, end=4),
                hunk_id=f"{changed}#hunk-001",
            )
        ]
    )[0]
    index = TestIndex(tests=_index_source(test_path, source), incomplete=False, warnings=[])
    mapped = map_candidate_tests([candidate], index, WeaverConfig())[0]
    return {item.symbol: item.match_reasons for item in mapped}


RUNS_PKG = (
    "import subprocess, sys\n"
    "def test_exit_code():\n    subprocess.run([sys.executable, '-m', 'pkg', 'report'])\n"
)


def test_a_test_that_runs_the_package_is_a_candidate_for_its_main_module() -> None:
    """`test_main.py` runs `python -m pkg`; no import ever reaches `pkg/__main__.py`."""
    reasons = main_module_reasons("tests/test_main.py", RUNS_PKG, "src/pkg/__main__.py")
    assert reasons == {
        "test_exit_code": ["mirrored source/test path", "changed module run with python -m"]
    }


def test_running_a_package_is_no_evidence_about_its_other_modules() -> None:
    """A run reaches the whole package, as coarsely as an import of it already says."""
    assert main_module_reasons("tests/test_cli.py", RUNS_PKG, "src/pkg/cli.py") == {}


def test_importing_the_package_confirms_a_test_name_but_is_no_import_of_the_module() -> None:
    """`from pkg import cli` counted as a direct import of every module of `pkg`.

    With category terminology alone that crossed the threshold, so a test of `cli` became a
    candidate for every module beside it.
    """
    reasons = symbol_reasons(
        "src/pkg/messages.py",
        "unexpected_failure",
        "from pkg import cli\n"
        "def test_an_unexpected_failure_names_its_class():\n    assert cli.main([]) == 1\n"
        "def test_a_review_of_the_behavior_exits_cleanly():\n    assert cli.main([]) == 0\n",
    )
    assert reasons == {
        "test_an_unexpected_failure_names_its_class": [
            "changed module's package imported",
            SYMBOL_IN_NAME,
        ]
    }


def test_a_test_that_imports_the_module_gets_no_package_signal_on_top() -> None:
    reasons = symbol_reasons(
        "src/pkg/messages.py",
        "unexpected_failure",
        "from pkg import messages\n"
        "def test_an_unexpected_failure_names_its_class():\n    assert messages\n",
    )
    assert reasons["test_an_unexpected_failure_names_its_class"] == [
        "direct module or symbol import",
        SYMBOL_IN_NAME,
    ]


def test_tests_matched_only_by_their_file_stand_as_the_file() -> None:
    """A mirrored name and an import say which file tests `cli.py`, not which of its tests.

    Each of them used to be a candidate of its own, cut to five in alphabetical order.
    """
    reasons = symbol_reasons(
        "src/cli.py",
        "pr_number",
        "import cli\n"
        "def test_a_command_line_is_parsed():\n    assert cli.main([])\n"
        "def test_an_unknown_option_exits_with_two():\n    assert cli.main(['-x'])\n"
        "def test_pr_number_rejects_zero():\n    assert cli.main(['--pr', '0'])\n",
        test_path="tests/test_cli.py",
    )
    assert reasons == {
        "<module>": ["mirrored source/test path", "direct module or symbol import"],
        "test_pr_number_rejects_zero": [
            "mirrored source/test path",
            "direct module or symbol import",
            SYMBOL_IN_NAME,
        ],
    }
