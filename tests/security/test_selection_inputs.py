"""Test selection reads a hostile repository's paths and sources, and hands paths to a shell."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from semantic_diff_weaver.git_diff import GitRepository, changed_files
from semantic_diff_weaver.models import AnalysisResult, TestSelection, WeaverConfig
from semantic_diff_weaver.renderer import render_markdown
from semantic_diff_weaver.selection import UNLISTED, UNSAFE, select_tests
from semantic_diff_weaver.service import analyze
from tests.conftest import git

Factory = Callable[..., tuple[Path, str, str]]

SHOP = {
    "shop/__init__.py": "",
    "shop/money.py": "def cents(amount):\n    return amount\n",
    "tests/test_money.py": "from shop.money import cents\n\n\ndef test_cents():\n    pass\n",
}
CHANGED = {"shop/money.py": "def cents(amount):\n    return 0\n"}
IMPORTING = "from shop.money import cents\n\n\ndef test_it():\n    pass\n"


def _select(path: Path, base: str, head: str) -> TestSelection:
    repo = GitRepository.open(str(path))
    return select_tests(repo, base, head, changed_files(repo, base, head), WeaverConfig())


@pytest.mark.parametrize(
    "name",
    [
        "tests/test_$(touch pwned).py",
        "tests/test_`id`.py",
        "tests/test_a;b.py",
        "tests/test_a b.py",
        "-x_test.py",
        pytest.param(
            "tests/test_*.py",
            marks=pytest.mark.skipif(os.name == "nt", reason="no `*` in Windows file names"),
        ),
        pytest.param(
            "tests/test_a\nb.py",
            marks=pytest.mark.skipif(os.name == "nt", reason="no newline in Windows file names"),
        ),
    ],
)
def test_a_path_a_shell_or_pytest_would_read_is_never_selected(
    repo_factory: Factory, name: str
) -> None:
    """A selected path goes into `pytest $tests`: anything but a plain path runs everything."""
    path, base, head = repo_factory({**SHOP, name: IMPORTING}, CHANGED)
    result = _select(path, base, head)
    assert result == TestSelection(run_all=True, reasons=[UNSAFE.format(count=1)])


def test_files_never_read_by_policy_are_never_named(repo_factory: Factory) -> None:
    secrets = {".ssh/id_ed25519": "key\n", ".env.production": "TOKEN=x\n"}
    path, base, head = repo_factory(
        {
            **SHOP,
            "shop/credentials.py": "KEY = 1\n",
            "tests/test_keys.py": ("from shop import credentials\n\n\ndef test_key():\n    pass\n"),
        },
        {**secrets, **CHANGED},
    )
    whole = _select(path, base, head)
    assert whole.reasons == [
        "2 changed file(s) are never read by policy, and a test may read them."
    ]
    path, base, head = repo_factory(
        {
            **SHOP,
            "shop/credentials.py": "KEY = 1\n",
            "tests/test_keys.py": ("from shop import credentials\n\n\ndef test_key():\n    pass\n"),
        },
        CHANGED,
    )
    narrow = _select(path, base, head)
    assert [item.path for item in narrow.tests] == ["tests/test_keys.py", "tests/test_money.py"]
    for result in (whole, narrow):
        dumped = str(result.model_dump())
        assert not any(word in dumped for word in ("credentials", "id_ed25519", ".ssh", ".env"))


def test_a_symlink_out_of_the_repository_is_never_followed(
    repo_factory: Factory, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("SECRET = 'outside the repository'\n", encoding="utf-8")
    path, base, _ = repo_factory(SHOP, CHANGED)
    try:
        (path / "shop" / "linked.py").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    (path / "tests" / "test_link.py").write_text(
        "from shop import linked\n\n\ndef test_link():\n    pass\n", encoding="utf-8"
    )
    git(path, "add", "--all")
    git(path, "commit", "-q", "-m", "link")
    head = git(path, "rev-parse", "HEAD")
    result = _select(path, base, head)
    assert result.reasons == [
        "shop/linked.py could not be read within the bounds for following imports."
    ]
    assert "outside the repository" not in str(result.model_dump())


@pytest.mark.skipif(sys.platform != "linux", reason="needs a file name that is not UTF-8")
def test_a_python_file_whose_name_cannot_be_read_runs_the_whole_suite(
    repo_factory: Factory,
) -> None:
    path, base, changed_head = repo_factory(SHOP, CHANGED)
    (path / "shop" / os.fsdecode(b"\xff.py")).write_bytes(b"X = 1\n")
    git(path, "add", "--all")
    git(path, "commit", "-q", "-m", "unreadable name")
    head = git(path, "rev-parse", "HEAD")
    repo = GitRepository.open(str(path))
    # The module the tests import changed; the file beside it may import anything.
    changed = changed_files(repo, base, changed_head)
    assert select_tests(repo, base, head, changed, WeaverConfig()).reasons == [UNLISTED]


def test_import_cycles_and_deep_nesting_end(repo_factory: Factory) -> None:
    deep = "VALUE = " + "[" * 1000 + "]" * 1000 + "\n"
    path, base, head = repo_factory(
        {
            **SHOP,
            "shop/a.py": "from shop import b\n",
            "shop/b.py": "from shop import a\nfrom shop import money\n",
            "tests/test_cycle.py": "from shop import a\n\n\ndef test_a():\n    pass\n",
        },
        CHANGED,
    )
    assert [item.path for item in _select(path, base, head).tests] == [
        "tests/test_cycle.py",
        "tests/test_money.py",
    ]
    path, base, head = repo_factory({**SHOP, "tests/test_deep.py": deep}, CHANGED)
    assert _select(path, base, head).reasons == [
        "tests/test_deep.py could not be parsed to follow its imports."
    ]


def test_selection_never_runs_the_code_it_reads(repo_factory: Factory, tmp_path: Path) -> None:
    sentinel = tmp_path / "ran"
    planted = f"open({str(sentinel)!r}, 'w').close()\n"
    path, base, head = repo_factory(
        {
            **SHOP,
            "tests/conftest.py": planted,
            "shop/__init__.py": planted,
            "tests/test_money.py": planted + IMPORTING,
        },
        CHANGED,
    )
    assert [item.path for item in _select(path, base, head).tests] == ["tests/test_money.py"]
    assert not sentinel.exists()


def test_the_brief_escapes_the_paths_a_reason_names(repo_factory: Factory) -> None:
    name = "docs/[x](javascript:alert(1))\x1b[31m.md"
    if os.name == "nt":  # pragma: no cover - platform dependent
        pytest.skip("Windows file names hold no control characters")
    path, base, head = repo_factory(SHOP, {**CHANGED, name: "x\n"})
    result = AnalysisResult.model_validate(
        analyze(
            {"repo_path": str(path), "base_ref": base, "head_ref": head, "output_format": "json"}
        )
    )
    assert result.test_selection is not None
    assert name in result.test_selection.reasons[0]
    markdown = render_markdown(result)
    assert "\x1b" not in markdown
    assert "](javascript:" not in markdown
    assert "\\[x\\]\\(javascript:alert\\(1\\)\\)\\\\u001b\\[31m\\.md" in markdown
