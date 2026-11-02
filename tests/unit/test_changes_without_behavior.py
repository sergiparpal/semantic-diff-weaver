"""Changes that alter no code that runs: type-checking blocks, imports, and reworded messages.

Each was a finding on the labeled benchmark's development cases while its label said that no
behavior changed: `docs/benchmark.md` records the measurements.
"""

from __future__ import annotations

import ast

import pytest

from semantic_diff_weaver.ast_diff.compare import compare_symbol, is_reworded
from semantic_diff_weaver.ast_diff.extract import (
    extract_symbols,
    file_names,
    fingerprint_without_messages,
    message_literals,
    type_checking_spellings,
)
from semantic_diff_weaver.ast_diff.types import StructuralDelta, SymbolSnapshot
from semantic_diff_weaver.models import BehaviorCategory, RulesConfig
from semantic_diff_weaver.semantic_candidates import classify_delta
from semantic_diff_weaver.source import SourceHunk

WIDE_HUNK = SourceHunk(id="hunk-001", old_start=1, old_count=400, new_start=1, new_count=400)


def _symbol(source: str, name: str) -> SymbolSnapshot:
    return {item.qualified_name: item for item in extract_symbols(source)}[name]


def _deltas(old: str, new: str, name: str = "<module>", path: str = "pkg/mod.py") -> list[str]:
    return [item.kind for item in _compare(old, new, name, path)]


def _compare(
    old: str, new: str, name: str = "<module>", path: str = "pkg/mod.py"
) -> list[StructuralDelta]:
    return compare_symbol(path, _symbol(old, name), _symbol(new, name), (WIDE_HUNK,))


def _rule(delta: StructuralDelta) -> tuple[BehaviorCategory, str]:
    category, _, _, _, _, rule = classify_delta(delta, RulesConfig())
    return category, rule


@pytest.mark.parametrize(
    "header",
    [
        pytest.param("from typing import TYPE_CHECKING\n", id="imported"),
        pytest.param("from typing_extensions import TYPE_CHECKING\n", id="typing-extensions"),
        pytest.param("TYPE_CHECKING = False\n", id="assigned-false"),
        pytest.param("from typing import *\n", id="star-imported-from-typing"),
    ],
)
def test_a_type_checking_block_is_no_part_of_the_module(header: str) -> None:
    """requests#7441 dropped an import from its `if TYPE_CHECKING:` block, which nothing runs."""
    old = f"{header}if TYPE_CHECKING:\n    from shop.cart import Cart, Item\nLIMIT = 3\n"
    (delta,) = _compare(old, old.replace("Cart, Item", "Cart"))
    assert delta.kind == "structural_refactor"
    assert delta.metadata["materiality"] == 0.0
    assert _rule(delta) == (BehaviorCategory.REFACTOR, "SDW-RULE-REFACTOR")


def test_typing_s_constant_may_be_read_through_the_module() -> None:
    old = "import typing as t\nif t.TYPE_CHECKING:\n    from shop import Cart\nLIMIT = 3\n"
    assert _deltas(old, old.replace("Cart", "Basket")) == ["structural_refactor"]
    plain = old.replace("import typing as t", "import typing").replace("t.TYPE", "typing.TYPE")
    assert _deltas(plain, plain.replace("Cart", "Basket")) == ["structural_refactor"]


def test_what_runs_beside_a_type_checking_block_is_still_compared() -> None:
    """The `else` of `if TYPE_CHECKING:` runs, and so does the body of `if not TYPE_CHECKING:`."""
    otherwise = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n    import chardet\nelse:\n    chardet = detect(1)\n"
    )
    assert _deltas(otherwise, otherwise.replace("detect(1)", "detect(2)")) == [
        "assignment_change",
        "call_change",
    ]
    negated = "import typing as t\nif not t.TYPE_CHECKING:\n    LIMIT = 3\n"
    assert _deltas(negated, negated.replace("3", "4")) == ["assignment_change"]
    elsewhere = "import typing as t\nif t.DEBUG:\n    LIMIT = 3\n"
    assert _deltas(elsewhere, elsewhere.replace("3", "4")) == ["assignment_change"]


def test_a_class_s_type_checking_block_is_no_part_of_the_class() -> None:
    old = (
        "from typing import TYPE_CHECKING\n"
        "class Cart:\n    if TYPE_CHECKING:\n        items: list\n    limit = 3\n"
    )
    assert _deltas(old, old.replace("items: list", "items: tuple"), "Cart") == [
        "structural_refactor"
    ]


def test_an_import_moved_out_of_a_type_checking_block_to_be_used_is_only_an_import() -> None:
    """requests#7505 imports `_types as _t` to run `_t.has_read`, where `_t` named the same module
    for annotations before, and drops an import nothing reads any more."""
    old = (
        "from __future__ import annotations\n"
        "from typing import TYPE_CHECKING\n"
        "from ._types import SupportsRead as _SupportsRead\n"
        "if TYPE_CHECKING:\n    from . import _types as _t\n\n"
        "def prepare(data: _t.Body):\n    return isinstance(data, _SupportsRead)\n"
    )
    new = (
        "from __future__ import annotations\n"
        "from typing import TYPE_CHECKING\n"
        "from . import _types as _t\n\n"
        "def prepare(data: _t.Body):\n    return _t.has_read(data)\n"
    )
    (delta,) = _compare(old, new)
    assert delta.kind == "structural_refactor"
    assert delta.metadata == {"materiality": 0.0, "imports_only": True}
    assert (delta.old, delta.new) == ("_SupportsRead = ._types.SupportsRead", "_t = ._types")
    assert _rule(delta) == (BehaviorCategory.REFACTOR, "SDW-RULE-REFACTOR-IMPORTS")


@pytest.mark.parametrize(
    ("old", "new", "kind"),
    [
        pytest.param(
            "def edit(path):\n    return path\n",
            "import os\n\ndef edit(path):\n    return os.fspath(path)\n",
            "structural_refactor",
            id="added-and-read",
        ),
        pytest.param(
            "import os\nimport sys\n\ndef edit(path):\n    return os.fspath(path)\n",
            "import os\n\ndef edit(path):\n    return os.fspath(path)\n",
            "structural_refactor",
            id="removed-and-unused",
        ),
        pytest.param(
            "import sys\nimport os\n\ndef edit(path):\n    return os.fspath(path)\n",
            "import os\nimport sys\n\ndef edit(path):\n    return os.fspath(path)\n",
            "structural_refactor",
            id="reordered",
        ),
        pytest.param(
            "def visit(node):\n    last_leaf = node.leaves[-1]\n    return last_leaf\n",
            "from black.nodes import last_leaf\n\ndef visit(node):\n    return last_leaf(node)\n",
            "structural_refactor",
            id="named-as-a-local-variable-before",
        ),
        pytest.param(
            "def load():\n    return 1\n",
            "import readline\n\ndef load():\n    return 1\n",
            "unknown_structure",
            id="imported-for-what-loading-does",
        ),
        pytest.param(
            "import os\n\ndef edit(path):\n    return os.fspath(path)\n",
            "def edit(path):\n    return os.fspath(path)\n",
            "unknown_structure",
            id="removed-and-still-read",
        ),
        pytest.param(
            "import json\n\ndef dump(value):\n    return json.dumps(value)\n",
            "import orjson as json\n\ndef dump(value):\n    return json.dumps(value)\n",
            "unknown_structure",
            id="rebound-elsewhere",
        ),
        pytest.param(
            "import xml\n\ndef parse(text):\n    return xml.etree.ElementTree.parse(text)\n",
            "import xml.etree.ElementTree\n\n"
            "def parse(text):\n    return xml.etree.ElementTree.parse(text)\n",
            "unknown_structure",
            id="now-loading-a-submodule",
        ),
        pytest.param(
            "def read(path):\n    return open(path)\n",
            "from .compat import open\n\ndef read(path):\n    return open(path)\n",
            "unknown_structure",
            id="shadowing-a-builtin-the-code-read",
        ),
        pytest.param(
            "def read(path):\n    return path\n",
            "from __future__ import annotations\n\ndef read(path):\n    return path\n",
            "unknown_structure",
            id="future-import",
        ),
        pytest.param(
            "def read(path):\n    return path\n",
            "from .compat import *\n\ndef read(path):\n    return path\n",
            "unknown_structure",
            id="star-import",
        ),
        pytest.param(
            "import os\nos\n",
            "import os\nimport sys\nsys\n",
            "unknown_structure",
            id="with-another-statement",
        ),
    ],
)
def test_a_module_whose_imports_alone_changed_is_a_refactor_only_in_step_with_its_code(
    old: str, new: str, kind: str
) -> None:
    assert _deltas(old, new) == [kind]


@pytest.mark.parametrize(
    ("removed", "kind"),
    [
        pytest.param("Cart", "unknown_structure", id="public"),
        pytest.param("_Cart", "structural_refactor", id="private"),
    ],
)
def test_a_package_keeps_each_public_name_it_imports(removed: str, kind: str) -> None:
    """Importers of the package may read what its `__init__.py` imports."""
    old = f"from .cart import {removed}\nfrom .money import total\n"
    assert _deltas(old, "from .money import total\n", path="shop/__init__.py") == [kind]
    assert _deltas(old, "from .money import total\n", path="shop/cart_view.py") == [
        "structural_refactor"
    ]


def test_a_name_the_module_exports_is_read() -> None:
    """A star import of the module reads what `__all__` lists, even with nothing else reading it."""
    old = "__all__ = ['Cart']\nfrom .cart import Cart\n"
    assert _deltas(old, "__all__ = ['Cart']\n") == ["unknown_structure"]


def test_a_file_s_names_are_read_by_scope() -> None:
    source = (
        "import os\n"
        "__all__ = ['exported']\n"
        "__all__.extend(['more'])\n"
        "@decorate\n"
        "def f(param: Annotated = default, *rest, **options) -> Returned:\n"
        "    local = 1\n"
        "    global SHARED\n"
        "    SHARED = 2\n"
        "    def inner():\n"
        "        return local + enclosing_only\n"
        "    match options:\n"
        "        case {'a': captured, **others}:\n"
        "            return captured, others\n"
        "    try:\n"
        "        pass\n"
        "    except Failure as caught:\n"
        "        return caught\n"
        "    return [item for item in rest], os.sep, lambda q, d=late: q + free\n"
        "class C:\n"
        "    attr = at_class_level\n"
    )
    mentioned, read, imported = file_names(ast.parse(source))
    assert read == {
        "Annotated", "Failure", "Returned", "__all__", "at_class_level", "decorate", "default",
        "enclosing_only", "exported", "free", "late", "local", "more", "os",
    }  # fmt: skip
    assert mentioned - read == {
        "SHARED", "attr", "captured", "caught", "item", "options", "others", "q", "rest",
    }  # fmt: skip
    assert imported == {"os": frozenset({"os"})}


@pytest.mark.parametrize(
    ("source", "spellings"),
    [
        pytest.param("from typing import TYPE_CHECKING\n", {"TYPE_CHECKING"}, id="imported"),
        pytest.param("import typing as t\n", {"t.TYPE_CHECKING"}, id="module-alias"),
        pytest.param("TYPE_CHECKING = False\n", {"TYPE_CHECKING"}, id="assigned-false"),
        pytest.param("x = 1\n", set(), id="unbound"),
    ],
)
def test_a_file_spells_typing_s_constant_by_its_imports(source: str, spellings: set[str]) -> None:
    assert type_checking_spellings(ast.parse(source)) == spellings


def test_a_reworded_message_changes_no_behavior() -> None:
    """httpx#3519 fixed "an sync" to "a sync" in four messages."""
    old = (
        "def close(stream):\n"
        "    if not stream.is_async:\n"
        "        raise RuntimeError('Attempted to call an sync close on an async stream.')\n"
        "    stream.close()\n"
    )
    (delta,) = _compare(old, old.replace("an sync", "a sync"), "close")
    assert delta.kind == "structural_refactor"
    assert delta.metadata == {"materiality": 0.0, "reworded": True}
    assert delta.old == "RuntimeError('Attempted to call an sync close on an async stream.')"
    assert delta.new == "RuntimeError('Attempted to call a sync close on an async stream.')"
    assert _rule(delta) == (BehaviorCategory.REFACTOR, "SDW-RULE-REFACTOR-MESSAGE")


@pytest.mark.parametrize(
    ("old", "new"),
    [
        pytest.param(
            "f'Bad value {value!r} here'", "f'Wrong value {value!r} given'", id="f-string"
        ),
        pytest.param("'Bad value %s' % value", "'Wrong value %s' % value", id="percent"),
        pytest.param("'Bad value {}'.format(value)", "'Wrong {} value'.format(value)", id="format"),
        pytest.param("'Bad value: ' + value", "'Wrong value: ' + value", id="concatenated"),
        pytest.param("message='Bad value'", "message='Wrong value'", id="keyword"),
        pytest.param("code, 'Bad value'", "code, 'Wrong value'", id="beside-a-value"),
    ],
)
def test_a_message_is_reworded_however_it_is_built(old: str, new: str) -> None:
    template = "def check(value, code):\n    raise errors.ValidationError({})\n"
    assert _deltas(template.format(old), template.format(new), "check") == ["structural_refactor"]


@pytest.mark.parametrize(
    ("old", "new"),
    [
        pytest.param("KeyError('user_id')", "KeyError('account_id')", id="a-key"),
        pytest.param(
            "ValueError('Unknown scheme')",
            "ValueError(f'Unknown scheme {url.scheme!r}')",
            id="a-value-now-told",
        ),
        pytest.param("ValueError('Bad value')", "TypeError('Bad value')", id="another-class"),
        pytest.param("ValueError('Bad value')", "ValueError('Bad value', url)", id="another-value"),
        pytest.param("Abort('Bad value')", "Abort('Wrong value')", id="not-named-an-exception"),
        pytest.param(
            "ValueError(_('Bad value'))", "ValueError(_('Wrong value'))", id="through-a-call"
        ),
    ],
)
def test_a_raise_that_says_or_raises_something_else_is_an_error_handling_change(
    old: str, new: str
) -> None:
    template = "def check(url):\n    raise {}\n"
    (delta,) = _compare(template.format(old), template.format(new), "check")
    assert delta.kind == "raise_change"
    assert _rule(delta)[0] is BehaviorCategory.ERROR_HANDLING


def test_a_message_reworded_beside_a_change_to_the_body_is_reported_with_it() -> None:
    """Only a body that differs in nothing else is read as reworded; a signature is no body."""
    old = "def check(x):\n    if x < 3:\n        raise ValueError('Too small a value')\n"
    new = old.replace("x < 3", "x <= 3").replace("Too small a value", "Value too small")
    assert _deltas(old, new, "check") == ["comparison_change", "condition_change", "raise_change"]
    signed = old.replace("(x)", "(x, y=1)").replace("Too small a value", "Value too small")
    assert _deltas(old, signed, "check") == ["signature_change"]


def test_a_message_reworded_in_a_nested_function_is_reworded_in_the_function_around_it() -> None:
    old = (
        "def outer():\n"
        "    def inner():\n"
        "        raise ValueError('Too small a value')\n"
        "    return inner\n"
    )
    new = old.replace("Too small a value", "Value too small")
    assert _deltas(old, new, "outer") == ["structural_refactor"]
    assert _deltas(old, new, "outer.inner") == ["structural_refactor"]


def test_a_renamed_symbol_is_a_refactor_by_how_alike_it_is() -> None:
    old = _symbol("def check(x):\n    raise ValueError('Too small a value')\n", "check")
    new = _symbol("def verify(x):\n    raise ValueError('Value too small')\n", "verify")
    (delta,) = compare_symbol("m.py", old, new, (WIDE_HUNK,))
    assert delta.kind == "structural_refactor"
    assert "reworded" not in delta.metadata


def test_blanking_a_message_leaves_the_tree_as_it_was() -> None:
    """The literals are blanked in place for one fingerprint, in a tree every symbol shares."""
    tree = ast.parse("def check():\n    raise ValueError('Too small a value')\n")
    function = tree.body[0]
    assert isinstance(function, ast.FunctionDef)
    before = ast.dump(tree)
    assert fingerprint_without_messages(function.body, "plain") != "plain"
    assert ast.dump(tree) == before
    raised = function.body[0]
    assert isinstance(raised, ast.Raise)
    assert [literal.value for literal in message_literals(raised)] == ["Too small a value"]
    reraised, bare = (
        statement
        for statement in ast.parse("raise\nraise ValueError\n").body
        if isinstance(statement, ast.Raise)
    )
    assert message_literals(reraised) == message_literals(bare) == []


def test_a_snapshot_built_without_a_message_fingerprint_is_never_reworded() -> None:
    old = _symbol("def check():\n    raise ValueError('Too small')\n", "check")
    new = _symbol("def check():\n    raise ValueError('Very small')\n", "check")
    assert is_reworded(old, new)
    old.fingerprint_without_messages = None
    assert not is_reworded(old, new)
