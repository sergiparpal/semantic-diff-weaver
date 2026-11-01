from __future__ import annotations

import pytest

import semantic_diff_weaver.test_mapper as test_mapper
from semantic_diff_weaver.ast_diff import StructuralDelta, module_references
from semantic_diff_weaver.call_graph import CallGraph, package_of, source_roots
from semantic_diff_weaver.errors import ErrorCode, WeaverError
from semantic_diff_weaver.git_diff import GitTreeEntry
from semantic_diff_weaver.models import LineRange, MappingRule, WeaverConfig
from semantic_diff_weaver.semantic_candidates import build_candidates
from semantic_diff_weaver.test_mapper import (
    IndexedTest,
    TestIndex,
    _distinctive_stem,
    _index_source,
    _mirrored,
    _module_name,
    _subject_words,
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


LOADING_TESTS = """import importlib
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent
PACKAGE = "shop"
_pkg = importlib.import_module(_PLUGIN_DIR.name)
_dish_mod = importlib.import_module(".src.dish", _PLUGIN_DIR.name)
_cart = importlib.import_module(".cart", PACKAGE)
_api = importlib.import_module("shop" + ".api")
Dish = _dish_mod.Dish


def _load_handler(module_suffix):
    mod = importlib.import_module(f".src.handlers.{module_suffix}", _PLUGIN_DIR.name)
    return mod.HANDLER


add_dish = _load_handler("add_dish")
list_fridge = _load_handler(module_suffix="list_fridge")


def test_a_dish_is_added():
    name = "json_fridge"
    importlib.import_module(".src.repositories." + name, _PLUGIN_DIR.name)
    assert add_dish({"name": Dish("x").name})


def test_the_fridge_is_read():
    store = importlib.import_module(".src.repositories.json_store", _PLUGIN_DIR.name)
    assert list_fridge({}) and store
"""


def test_the_index_reads_what_a_test_file_loads_by_name() -> None:
    """`meal-manager`'s tests load their modules with `importlib`, relative to their package.

    A name written out, a module-level constant, an f-string of a loader's argument, and a
    concatenation of those are read; a name known only when the test runs is not. The name of
    the test's own directory loads the package it lives in, here the repository's root, `""`.
    """
    added, read = _index_source("test_integration.py", LOADING_TESTS)
    shared = {"", "src.dish", "shop.cart", "shop.api", "src.handlers.add_dish"}
    shared.add("src.handlers.list_fridge")
    assert shared <= added.imports
    assert "src.repositories.json_store" in read.imports - added.imports
    assert not any("json_fridge" in name for name in added.imports)
    assert dict(added.bound) == {
        "importlib": "importlib",
        "Path": "pathlib.Path",
        "_pkg": "",
        "_dish_mod": "src.dish",
        "_cart": "shop.cart",
        "_api": "shop.api",
        "Dish": "src.dish.Dish",
        "add_dish": "src.handlers.add_dish.HANDLER",
        "list_fridge": "src.handlers.list_fridge.HANDLER",
    }
    assert dict(read.bound)["store"] == "src.repositories.json_store"


EDGE_LOADS = """import importlib
import shop.extra as extra
from importlib import import_module

CONSTANT = "shop"
cart = importlib.import_module(name="shop.cart")
copy = cart
total = cart.Cart.total
too_high = importlib.import_module("...x", "a")
quoted = importlib.import_module(f"{CONSTANT!r}")
long = importlib.import_module(LONG)


def _module(suffix):
    return import_module(CONSTANT + "." + suffix)


def _discarded(suffix):
    import_module("shop." + suffix)
    return None


def _either(first, second):
    one = import_module(first)
    two = import_module(second)
    return one.VALUE


def _elsewhere(suffix):
    mod = import_module("shop." + suffix)
    return other.VALUE


api = _module("api")
views = _discarded("views")
both = _either("shop.a", "shop.b")
unknown = _module(compute())
spread = _module(**options)
models = _elsewhere("models")


def test_loads():
    assert api and copy and total
"""


def test_a_load_the_file_does_not_spell_out_binds_nothing() -> None:
    """Only a name the file writes out is read, and a helper binds only what it returns."""
    long_name = " + ".join(f"'{letter}'" for letter in "abcdefghijklmnopqrstu")
    [test] = _index_source("tests/test_edges.py", EDGE_LOADS.replace("LONG", long_name))
    loaded = {"shop.cart", "shop.api", "shop.views", "shop.a", "shop.b", "shop.models"}
    assert test.imports == {"importlib", "importlib.import_module", "shop.extra", *loaded}
    assert dict(test.bound) == {
        "importlib": "importlib",
        "extra": "shop.extra",
        "import_module": "importlib.import_module",
        "cart": "shop.cart",
        "copy": "shop.cart",
        "total": "shop.cart.Cart.total",
        "api": "shop.api",
    }


OWN_PACKAGE_LOADS = """import importlib
import os
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent
HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "shop.cart"
NAME = compute()
_pkg = importlib.import_module(_PLUGIN_DIR.name)
_same = importlib.import_module(os.path.basename(HERE))
_own = importlib.import_module(__package__)
_cart = importlib.import_module(NAME)
_deep = importlib.import_module(DEEP.parent.name)


def test_registers():
    assert _pkg.register and _same and _own and _cart and _deep
"""


@pytest.mark.parametrize(
    ("path", "package"), [("test_unit.py", ""), ("tests/test_unit.py", "tests")]
)
def test_the_name_of_a_tests_own_directory_loads_its_own_package(path: str, package: str) -> None:
    """`meal-manager` loads its plugin package as `importlib.import_module(_PLUGIN_DIR.name)`.

    A test at the repository's root lives in the root package, which the call graph names
    `""`. A name assigned again with something unknown is forgotten.
    """
    deep = "Path(" * 20 + "__file__" + ")" * 20
    [test] = _index_source(path, OWN_PACKAGE_LOADS.replace("DEEP", deep))
    assert test.imports == {"importlib", "os", "pathlib", "pathlib.Path", package}
    bound = dict(test.bound)
    assert bound["_pkg"] == bound["_same"] == bound["_own"] == package
    assert "_cart" not in bound
    assert "_deep" not in bound


def test_an_import_inside_a_test_is_that_tests_alone() -> None:
    """Two of `meal-manager`'s integration tests load `dii.store`; the other 80 do not."""
    source = (
        "import shop\n\n"
        "def test_one():\n    import shop.extra\n    assert shop.extra\n\n"
        "def test_two():\n    assert shop\n"
    )
    one, two = _index_source("tests/test_shop.py", source)
    assert one.imports == {"shop", "shop.extra"}
    assert two.imports == {"shop"}


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


CLI_MODULE = """import argparse

HANDLERS = {"run": _dispatch}


def main(argv):
    return _run(_parser().parse_args(argv))


def _parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr", type=pr_number)
    return parser


def pr_number(text):
    return int(text)


def _run(options):
    return 0


def _dispatch():
    return 2


def _describe(options):
    return str(options)


def describe_all(options):
    return str(options)


@register
def _on_startup():
    return 1
"""

CLI_TESTS = """import cli


def invoke(argv):
    return cli.main(argv)


def test_a_command_line_is_parsed():
    assert invoke(["--pr", "7"]) == 0


def test_an_unknown_option_is_rejected():
    assert invoke(["--x"]) == 2
"""

REACHED = "changed symbol reached through calls"
FILE_ONLY = ["mirrored source/test path", "direct module or symbol import"]


def reach_reasons(
    symbol: str,
    tests: str = CLI_TESTS,
    test_path: str = "tests/test_cli.py",
    module_path: str = "src/cli.py",
) -> dict[str, list[str]]:
    candidate = build_candidates(
        [
            StructuralDelta(
                path=module_path,
                symbol=symbol,
                kind="symbol_added",
                old=None,
                new="(text)",
                old_lines=None,
                new_lines=LineRange(start=1, end=2),
                hunk_id=f"{module_path}#hunk-001",
            )
        ]
    )[0]
    index = TestIndex(tests=_index_source(test_path, tests), incomplete=False, warnings=[])
    graph = CallGraph({module_path: module_references(CLI_MODULE, package_of(module_path))})
    mapped = map_candidate_tests([candidate], index, WeaverConfig(), graph)[0]
    return {item.symbol: item.match_reasons for item in mapped}


def test_a_test_reaches_what_its_same_file_helpers_call() -> None:
    [first, _] = _index_source("tests/test_cli.py", CLI_TESTS)
    assert {"invoke", "cli", "main"} <= first.reach_names


@pytest.mark.parametrize("symbol", ["_parser", "_run", "pr_number", "_dispatch"])
def test_a_helper_the_tests_reach_through_the_module_calls_is_theirs(symbol: str) -> None:
    """`main` calls `_run` and `_parser`, which hands `pr_number` to argparse.

    `_dispatch` is reached by loading the module, which puts it in a dispatch table. Each test
    used to be file-level evidence at best: the test file, standing for all of its tests.
    """
    assert reach_reasons(symbol) == {
        "test_a_command_line_is_parsed": [*FILE_ONLY, REACHED],
        "test_an_unknown_option_is_rejected": [*FILE_ONLY, REACHED],
    }


def test_a_test_calling_a_symbol_directly_ranks_first_among_those_reaching_it() -> None:
    """A direct call is a reach as well as a mention, and the reach is the stronger evidence.

    Scored by its mention, the test calling `_parser` ranked below the tests reaching it through
    `cli.main`. Scored by its reach, it ties with them, and naming the symbol breaks the tie.
    """
    tests = f"{CLI_TESTS}\n\ndef test_a_pr_is_an_option():\n    assert cli._parser()\n"
    reasons = reach_reasons("_parser", tests)
    assert list(reasons) == [
        "test_a_pr_is_an_option",
        "test_a_command_line_is_parsed",
        "test_an_unknown_option_is_rejected",
    ]
    assert {tuple(items) for items in reasons.values()} == {(*FILE_ONLY, REACHED)}


def test_a_private_helper_no_test_reaches_has_no_candidate() -> None:
    """Nothing calls `_describe`, so the test file is no evidence that it is tested."""
    assert reach_reasons("_describe") == {}


@pytest.mark.parametrize("symbol", ["describe_all", "_on_startup"])
def test_what_another_module_or_a_decorator_may_call_keeps_the_test_file(symbol: str) -> None:
    """A public function has callers elsewhere; a decorator may register a private one."""
    assert reach_reasons(symbol) == {"<module>": FILE_ONLY}


def test_without_a_test_that_loads_the_module_nothing_is_ruled_untested() -> None:
    """Tests that load a module by a name known only when they run show no reach, which is no
    evidence of none, and their tests named after the handler stay its candidates.

    `meal-manager`'s tests write out the names they load, which the index reads.
    """
    loads = "    name = 'meal_handler'\n    assert importlib.import_module('src.' + name)\n"
    tests = (
        "import importlib\n"
        f"def test_meal_handler_rejects_bad_input():\n{loads}"
        f"def test_meal_handler_accepts_good_input():\n{loads}"
    )
    reasons = reach_reasons(
        "_describe", tests, test_path="test_integration.py", module_path="src/meal_handler.py"
    )
    assert set(reasons) == {
        "test_meal_handler_rejects_bad_input",
        "test_meal_handler_accepts_good_input",
    }


SHOP_MODULES = {
    "src/shop/api.py": (
        "from shop import utils\n\n\ndef handle(order):\n    return utils._normalize(order)\n"
    ),
    "src/shop/utils.py": (
        "def _normalize(order):\n    return order\n\n\ndef _unused(order):\n    return order\n"
    ),
}
# `import shop` gives the package signal and "review" the category's terminology, neither of
# which adds to a test whose only tie to `utils` is reaching it through `api`.
API_TESTS = (
    "import shop\nimport shop.api\n\n"
    "def test_an_order_under_review_is_handled():\n    assert shop.api.handle({}) == {}\n"
)
UTILS_TESTS = (
    "from shop import utils\n\n"
    "def test_the_module_loads():\n    assert utils\n\n"
    "def test_the_module_has_a_name():\n    assert utils.__name__\n"
)
REACHED_ELSEWHERE = "changed symbol reached through another module's calls"


def cross_reasons(symbol: str, *, incomplete: bool = False) -> dict[str, list[str]]:
    graph = CallGraph(
        {path: module_references(text, package_of(path)) for path, text in SHOP_MODULES.items()},
        incomplete=incomplete,
    )
    index = TestIndex(
        tests=[
            *_index_source("tests/test_api.py", API_TESTS),
            *_index_source("tests/test_utils.py", UTILS_TESTS),
        ],
        incomplete=False,
        warnings=[],
    )
    candidate = build_candidates(
        [
            StructuralDelta(
                path="src/shop/utils.py",
                symbol=symbol,
                kind="symbol_added",
                old=None,
                new="(order)",
                old_lines=None,
                new_lines=LineRange(start=1, end=2),
                hunk_id="src/shop/utils.py#hunk-001",
            )
        ]
    )[0]
    mapped = map_candidate_tests([candidate], index, WeaverConfig(), graph)[0]
    return {f"{item.path}::{item.symbol}": item.match_reasons for item in mapped}


def test_a_test_reaches_a_module_it_does_not_import_through_another() -> None:
    """`test_api.py` never imports `utils`; it calls `api.handle`, which calls into it."""
    reasons = cross_reasons("_normalize")
    assert reasons["tests/test_api.py::test_an_order_under_review_is_handled"] == [
        REACHED_ELSEWHERE
    ]


def test_a_helper_reached_only_through_another_module_is_not_ruled_untested() -> None:
    """`test_utils.py` loads `utils` and calls neither helper; only `_normalize` is reached."""
    assert "tests/test_utils.py::<module>" in cross_reasons("_normalize")
    assert cross_reasons("_unused") == {}


def test_a_graph_cut_short_by_its_bounds_rules_no_helper_untested() -> None:
    assert cross_reasons("_unused", incomplete=True) == {"tests/test_utils.py::<module>": FILE_ONLY}


BILLING_MODULES = {
    "src/shop/__init__.py": "from .billing import Invoice\n",
    "src/shop/billing.py": "class Invoice:\n    def total(self):\n        return 0\n",
    "src/shop/api.py": (
        "from shop.billing import Invoice\n\n\n"
        "def make_invoice():\n    return Invoice()\n\n\n"
        "def report():\n    return {'total': 0}\n"
    ),
}
BILLING_TESTS = (
    "from shop import api\n"
    "from shop import Invoice\n\n"
    "def test_a_made_invoice_is_empty():\n"
    "    invoice = api.make_invoice()\n"
    "    assert invoice.total() == 0\n\n"
    "def test_a_new_invoice_is_empty():\n"
    "    assert Invoice().total() == 0\n\n"
    "def test_an_invoice_is_made():\n"
    "    assert api.make_invoice()\n\n"
    "def test_the_report_sums_nothing():\n"
    "    assert api.report().total == 0\n\n"
    "def test_a_fresh_invoice_starts_from_nothing():\n"
    "    invoice, total = api.make_invoice(), 0\n"
    "    assert invoice and total == 0\n"
)


def test_a_test_reaches_the_methods_it_calls_on_an_instance_of_a_class_it_reaches() -> None:
    """`api.make_invoice()` hands the test an `Invoice`, and `shop` re-exports the class.

    Neither test imports `billing`. A test that reaches the class without calling `total` on
    it, or calls `total` on what `api.report()` returns, does not reach the method.
    """
    graph = CallGraph(
        {path: module_references(text, package_of(path)) for path, text in BILLING_MODULES.items()}
    )
    index = TestIndex(
        tests=_index_source("tests/test_api.py", BILLING_TESTS), incomplete=False, warnings=[]
    )
    candidate = build_candidates(
        [
            StructuralDelta(
                path="src/shop/billing.py",
                symbol="Invoice.total",
                kind="symbol_added",
                old=None,
                new="(self)",
                old_lines=None,
                new_lines=LineRange(start=2, end=3),
                hunk_id="src/shop/billing.py#hunk-001",
            )
        ]
    )[0]
    mapped = map_candidate_tests([candidate], index, WeaverConfig(), graph)[0]
    assert {item.symbol: item.match_reasons for item in mapped} == {
        "test_a_made_invoice_is_empty": [REACHED_ELSEWHERE],
        "test_a_new_invoice_is_empty": [REACHED_ELSEWHERE],
    }


def test_a_test_holds_an_instance_it_makes_or_reads_not_one_a_module_makes_on_import() -> None:
    """`kitchen` makes a `Store` on import. Loading it hands a test none, so `.get` on a
    dictionary is no call on the store, while reading `kitchen._store` or making a
    `kitchen.Store()`, through the class the package re-exports, holds one."""
    modules = {
        "src/kitchen/__init__.py": "from .stores import Store\n\n_store = Store()\n",
        "src/kitchen/stores.py": "class Store:\n    def get(self, key):\n        return key\n",
    }
    tests = (
        "import kitchen\n\n"
        "def test_a_lookup_reads_a_dict():\n    assert {'a': 1}.get('a') == 1 and kitchen\n\n"
        "def test_the_store_returns_the_key():\n    assert kitchen._store.get('a') == 'a'\n\n"
        "def test_a_new_store_returns_the_key():\n    assert kitchen.Store().get('a') == 'a'\n"
    )
    graph = CallGraph(
        {path: module_references(text, package_of(path)) for path, text in modules.items()}
    )
    index = TestIndex(
        tests=_index_source("tests/test_kitchen.py", tests), incomplete=False, warnings=[]
    )
    candidate = build_candidates(
        [
            StructuralDelta(
                path="src/kitchen/stores.py",
                symbol="Store.get",
                kind="symbol_added",
                old=None,
                new="(self, key)",
                old_lines=None,
                new_lines=LineRange(start=2, end=3),
                hunk_id="src/kitchen/stores.py#hunk-001",
            )
        ]
    )[0]
    mapped = map_candidate_tests([candidate], index, WeaverConfig(), graph)[0]
    assert {item.symbol: item.match_reasons for item in mapped} == {
        "test_the_store_returns_the_key": [REACHED_ELSEWHERE],
        "test_a_new_store_returns_the_key": [REACHED_ELSEWHERE],
    }


def test_a_package_init_is_named_by_its_package() -> None:
    """Tests import, mirror, and name a package, never its `__init__`."""
    assert _module_name("src/shop/__init__.py") == "shop"
    assert _module_name("__init__.py") == ""
    assert _module_name("src/shop/cart.py") == "shop.cart"
    assert _mirrored("src/shop/__init__.py", "tests/test_shop.py")
    assert not _mirrored("src/shop/__init__.py", "tests/test_init.py")
    assert not _mirrored("__init__.py", "tests/test_init.py")
    assert _distinctive_stem("src/meal_planner/__init__.py") == "meal_planner"
    assert _distinctive_stem("__init__.py") is None


def init_reasons(modules: dict[str, str], tests: str, test_path: str, path: str, symbol: str):
    roots = source_roots(modules)
    graph = CallGraph(
        {item: module_references(text, package_of(item, roots)) for item, text in modules.items()},
        roots=roots,
    )
    index = TestIndex(tests=_index_source(test_path, tests), incomplete=False, warnings=[])
    candidate = build_candidates(
        [
            StructuralDelta(
                path=path,
                symbol=symbol,
                kind="symbol_added",
                old=None,
                new="()",
                old_lines=None,
                new_lines=LineRange(start=1, end=2),
                hunk_id=f"{path}#hunk-001",
            )
        ]
    )[0]
    mapped = map_candidate_tests([candidate], index, WeaverConfig(), graph)[0]
    return {item.symbol: item.match_reasons for item in mapped}


def test_a_test_importing_a_package_imports_its_init_and_one_importing_a_module_does_not() -> None:
    """Importing `shop.cart` runs `shop/__init__.py`, as loading any module runs what it
    imports, which no test counts; importing the package or a name it binds does."""
    modules = {
        "src/shop/__init__.py": "def total():\n    return 0\n",
        "src/shop/cart.py": "class Cart:\n    pass\n",
    }
    tests = (
        "def test_the_package_sums_nothing():\n"
        "    from shop import total\n"
        "    assert total() == 0\n\n"
        "def test_a_cart_is_made():\n"
        "    from shop.cart import Cart\n"
        "    assert Cart()\n"
    )
    reasons = init_reasons(modules, tests, "tests/test_shop.py", "src/shop/__init__.py", "total")
    assert reasons == {
        "test_the_package_sums_nothing": [
            "mirrored source/test path",
            "direct module or symbol import",
            REACHED,
        ]
    }


def test_a_test_loading_the_root_package_imports_the_root_init() -> None:
    """`meal-manager`'s tests load the plugin's own `__init__.py` as the root package."""
    modules = {"__init__.py": "def register(ctx):\n    return ctx\n", "src/__init__.py": ""}
    tests = (
        "import importlib\nfrom pathlib import Path\n\n"
        "_PLUGIN_DIR = Path(__file__).resolve().parent\n"
        "_pkg = importlib.import_module(_PLUGIN_DIR.name)\n\n"
        "def test_wiring_hands_back_the_context():\n    assert _pkg.register({}) == {}\n"
    )
    reasons = init_reasons(modules, tests, "test_plugin.py", "__init__.py", "register")
    assert reasons == {
        "test_wiring_hands_back_the_context": ["direct module or symbol import", REACHED]
    }


ERROR_MODULES = {
    "src/store/__init__.py": "from .disk import Repo\n",
    "src/store/disk.py": (
        "def write(path, data):\n    raise OSError(path)\n\n\n"
        "class Repo:\n    def save(self, data):\n        return write('x', data)\n\n\n"
        "def export(data):\n    return Repo().save(data)\n"
    ),
}
ERROR_TESTS = (
    "from store import Repo, disk\n\n"
    "def test_an_error_propagates_from_the_disk():\n    assert disk.write('y', {})\n\n"
    "def test_the_data_reaches_the_disk():\n    assert disk.write('x', {})\n\n"
    "def test_a_failure_to_store_is_an_error():\n    assert Repo().save({})\n\n"
    "def test_an_export_failure_is_an_error():\n    assert disk.export({})\n"
)


def error_reasons(symbol: str) -> dict[str, list[str]]:
    roots = source_roots(ERROR_MODULES)
    graph = CallGraph(
        {
            path: module_references(text, package_of(path, roots))
            for path, text in ERROR_MODULES.items()
        },
        roots=roots,
    )
    index = TestIndex(
        tests=_index_source("tests/test_store.py", ERROR_TESTS), incomplete=False, warnings=[]
    )
    candidate = build_candidates(
        [
            StructuralDelta(
                path="src/store/disk.py",
                symbol=symbol,
                kind="raise_change",
                old="raise ValueError(path)",
                new="raise OSError(path)",
                old_lines=LineRange(start=2, end=2),
                new_lines=LineRange(start=2, end=2),
                hunk_id="src/store/disk.py#hunk-001",
            )
        ]
    )[0]
    mapped = map_candidate_tests([candidate], index, WeaverConfig(), graph)[0]
    return {item.symbol: item.match_reasons for item in mapped}


IMPORTED = "direct module or symbol import"
WORDED = "behavior-category terminology"


def test_category_words_confirm_a_test_that_calls_the_change_not_one_that_reaches_it() -> None:
    """A test reaching `write` through `Repo.save` is about saving, and its words about failure
    describe that. Counted, they ranked it above a test calling `write` without them, as tests
    reaching `atomic_write_json` through a repository outranked the tests calling it."""
    assert list(error_reasons("write").items()) == [
        ("test_an_error_propagates_from_the_disk", [IMPORTED, REACHED, WORDED]),
        ("test_the_data_reaches_the_disk", [IMPORTED, REACHED]),
        ("test_a_failure_to_store_is_an_error", [IMPORTED, REACHED]),
        ("test_an_export_failure_is_an_error", [IMPORTED, REACHED]),
    ]


def test_a_method_called_on_an_instance_the_test_makes_is_called_directly() -> None:
    """`Repo().save()` calls `save` on a `Repo` the test makes, through the class `store`
    re-exports; `disk.export()` calls it on one `export` makes."""
    reasons = error_reasons("Repo.save")
    assert reasons["test_a_failure_to_store_is_an_error"] == [IMPORTED, REACHED, WORDED]
    assert reasons["test_an_export_failure_is_an_error"] == [IMPORTED, REACHED]


def test_the_subject_of_a_change_is_the_words_of_its_class_function_and_module() -> None:
    """A dunder and `<module>` name nothing, and an `__init__.py` is its package."""
    assert _subject_words(
        "src/repositories/json_fridge.py", "JsonFridgeRepository.load_entries"
    ) == {
        "json",
        "fridge",
        "repository",
        "load",
        "entries",
    }
    assert _subject_words("src/dii/__init__.py", "IngredientSessionStore.__init__") == {
        "ingredient",
        "session",
        "store",
        "dii",
    }
    assert _subject_words("src/shop/the_cart.py", "<module>") == {"cart"}
    # A function named by one word, a verb tests use for anything; a class named by one, a noun.
    assert _subject_words("semantic_diff_weaver/call_graph.py", "CallGraph._uses") == {
        "call",
        "graph",
    }
    assert _subject_words("src/distiller/report.py", "run") == {"report"}
    assert _subject_words("src/dish.py", "Dish") == {"dish"}


SWEEP_MODULES = {
    "src/store/__init__.py": "",
    "src/store/disk.py": (
        "def _sweep_stale_temps(path):\n    raise OSError(path)\n\n\n"
        "def save(path):\n    return _sweep_stale_temps(path)\n"
    ),
}
SWEEP_TESTS = (
    "from store import disk\n\n"
    "def test_a_save_returns_its_path():\n    assert disk.save('x')\n\n"
    "def test_a_save_error_is_reported():\n    assert disk.save('y')\n\n"
    "def test_stale_temps_are_swept_on_save():\n    assert disk.save('z')\n"
)


def test_tests_reaching_a_change_alike_are_ordered_by_the_words_they_share_with_it() -> None:
    """All three mirror `disk.py`, import it, and reach `_sweep_stale_temps` through `save`, so
    they score alike. The one named after stale temps comes first, then the one whose words are
    about errors, the change's category, which describe a test's topic rather than the change's
    subject."""
    roots = source_roots(SWEEP_MODULES)
    graph = CallGraph(
        {
            path: module_references(text, package_of(path, roots))
            for path, text in SWEEP_MODULES.items()
        },
        roots=roots,
    )
    index = TestIndex(
        tests=_index_source("tests/test_disk.py", SWEEP_TESTS), incomplete=False, warnings=[]
    )
    candidate = build_candidates(
        [
            StructuralDelta(
                path="src/store/disk.py",
                symbol="_sweep_stale_temps",
                kind="raise_change",
                old="raise ValueError(path)",
                new="raise OSError(path)",
                old_lines=LineRange(start=2, end=2),
                new_lines=LineRange(start=2, end=2),
                hunk_id="src/store/disk.py#hunk-001",
            )
        ]
    )[0]
    mapped = map_candidate_tests([candidate], index, WeaverConfig(), graph)[0]
    assert [(item.symbol, item.match_score) for item in mapped] == [
        ("test_stale_temps_are_swept_on_save", 0.65),
        ("test_a_save_error_is_reported", 0.65),
        ("test_a_save_returns_its_path", 0.65),
    ]


REPOSITORY_MODULES = {
    "src/repos/__init__.py": (
        "from .json_dish import JsonDishRepository\n\ndish_repo = JsonDishRepository()\n"
    ),
    "src/repos/json_dish.py": "class JsonDishRepository:\n    def __init__(self):\n        self.rows = []\n",
    "src/repos/json_fridge.py": (
        "class JsonFridgeRepository:\n    def __init__(self):\n        self.rows = []\n"
    ),
    "src/repos/json_alias.py": (
        "class JsonAliasRepository:\n    def __init__(self):\n        self.rows = []\n"
    ),
}
REPOSITORY_TESTS = (
    "import repos\n\n"
    "def test_a_json_write_cleans_up():\n    assert repos\n\n"
    "def test_the_dish_is_added():\n    assert repos\n"
)


def test_a_shared_word_weighs_by_how_few_neighbouring_symbols_name_it() -> None:
    """Both tests reach `JsonDishRepository.__init__` by loading `repos`, alike. `json` is in the
    name of every repository beside it and `dish` in one, so the test about dishes comes first."""
    graph = CallGraph(
        {
            path: module_references(text, package_of(path))
            for path, text in REPOSITORY_MODULES.items()
        }
    )
    index = TestIndex(
        tests=_index_source("tests/test_store.py", REPOSITORY_TESTS), incomplete=False, warnings=[]
    )
    candidate = build_candidates(
        [
            StructuralDelta(
                path="src/repos/json_dish.py",
                symbol="JsonDishRepository.__init__",
                kind="symbol_added",
                old=None,
                new="(self)",
                old_lines=None,
                new_lines=LineRange(start=2, end=3),
                hunk_id="src/repos/json_dish.py#hunk-001",
            )
        ]
    )[0]
    mapped = map_candidate_tests([candidate], index, WeaverConfig(), graph)[0]
    assert [item.symbol for item in mapped] == [
        "test_the_dish_is_added",
        "test_a_json_write_cleans_up",
    ]
    assert mapped[0].match_score == mapped[1].match_score


TABLE_MODULES = {
    "src/checks/__init__.py": "",
    "src/checks/rules.py": (
        "def _classify_condition(delta):\n    raise OSError(delta)\n\n\n"
        "CLASSIFIERS = (_classify_condition,)\n\n\n"
        "def classify(delta):\n    return [rule(delta) for rule in CLASSIFIERS]\n\n\n"
        "def symbols(text):\n    return text.split()\n"
    ),
}
TABLE_TESTS = (
    "from checks import rules\n\n"
    "def test_a_text_is_split_into_symbols():\n    assert rules.symbols('a b')\n\n"
    "def test_each_rule_judges_a_delta():\n    assert rules.classify(0)\n\n"
    "def test_a_condition_site_is_found():\n    assert rules.symbols('if x')\n"
)


def test_a_test_reaching_a_change_through_calls_ranks_before_one_only_loading_it() -> None:
    """All three load `rules.py`, whose table names `_classify_condition`, so they reach it
    and score alike. The one calling the code that runs the table comes before the one that
    only splits text, and after the one named after a condition, which is likelier about it."""
    roots = source_roots(TABLE_MODULES)
    graph = CallGraph(
        {
            path: module_references(text, package_of(path, roots))
            for path, text in TABLE_MODULES.items()
        },
        roots=roots,
    )
    index = TestIndex(
        tests=_index_source("tests/test_checks.py", TABLE_TESTS), incomplete=False, warnings=[]
    )
    candidate = build_candidates(
        [
            StructuralDelta(
                path="src/checks/rules.py",
                symbol="_classify_condition",
                kind="raise_change",
                old="raise ValueError(delta)",
                new="raise OSError(delta)",
                old_lines=LineRange(start=2, end=2),
                new_lines=LineRange(start=2, end=2),
                hunk_id="src/checks/rules.py#hunk-001",
            )
        ]
    )[0]
    mapped = map_candidate_tests([candidate], index, WeaverConfig(), graph)[0]
    assert [item.symbol for item in mapped] == [
        "test_a_condition_site_is_found",
        "test_each_rule_judges_a_delta",
        "test_a_text_is_split_into_symbols",
    ]
    assert len({item.match_score for item in mapped}) == 1


ROUTE_TESTS = """BASE = "https://shop.example/api"


def _delete(client, order):
    return client.delete(f"/orders/{order.id}")


def test_an_order_is_deleted(client, order):
    assert _delete(client, order).status_code == 204


def test_an_order_is_read(client):
    assert client.get("/orders/7?expand=lines").status_code == 200


def test_the_catalog_is_served(session):
    assert session.request("DELETE", BASE + "/orders/7")


def test_the_root_is_up(client):
    assert client.get(BASE + "/").ok and client.get("https://shop.example/v1/orders/7/")


def test_a_note_is_not_a_route():
    assert "//cdn.example/orders/7" and "orders/7" and "ftp://shop.example/orders/7"
"""


def route_candidate(symbol: str):
    return build_candidates(
        [
            StructuralDelta(
                path="openapi.yaml",
                symbol=symbol,
                kind="api_operation_removed",
                old="operation: {}",
                new=None,
                old_lines=None,
                new_lines=None,
                hunk_id=None,
                metadata={"location": "operation", "context": "response"},
            )
        ]
    )[0]


def route_reasons(symbol: str) -> dict[str, tuple[float, list[str]]]:
    index = TestIndex(
        tests=_index_source("tests/test_orders.py", ROUTE_TESTS), incomplete=False, warnings=[]
    )
    mapped = map_candidate_tests([route_candidate(symbol)], index, WeaverConfig())[0]
    return {item.symbol: (item.match_score, item.match_reasons) for item in mapped}


def test_the_index_reads_the_routes_a_test_and_its_helpers_request() -> None:
    routes = {item.symbol: item.routes for item in _index_source("tests/t.py", ROUTE_TESTS)}
    assert routes == {
        "test_an_order_is_deleted": frozenset({"/orders/x"}),
        "test_an_order_is_read": frozenset({"/orders/7?expand=lines"}),
        # A URL's path; a concatenation is not followed, so `BASE + "/orders/7"` is its parts.
        "test_the_catalog_is_served": frozenset({"/orders/7"}),
        "test_the_root_is_up": frozenset({"/", "/v1/orders/7/"}),
        "test_a_note_is_not_a_route": frozenset(),
    }


def test_a_url_that_does_not_parse_requests_no_route_and_keeps_its_test() -> None:
    source = (
        "def test_a_bad_host_is_rejected(client):\n"
        "    assert client.get('http://[shop/orders/7') and client.get('/orders/7')\n"
    )
    (indexed,) = _index_source("tests/t.py", source)
    assert indexed.routes == frozenset({"/orders/7"})


def test_the_routes_of_a_test_are_bounded() -> None:
    source = "def test_many(client):\n" + "".join(
        f"    client.get('/r{index}')\n" for index in range(test_mapper.MAX_ROUTES_PER_TEST + 5)
    )
    (indexed,) = _index_source("tests/t.py", source)
    assert len(indexed.routes) == test_mapper.MAX_ROUTES_PER_TEST
    long_route = "/" + "a" * test_mapper.MAX_ROUTE_CHARS
    (indexed,) = _index_source(
        "tests/t.py", f"def test_long(client):\n    client.get({long_route!r})\n"
    )
    assert indexed.routes == frozenset()


@pytest.mark.parametrize(
    ("route", "path", "matches"),
    [
        ("/orders/{id}", "/orders/7", True),
        ("/orders/{id}", "/orders/x/", True),
        ("/orders/{id}", "/orders/7?expand=lines", True),
        ("/orders/{id}", "/api/v1/orders/7", True),
        ("/orders/{id}", "/orders/7/lines", False),
        ("/orders/{id}", "/orders/", False),
        ("/orders/{id}", "/preorders/7", False),
        ("/orders.json", "/ordersXjson", False),
        ("/", "/", True),
        ("/", "/?ready=1", True),
        ("/", "/orders", False),
    ],
)
def test_a_route_template_matches_the_paths_that_request_it(
    route: str, path: str, matches: bool
) -> None:
    assert (test_mapper._route_pattern(route).fullmatch(path) is not None) is matches


def test_an_operation_maps_to_the_tests_that_request_its_route() -> None:
    assert route_reasons("DELETE /orders/{id}") == {
        "test_an_order_is_deleted": (
            0.8,
            ["requests the changed operation's route", "calls `delete`"],
        ),
        # Calls no method by name, so requests the route for any of its operations.
        "test_the_catalog_is_served": (0.55, ["requests the changed operation's route"]),
    }
    # The test that reads the order calls only `get`, so it requests the route for another
    # operation; a test with the route only under a base path or a trailing slash still does.
    assert set(route_reasons("GET /orders/{id}")) == {
        "test_an_order_is_read",
        "test_the_catalog_is_served",
        "test_the_root_is_up",
    }
    assert set(route_reasons("GET /")) == {"test_the_root_is_up"}


def test_a_change_without_a_route_maps_to_no_test() -> None:
    assert route_reasons("Order") == {}
    assert route_reasons("<security>") == {}
