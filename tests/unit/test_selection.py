from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path

import pytest

import semantic_diff_weaver.selection as selection
from semantic_diff_weaver.errors import ErrorCode, WeaverError
from semantic_diff_weaver.git_diff import GitRepository, changed_files
from semantic_diff_weaver.models import TestSelection, WeaverConfig
from semantic_diff_weaver.selection import select_tests
from tests.conftest import git

Factory = Callable[..., tuple[Path, str, str]]

SHOP = {
    "shop/__init__.py": "",
    "shop/money.py": "def cents(amount):\n    return round(amount * 100)\n",
    "shop/cart.py": (
        "from shop.money import cents\n\n\ndef total(items):\n"
        "    return sum(cents(item) for item in items)\n"
    ),
    "shop/report.py": "def render(rows):\n    return '\\n'.join(rows)\n",
    "tests/test_money.py": (
        "from shop.money import cents\n\n\ndef test_cents():\n    assert cents(1) == 100\n"
    ),
    "tests/test_cart.py": (
        "from shop.cart import total\n\n\ndef test_total():\n    assert total([1]) == 100\n"
    ),
    "tests/test_report.py": (
        "from shop import report\n\n\ndef test_render():\n    assert report.render(['a']) == 'a'\n"
    ),
}


def select(
    repo_factory: Factory,
    old: dict[str, str],
    new: dict[str, str],
    *,
    remove: tuple[str, ...] = (),
    config: WeaverConfig | None = None,
) -> TestSelection:
    path, base, head = repo_factory(old, new, remove=remove)
    repo = GitRepository.open(str(path))
    return select_tests(repo, base, head, changed_files(repo, base, head), config or WeaverConfig())


def selected(result: TestSelection) -> dict[str, list[str]]:
    assert not result.run_all, result.reasons
    return {item.path: item.reasons for item in result.tests}


def test_a_changed_module_selects_the_tests_that_import_it(repo_factory: Factory) -> None:
    result = select(repo_factory, SHOP, {"shop/money.py": "def cents(amount):\n    return 0\n"})
    assert selected(result) == {
        "tests/test_cart.py": ["It reaches shop/money.py, which changed, through shop/cart.py."],
        "tests/test_money.py": ["It loads shop/money.py, which changed."],
    }


def test_a_changed_package_init_selects_every_test_of_its_modules(repo_factory: Factory) -> None:
    result = select(repo_factory, SHOP, {"shop/__init__.py": "VERSION = 2\n"})
    assert sorted(selected(result)) == [
        "tests/test_cart.py",
        "tests/test_money.py",
        "tests/test_report.py",
    ]


def test_a_changed_test_file_selects_itself(repo_factory: Factory) -> None:
    changed = SHOP["tests/test_report.py"] + "\n\ndef test_more():\n    pass\n"
    result = select(repo_factory, SHOP, {"tests/test_report.py": changed})
    assert selected(result) == {"tests/test_report.py": ["It changed."]}


def test_a_changed_helper_selects_the_tests_that_import_it_but_not_itself(
    repo_factory: Factory,
) -> None:
    old = {
        **SHOP,
        "tests/__init__.py": "",
        "tests/helpers.py": "def rows():\n    return ['a']\n\n\ndef test_like():\n    pass\n",
        "tests/test_report.py": (
            "from tests.helpers import rows\n\n\ndef test_rows():\n    assert rows()\n"
        ),
    }
    result = select(repo_factory, old, {"tests/helpers.py": "def rows():\n    return []\n"})
    assert selected(result) == {
        "tests/test_report.py": ["It loads tests/helpers.py, which changed."]
    }


def test_a_changed_conftest_selects_the_tests_below_it(repo_factory: Factory) -> None:
    old = {
        "tests/conftest.py": "",
        "tests/unit/conftest.py": "",
        "tests/unit/test_a.py": "def test_a():\n    pass\n",
        "tests/other/test_b.py": "def test_b():\n    pass\n",
        "test_root.py": "def test_root():\n    pass\n",
    }
    unit = select(repo_factory, old, {"tests/unit/conftest.py": "VALUE = 1\n"})
    assert selected(unit) == {
        "tests/unit/test_a.py": ["It loads tests/unit/conftest.py, which changed."]
    }
    every = select(repo_factory, old, {"tests/conftest.py": "VALUE = 1\n"})
    assert sorted(selected(every)) == ["tests/other/test_b.py", "tests/unit/test_a.py"]


def test_a_conftest_s_plugins_are_followed(repo_factory: Factory) -> None:
    old = {
        **SHOP,
        "shop/fixtures.py": "def make():\n    return 1\n",
        "tests/conftest.py": "pytest_plugins = ['shop.fixtures']\n",
    }
    result = select(repo_factory, old, {"shop/fixtures.py": "def make():\n    return 2\n"})
    assert sorted(selected(result)) == [
        "tests/test_cart.py",
        "tests/test_money.py",
        "tests/test_report.py",
    ]
    assert selected(result)["tests/test_money.py"] == [
        "It reaches shop/fixtures.py, which changed, through tests/conftest.py."
    ]


def test_relative_imports_and_a_src_layout_resolve(repo_factory: Factory) -> None:
    old = {
        "src/shop/__init__.py": "",
        "src/shop/money.py": "def cents(amount):\n    return amount\n",
        "src/shop/cart.py": "from .money import cents\n",
        "tests/test_cart.py": "from shop.cart import cents\n\n\ndef test_cents():\n    pass\n",
    }
    result = select(repo_factory, old, {"src/shop/money.py": "def cents(amount):\n    return 0\n"})
    assert selected(result) == {
        "tests/test_cart.py": [
            "It reaches src/shop/money.py, which changed, through src/shop/cart.py."
        ]
    }


def test_a_module_imported_by_a_shorter_name_is_found(repo_factory: Factory) -> None:
    """A test that puts `tools` on `sys.path` imports `tools/release.py` as `release`."""
    old = {
        # The repository's root package is no module's package, and has no name to shorten.
        "__init__.py": "",
        "tools/release.py": "def bump():\n    return 1\n",
        "shop/json.py": "def dumps():\n    return ''\n",
        "tests/test_release.py": (
            "import sys\nsys.path.insert(0, 'tools')\nimport json\nimport release\n"
            "import yaml.constructor\n\n\n"
            "def test_bump():\n    assert release.bump() == 1\n"
        ),
    }
    result = select(repo_factory, old, {"tools/release.py": "def bump():\n    return 2\n"})
    assert selected(result) == {
        "tests/test_release.py": ["It loads tools/release.py, which changed."]
    }
    # `import json` is the standard library's, whatever the repository names `json.py`.
    unrelated = select(repo_factory, old, {"shop/json.py": "def dumps():\n    return '{}'\n"})
    assert selected(unrelated) == {}


def test_a_deleted_module_still_selects_the_tests_that_import_it(repo_factory: Factory) -> None:
    old = {
        **SHOP,
        "shop/legacy.py": "OLD = 1\n",
        "tests/test_legacy.py": "from shop.legacy import OLD\n\n\ndef test_old():\n    pass\n",
    }
    result = select(repo_factory, old, {}, remove=("shop/legacy.py",))
    assert selected(result) == {"tests/test_legacy.py": ["It loads shop/legacy.py, which changed."]}


def test_a_module_run_with_dash_m_is_followed(repo_factory: Factory) -> None:
    old = {
        **SHOP,
        "shop/__main__.py": "print('shop')\n",
        "tests/test_main.py": (
            "import subprocess\nimport sys\n\n\ndef test_main():\n"
            "    subprocess.run([sys.executable, '-m', 'shop'], check=True)\n"
        ),
    }
    result = select(repo_factory, old, {"shop/__main__.py": "print('changed')\n"})
    assert selected(result) == {"tests/test_main.py": ["It loads shop/__main__.py, which changed."]}


def test_a_file_that_loads_unnamed_modules_is_selected_for_any_change(
    repo_factory: Factory,
) -> None:
    old = {
        **SHOP,
        "tests/test_plugins.py": (
            "import importlib\n\n\ndef test_each(name='shop.report'):\n"
            "    importlib.import_module(name)\n"
        ),
        "tests/loading.py": "import importlib\n\n\ndef load(name):\n    return importlib.import_module(name)\n",
        "tests/test_loading.py": (
            "import sys\nsys.path.insert(0, 'tests')\nimport loading\n\n\n"
            "def test_load():\n    loading.load('shop')\n"
        ),
    }
    result = select(repo_factory, old, {"shop/money.py": "def cents(amount):\n    return 0\n"})
    assert selected(result)["tests/test_plugins.py"] == [
        "It loads modules it does not name, so any change may reach it."
    ]
    assert selected(result)["tests/test_loading.py"] == [
        "It reaches tests/loading.py, which loads modules it does not name, so any change may "
        "reach it."
    ]


def test_reaching_a_file_never_read_by_policy_is_reaching_anything(
    repo_factory: Factory,
) -> None:
    old = {
        **SHOP,
        "shop/credentials.py": "KEY = 'x'\n",
        "tests/test_keys.py": "from shop import credentials\n\n\ndef test_key():\n    pass\n",
    }
    result = select(repo_factory, old, {"shop/report.py": "def render(rows):\n    return ''\n"})
    reasons = selected(result)["tests/test_keys.py"]
    assert reasons == ["It loads a file never read by policy, so any change may reach it."]
    assert "credentials" not in str(result.model_dump())


def test_a_change_no_test_reaches_selects_none(repo_factory: Factory) -> None:
    old = {**SHOP, "tools/release.py": "VERSION = 1\n"}
    assert selected(select(repo_factory, old, {"tools/release.py": "VERSION = 2\n"})) == {}


def test_only_files_named_and_defining_tests_are_selected(repo_factory: Factory) -> None:
    importing = "from shop.money import cents\n"
    old = {
        **SHOP,
        "tests/check_money.py": importing + "\n\ndef test_check():\n    pass\n",
        "tests/test_helpers.py": importing + "\n\ndef helper():\n    pass\n",
        "app/tests.py": importing
        + "\n\nclass MoneyTests:\n    def test_cents(self):\n        pass\n",
        "money_test.py": importing + "\n\ndef test_suffix():\n    pass\n",
        # Directories pytest looks for no test in, unless told to.
        ".claude/hooks/tests/test_hook.py": importing + "\n\ndef test_hook():\n    pass\n",
        "build/lib/tests/test_built.py": importing + "\n\ndef test_built():\n    pass\n",
    }
    result = select(repo_factory, old, {"shop/money.py": "def cents(amount):\n    return 0\n"})
    assert sorted(selected(result)) == [
        "app/tests.py",
        "money_test.py",
        "tests/test_cart.py",
        "tests/test_money.py",
    ]


def test_a_changed_file_that_is_not_python_runs_the_whole_suite(repo_factory: Factory) -> None:
    new = {name: "changed\n" for name in ("README.md", "docs/a.md", "docs/b.md", "data.json")}
    result = select(repo_factory, SHOP, {**new, "shop/money.py": "CHANGED = 1\n"})
    assert result.run_all
    assert result.reasons == [
        "Tests may read changed files that are not Python: README.md, data.json, docs/a.md, and "
        "1 more."
    ]


def test_ignored_files_select_nothing_and_run_nothing(repo_factory: Factory) -> None:
    config = WeaverConfig(selection={"ignore": ["**/*.md", "shop/report.py"]})
    new = {"README.md": "changed\n", "docs/guide.md": "changed\n"}
    assert select(repo_factory, SHOP, new, config=config) == TestSelection(run_all=False)
    changed = {**new, "shop/report.py": "X = 1\n", "shop/money.py": "Y = 1\n"}
    assert sorted(selected(select(repo_factory, SHOP, changed, config=config))) == [
        "tests/test_cart.py",
        "tests/test_money.py",
    ]


def test_a_file_never_read_by_policy_runs_the_whole_suite_unnamed(repo_factory: Factory) -> None:
    result = select(repo_factory, SHOP, {".env": "TOKEN=x\n"})
    assert result.reasons == [
        "1 changed file(s) are never read by policy, and a test may read them."
    ]
    assert ".env" not in str(result.model_dump())


def test_a_changed_setup_file_runs_the_whole_suite(repo_factory: Factory) -> None:
    result = select(repo_factory, SHOP, {"setup.py": "from setuptools import setup\n"})
    assert result.reasons == [
        "Changed setup.py set up how the repository builds or starts Python, which every test "
        "runs on."
    ]


def test_the_same_commit_runs_the_whole_suite(repo_factory: Factory) -> None:
    path, _, head = repo_factory(SHOP, {"shop/money.py": "X = 1\n"})
    repo = GitRepository.open(str(path))
    result = select_tests(repo, head, head, [], WeaverConfig())
    assert result.reasons == [selection.SAME_COMMIT]


def test_a_test_file_that_does_not_parse_runs_the_whole_suite(repo_factory: Factory) -> None:
    result = select(repo_factory, SHOP, {"tests/test_money.py": "def test_(:\n"})
    assert result.reasons == ["tests/test_money.py could not be parsed to follow its imports."]


def test_a_file_past_the_size_bound_runs_the_whole_suite(repo_factory: Factory) -> None:
    config = WeaverConfig(rules={"max_file_bytes": 1024})
    old = {**SHOP, "tests/test_big.py": "# padding\n" * 200 + "def test_big():\n    pass\n"}
    result = select(repo_factory, old, {"shop/money.py": "X = 1\n"}, config=config)
    assert result.reasons == [
        "tests/test_big.py could not be read within the bounds for following imports."
    ]


def test_a_symlinked_test_file_runs_the_whole_suite(repo_factory: Factory) -> None:
    path, base, _ = repo_factory(SHOP, {"shop/money.py": "X = 1\n"})
    try:
        (path / "tests" / "test_link.py").symlink_to("test_money.py")
    except OSError:
        pytest.skip("symlink creation is unavailable")
    git(path, "add", "--all")
    git(path, "commit", "-q", "-m", "link")
    head = git(path, "rev-parse", "HEAD")
    repo = GitRepository.open(str(path))
    result = select_tests(repo, base, head, changed_files(repo, base, head), WeaverConfig())
    assert result.reasons == [
        "tests/test_link.py could not be read within the bounds for following imports."
    ]


def test_a_path_a_shell_would_read_runs_the_whole_suite(repo_factory: Factory) -> None:
    old = {
        **SHOP,
        "tests/test_$(x).py": "from shop.money import cents\n\n\ndef test_x():\n    pass\n",
    }
    result = select(repo_factory, old, {"shop/money.py": "X = 1\n"})
    assert result.reasons == [selection.UNSAFE.format(count=1)]


def test_the_file_bound_runs_the_whole_suite(
    repo_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(selection, "MAX_SELECTION_FILES", 3)
    # The three test files are within it, and the modules they import are past it.
    assert select(repo_factory, SHOP, {"shop/money.py": "X = 1\n"}).reasons == [selection.CAPPED]
    monkeypatch.setattr(selection, "MAX_SELECTION_FILES", 2)
    # The test files alone are past it.
    assert select(repo_factory, SHOP, {"shop/money.py": "X = 1\n"}).reasons == [selection.CAPPED]


def test_the_time_bound_runs_the_whole_suite(
    repo_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(selection, "SELECTION_TIMEOUT_SECONDS", -1.0)
    assert select(repo_factory, SHOP, {"shop/money.py": "X = 1\n"}).reasons == [selection.SLOW]


def test_git_failures_run_the_whole_suite(
    repo_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, base, head = repo_factory(SHOP, {"shop/money.py": "X = 1\n"})
    repo = GitRepository.open(str(path))
    changed = changed_files(repo, base, head)

    def refuse(*args: object, **kwargs: object) -> object:
        raise WeaverError(ErrorCode.PATH_OUTSIDE_REPOSITORY, "safe", "retry")

    monkeypatch.setattr(repo, "tree_entries", refuse)
    assert select_tests(repo, base, head, changed, WeaverConfig()).reasons == [selection.UNREAD]
    monkeypatch.setattr(repo, "list_files", refuse)
    assert select_tests(repo, base, head, changed, WeaverConfig()).reasons == [selection.UNLISTED]


def _reading(source: str) -> selection._Reading:
    reading = selection._Reading()
    selection._read(ast.parse(source), "", reading)
    return reading


@pytest.mark.parametrize(
    ("source", "names", "programs", "opaque"),
    [
        pytest.param(
            "import importlib\nimportlib.import_module(name)\n",
            set(),
            set(),
            True,
            id="computed-name",
        ),
        pytest.param(
            "import importlib\nimportlib.import_module('shop.money')\n",
            {"shop.money"},
            set(),
            False,
            id="written-name",
        ),
        pytest.param(
            "import importlib\n\n\ndef _load(suffix):\n"
            "    return importlib.import_module('shop.' + suffix)\n\n\n"
            "money = _load('money')\n",
            {"shop.money"},
            set(),
            False,
            id="loader-written",
        ),
        pytest.param(
            "import importlib\n\n\ndef _load(suffix):\n"
            "    return importlib.import_module('shop.' + suffix)\n\n\n"
            "def test_any(value):\n    _load(value)\n",
            set(),
            set(),
            True,
            id="loader-computed",
        ),
        pytest.param(
            "__import__('shop.money')\n", {"shop.money"}, set(), False, id="dunder-written"
        ),
        pytest.param("__import__(name)\n", set(), set(), True, id="dunder-computed"),
        pytest.param(
            "import pytest\npytest.importorskip('shop.money')\n",
            {"shop.money"},
            set(),
            False,
            id="importorskip",
        ),
        pytest.param(
            "import importlib.util\nimportlib.util.spec_from_file_location('x', path)\n",
            set(),
            set(),
            True,
            id="file-path",
        ),
        pytest.param("import runpy\nrunpy.run_path(path)\n", set(), set(), True, id="run-path"),
        pytest.param(
            "from importlib.metadata import entry_points\nentry_points()\n",
            set(),
            set(),
            True,
            id="plugins",
        ),
        pytest.param("exec(code)\n", set(), set(), True, id="exec-computed"),
        pytest.param(
            "exec('import shop.money')\n", {"shop.money"}, set(), False, id="exec-written"
        ),
        pytest.param("exec('def (')\n", set(), set(), True, id="exec-broken"),
        pytest.param("model.eval()\n", set(), set(), False, id="a-method-named-eval"),
        pytest.param(
            "import sys\nrun([sys.executable, '-m', 'shop'])\n", set(), {"shop"}, False, id="dash-m"
        ),
        pytest.param(
            "import sys\nrun((sys.executable, '-X', 'dev', '-B', '-m', 'shop'))\n",
            set(),
            {"shop"},
            False,
            id="dash-m-after-options",
        ),
        pytest.param(
            "import sys\nrun([sys.executable, '-c', 'import shop.money'])\n",
            {"shop.money"},
            set(),
            False,
            id="dash-c-written",
        ),
        pytest.param(
            "import sys\nrun([sys.executable, '-c', \"import sys; run([sys.executable, '-c', ''])\"])\n",
            set(),
            set(),
            True,
            id="dash-c-starting-python",
        ),
        pytest.param(
            "import sys\nrun([sys.executable, '-c', code])\n",
            set(),
            set(),
            True,
            id="dash-c-computed",
        ),
        pytest.param(
            "import sys\nrun([sys.executable, '-m'])\n", set(), set(), True, id="dash-m-missing"
        ),
        pytest.param(
            "import sys\nrun([sys.executable, 'tool.py'])\n", set(), set(), True, id="a-script"
        ),
        pytest.param(
            "import sys\nrun([sys.executable, *arguments])\n",
            set(),
            set(),
            True,
            id="computed-arguments",
        ),
        pytest.param("import sys\nrun([sys.executable])\n", set(), set(), True, id="no-arguments"),
        pytest.param(
            "import sys\nrun(f'{sys.executable} -m shop')\n",
            set(),
            {"shop"},
            True,
            id="a-command-line",
        ),
        pytest.param(
            "run(['python3.12', '-m', 'shop'])\n", set(), {"shop"}, False, id="python-by-name"
        ),
        pytest.param(
            "run(['/usr/bin/python', 'tool.py'])\n", set(), set(), True, id="python-script"
        ),
        pytest.param("run(['git', 'status'])\n", set(), set(), False, id="not-python"),
        pytest.param(
            "pytest_plugins = 'shop.plugin'\n", {"shop.plugin"}, set(), False, id="plugin"
        ),
        pytest.param(
            "pytest_plugins: list[str] = ['shop.a']\npytest_plugins += ('shop.b',)\n",
            {"shop.a", "shop.b"},
            set(),
            False,
            id="plugins",
        ),
        pytest.param("pytest_plugins = PLUGINS\n", set(), set(), True, id="plugins-computed"),
        pytest.param("plugins: list[str]\nother = ['x']\n", set(), set(), False, id="no-plugins"),
    ],
)
def test_what_a_file_loads_is_read(
    source: str, names: set[str], programs: set[str], opaque: bool
) -> None:
    reading = _reading(source)
    assert names <= reading.names
    assert reading.programs == programs
    assert reading.opaque is opaque
