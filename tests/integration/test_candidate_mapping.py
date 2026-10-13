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


def test_a_test_changed_with_its_code_is_a_candidate_and_not_a_finding(repo_factory) -> None:
    """Analyzed as product code, every changed test became a finding of its own.

    A branch that added a module and its tests reported each test function as a medium-risk
    behavior change with an obligation to test it, and those findings set the overall risk.
    """
    tests = "from src.api import allowed\n\ndef test_allowed_boundary():\n    assert allowed(5)\n"
    opt_in = "version: 1\nrules:\n  analyze_test_files: true\n"
    repo, base, head = repo_factory(
        {"src/api.py": "def allowed(x):\n    return x < 5\n"},
        {"src/api.py": "def allowed(x):\n    return x <= 5\n", "tests/test_api.py": tests},
    )
    request = {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}

    result = analyze(request)
    assert result["scope"]["analyzed_files"] == ["src/api.py"]
    assert result["scope"]["excluded_counts"] == {"test_file": 1}
    assert {item["evidence"][0]["path"] for item in result["behavior_changes"]} == {"src/api.py"}
    assert "tests/test_api.py" in _candidate_tests(result)

    repo, base, head = repo_factory(
        {"src/api.py": "def allowed(x):\n    return x < 5\n", ".semantic-diff-weaver.yaml": opt_in},
        {
            "src/api.py": "def allowed(x):\n    return x <= 5\n",
            "tests/test_api.py": tests,
            ".semantic-diff-weaver.yaml": opt_in,
        },
    )
    opted_in = analyze({**request, "repo_path": str(repo), "base_ref": base, "head_ref": head})
    assert opted_in["scope"]["analyzed_files"] == ["src/api.py", "tests/test_api.py"]


def test_an_untested_change_is_never_shown_another_changes_tests(repo_factory) -> None:
    """Two boundary changes in different modules, of which only one has a test.

    Their obligations used to merge on the template's wording alone, so the untested
    `free_shipping` was listed under `test_discount_above_threshold` as having a candidate.
    """
    tests = (
        "from app.pricing import discount\n\n"
        "def test_discount_above_threshold():\n    assert discount(150) == 10\n"
    )
    repo, base, head = repo_factory(
        {
            "app/pricing.py": "def discount(total):\n    return 10 if total > 100 else 0\n",
            "app/shipping.py": "def free_shipping(weight):\n    return weight < 5\n",
            "tests/test_pricing.py": tests,
        },
        {
            "app/pricing.py": "def discount(total):\n    return 10 if total >= 100 else 0\n",
            "app/shipping.py": "def free_shipping(weight):\n    return weight <= 5\n",
            "tests/test_pricing.py": tests,
        },
    )
    result = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}
    )
    symbols = {item["id"]: item["evidence"][0]["symbol"] for item in result["behavior_changes"]}
    assert sorted(symbols.values()) == ["discount", "free_shipping"]
    for obligation in result["test_obligations"]:
        covered = {symbols[item] for item in obligation["behavior_change_ids"]}
        if covered == {"free_shipping"}:
            assert obligation["candidate_existing_tests"] == []
            assert obligation["coverage_status"] == "no_candidate_found"
        else:
            assert covered == {"discount"}
            assert obligation["coverage_status"] == "candidate_exists_unverified"


def test_a_brief_names_the_test_file_when_none_of_its_tests_names_the_change(
    repo_factory,
) -> None:
    tests = (
        "import cli\n\n"
        "def test_a_command_line_is_parsed():\n    assert cli.main([])\n\n"
        "def test_an_unknown_option_exits_with_two():\n    assert cli.main(['-x'])\n"
    )
    repo, base, head = repo_factory(
        {"src/cli.py": "def accepts(x):\n    return x < 5\n", "tests/test_cli.py": tests},
        {"src/cli.py": "def accepts(x):\n    return x <= 5\n", "tests/test_cli.py": tests},
    )
    envelope = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "both"}
    )
    assert {
        (test["path"], test["symbol"])
        for obligation in envelope["analysis"]["test_obligations"]
        for test in obligation["candidate_existing_tests"]
    } == {("tests/test_cli.py", "<module>")}
    assert (
        "Candidate existing tests (unverified): `tests/test\\_cli\\.py` (whole file; 0.50; "
        "mirrored source/test path, direct module or symbol import)"
    ) in envelope["markdown"]


def test_a_helper_the_tests_reach_is_told_apart_from_one_nothing_calls(repo_factory) -> None:
    """Both used to get the module's test file; only `_parser` is reached through `main`."""
    tests = (
        "import cli\n\n"
        "def invoke(argv):\n    return cli.main(argv)\n\n"
        "def test_a_command_line_is_parsed():\n    assert invoke(['x']) == 2\n\n"
        "def test_no_arguments_parse_to_one():\n    assert invoke([]) == 1\n"
    )
    module = "def main(argv):\n    return _parser(argv)\n\n\ndef _parser(argv):\n    return {}\n"
    unused = "\n\ndef _describe(argv):\n    return str(argv)\n"
    repo, base, head = repo_factory(
        {"src/cli.py": module.format("len(argv)"), "tests/test_cli.py": tests},
        {"src/cli.py": module.format("len(argv) + 1") + unused, "tests/test_cli.py": tests},
    )
    result = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}
    )
    symbols = {item["id"]: item["evidence"][0]["symbol"] for item in result["behavior_changes"]}
    reasons = {
        (symbols[behavior_id], test["symbol"]): test["match_reasons"]
        for obligation in result["test_obligations"]
        for behavior_id in obligation["behavior_change_ids"]
        for test in obligation["candidate_existing_tests"]
    }
    assert (
        "changed symbol reached through calls"
        in reasons[("_parser", "test_a_command_line_is_parsed")]
    )
    assert {
        obligation["coverage_status"]
        for obligation in result["test_obligations"]
        if any(symbols[item] == "_describe" for item in obligation["behavior_change_ids"])
    } == {"no_candidate_found"}


def test_a_test_reaches_a_change_through_a_module_the_diff_leaves_alone(repo_factory) -> None:
    """`test_api.py` calls `api.handle`, unchanged, which calls the changed `utils._normalize`."""
    api = "from shop import utils\n\n\ndef handle(order):\n    return utils._normalize(order)\n"
    tests = (
        "import shop.api\n\ndef test_an_order_is_handled():\n    assert shop.api.handle({}) == {}\n"
    )
    unchanged = {"src/shop/__init__.py": "", "src/shop/api.py": api, "tests/test_api.py": tests}
    repo, base, head = repo_factory(
        {**unchanged, "src/shop/utils.py": "def _normalize(order):\n    return order\n"},
        {**unchanged, "src/shop/utils.py": "def _normalize(order):\n    return dict(order)\n"},
    )
    result = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}
    )
    assert result["scope"]["analyzed_files"] == ["src/shop/utils.py"]
    candidates = {
        (test["path"], test["symbol"]): test["match_reasons"]
        for obligation in result["test_obligations"]
        for test in obligation["candidate_existing_tests"]
    }
    assert candidates == {
        ("tests/test_api.py", "test_an_order_is_handled"): [
            "changed symbol reached through another module's calls"
        ]
    }


def test_a_test_reaches_a_changed_method_through_an_instance_another_module_makes(
    repo_factory,
) -> None:
    """`checkout` makes a `Cart` from another module and calls `cart.total()`, which changed."""
    checkout = (
        "from shop import Cart\n\n\ndef checkout():\n    cart = Cart()\n    return cart.total()\n"
    )
    tests = "from shop import checkout\n\ndef test_an_empty_order_costs_nothing():\n"
    tests += "    assert checkout.checkout() == 0\n"
    unchanged = {
        "src/shop/__init__.py": "from .cart import Cart\n",
        "src/shop/checkout.py": checkout,
        "tests/test_checkout.py": tests,
    }
    cart = "class Cart:\n    def total(self):\n        return {}\n"
    repo, base, head = repo_factory(
        {**unchanged, "src/shop/cart.py": cart.format(0)},
        {**unchanged, "src/shop/cart.py": cart.format("sum([])")},
    )
    result = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}
    )
    assert result["scope"]["analyzed_files"] == ["src/shop/cart.py"]
    candidates = {
        (test["path"], test["symbol"]): test["match_reasons"]
        for obligation in result["test_obligations"]
        for test in obligation["candidate_existing_tests"]
    }
    assert candidates == {
        ("tests/test_checkout.py", "test_an_empty_order_costs_nothing"): [
            "changed symbol reached through another module's calls"
        ]
    }


def test_a_test_reaches_a_changed_method_through_an_instance_another_module_holds(
    repo_factory,
) -> None:
    """`handlers.list_items` calls `pantry.load()` on the instance `stores.py` makes on import."""
    handlers = (
        "from kitchen.stores import pantry\n\n\ndef list_items():\n    return pantry.load()\n"
    )
    tests = "from kitchen import handlers\n\ndef test_an_empty_pantry_lists_nothing():\n"
    tests += "    assert not handlers.list_items()\n"
    unchanged = {
        "src/kitchen/__init__.py": "",
        "src/kitchen/stores.py": "from kitchen.storage import JsonPantry\n\npantry = JsonPantry()\n",
        "src/kitchen/handlers.py": handlers,
        "tests/test_handlers.py": tests,
    }
    storage = "class JsonPantry:\n    def load(self):\n        return {}\n"
    repo, base, head = repo_factory(
        {**unchanged, "src/kitchen/storage.py": storage.format("[]")},
        {**unchanged, "src/kitchen/storage.py": storage.format("list()")},
    )
    result = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}
    )
    assert result["scope"]["analyzed_files"] == ["src/kitchen/storage.py"]
    candidates = {
        (test["path"], test["symbol"]): test["match_reasons"]
        for obligation in result["test_obligations"]
        for test in obligation["candidate_existing_tests"]
    }
    assert candidates == {
        ("tests/test_handlers.py", "test_an_empty_pantry_lists_nothing"): [
            "changed symbol reached through another module's calls"
        ]
    }


def test_a_test_that_runs_the_package_covers_its_changed_main_module(repo_factory) -> None:
    """`python -m pkg` is how a test reaches `pkg/__main__.py`; nothing imports it."""
    test_main = (
        "import subprocess\nimport sys\n\n"
        "def test_the_program_exits_with_what_main_returns():\n"
        "    subprocess.run([sys.executable, '-P', '-m', 'pkg'], check=False)\n"
    )
    repo, base, head = repo_factory(
        {
            "src/pkg/__init__.py": "",
            "src/pkg/__main__.py": "raise SystemExit(0)\n",
            "tests/test_main.py": test_main,
        },
        {
            "src/pkg/__init__.py": "",
            "src/pkg/__main__.py": "raise SystemExit(1)\n",
            "tests/test_main.py": test_main,
        },
    )
    result = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}
    )
    assert {item["evidence"][0]["path"] for item in result["behavior_changes"]} == {
        "src/pkg/__main__.py"
    }
    assert set(_candidate_tests(result)) == {"tests/test_main.py"}


def test_a_file_named_like_a_test_is_left_out_only_when_it_defines_one(repo_factory) -> None:
    """The name alone would have hidden this repository's own `test_mapper.py` from review."""
    repo, base, head = repo_factory(
        {"src/test_mapper.py": "def threshold():\n    return 0.35\n"},
        {
            "src/test_mapper.py": "def threshold():\n    return 0.45\n",
            "test_unit.py": "def test_threshold():\n    assert True\n",
        },
    )
    result = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}
    )
    assert result["scope"]["analyzed_files"] == ["src/test_mapper.py"]
    assert result["scope"]["excluded_counts"] == {"test_file": 1}
    assert result["behavior_changes"]


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
