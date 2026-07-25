"""Statement ordering is a permutation, not any inequality of the order tuple.

``SymbolSnapshot.statement_order`` entries embed unparsed expression content, so comparing
the tuples for inequality also fires on pure content edits. These tests pin the permutation
definition and the deltas that genuinely cover each shape of change.
"""

from __future__ import annotations

from semantic_diff_weaver.ast_diff import analyze_ast
from semantic_diff_weaver.ast_diff.compare import is_reordering
from semantic_diff_weaver.source import SourceHunk, SourceRevisionPair


def changed(old: str, new: str) -> SourceRevisionPair:
    return SourceRevisionPair(
        path="src/sample.py",
        old_path="src/sample.py",
        new_path="src/sample.py",
        hunks=(SourceHunk(id="hunk-001", old_start=1, old_count=100, new_start=1, new_count=100),),
        old_text=old,
        new_text=new,
    )


def kinds(old: str, new: str) -> set[str]:
    return {item.kind for item in analyze_ast([changed(old, new)]).deltas}


def test_added_keyword_argument_is_not_a_reordering() -> None:
    """The regression this fix targets: a content-only edit inside a return."""
    result = kinds(
        "def fetch(client, x):\n    return client.fetch(x)\n",
        "def fetch(client, x):\n    return client.fetch(x, strict=True)\n",
    )
    assert "call_change" in result
    assert "statement_order_change" not in result


def test_inserted_statement_is_not_a_reordering() -> None:
    """An insertion changes the multiset, so it is not a permutation."""
    result = kinds(
        "def save(user):\n    return user\n",
        "def save(user):\n    notify(user)\n    return user\n",
    )
    assert "call_change" in result
    assert "statement_order_change" not in result


def test_swapped_sibling_calls_still_report_ordering() -> None:
    """A genuine permutation is caught by the order-sensitive call delta."""
    result = kinds(
        "def go():\n    f()\n    g()\n",
        "def go():\n    g()\n    f()\n",
    )
    assert "call_order_change" in result


def test_swapped_sibling_assignments_report_an_assignment_change() -> None:
    """``assignment_change`` is order-sensitive and already covers this move."""
    result = kinds(
        "def go():\n    a = 1\n    b = 2\n    return a + b\n",
        "def go():\n    b = 2\n    a = 1\n    return a + b\n",
    )
    assert "assignment_change" in result
    assert "statement_order_change" not in result


def test_is_reordering_predicate() -> None:
    assert is_reordering(("a", "b"), ("a", "b")) is False
    assert is_reordering(("a", "b"), ("b", "a")) is True
    assert is_reordering(("a", "b"), ("a", "c")) is False
    assert is_reordering(("a",), ("a", "b")) is False
    assert is_reordering((), ()) is False
    # A permutation of a multiset with repeats is still a permutation.
    assert is_reordering(("a", "a", "b"), ("b", "a", "a")) is True
    assert is_reordering(("a", "a", "b"), ("a", "b", "b")) is False
