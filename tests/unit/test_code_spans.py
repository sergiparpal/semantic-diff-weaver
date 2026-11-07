"""A code span shows its text as it is: Markdown reads no escape inside one."""

from __future__ import annotations

import pytest

from semantic_diff_weaver.renderer import _code, _inline
from semantic_diff_weaver.textutil import code_span, split_code_spans

BACKSLASH = chr(92)


@pytest.mark.parametrize(
    ("text", "span"),
    [
        # Nothing is escaped: `parse\_amount` would show its backslash.
        ("parse_amount", "`parse_amount`"),
        ("tests/test_cli.py::Test.test_a", "`tests/test_cli.py::Test.test_a`"),
        ("**bold**[link](javascript:alert)", "`**bold**[link](javascript:alert)`"),
        # A span opens with more backticks than any run it holds, so none of its own closes it.
        ("a`b", "``a`b``"),
        ("a``b`c", "```a``b`c```"),
        # Text that starts or ends with a backtick, or with a space at both, is padded, since the
        # span drops one space at each end.
        ("`x`", "`` `x` ``"),
        ("x````", "````` x```` `````"),
        (" x ", "`  x  `"),
        (" x", "` x`"),
    ],
)
def test_a_code_span_shows_its_text_and_holds_its_backticks(text: str, span: str) -> None:
    assert _code(text) == span


def test_a_control_character_in_a_code_span_is_made_visible() -> None:
    text = "a" + chr(0x1B) + "[31m" + chr(0x202E) + "b" + chr(10) + "c"
    assert _code(text) == (
        "`a" + BACKSLASH + "u001b[31m" + BACKSLASH + "u202eb" + BACKSLASH + "nc`"
    )


@pytest.mark.parametrize(
    "text",
    [
        "x",
        "a`b",
        "`x`",
        "x````",
        " x ",
        " ",
        "  ",
        "render('`` and `')",
        "[link](javascript:alert(1)) @everyone <b>",
    ],
)
def test_a_code_span_reads_back_as_the_text_it_holds(text: str) -> None:
    assert split_code_spans(code_span(text)) == [(text, True)]


def test_prose_and_spans_come_apart_in_order() -> None:
    text = f"Call {code_span('f(x)')} where it called {code_span('g()')}."
    assert split_code_spans(text) == [
        ("Call ", False),
        ("f(x)", True),
        (" where it called ", False),
        ("g()", True),
        (".", False),
    ]


@pytest.mark.parametrize(
    ("text", "parts"),
    [
        # A run that nothing closes is prose, and so is everything after it.
        ("a ` b", [("a ` b", False)]),
        ("a `` b ` c", [("a `` b ` c", False)]),
        # A longer run is skipped by a shorter span, as Markdown reads it.
        ("``a`b`` c `d`", [("a`b", True), (" c ", False), ("d", True)]),
        ("", []),
    ],
)
def test_backticks_that_open_no_span_are_prose(text: str, parts: list[tuple[str, bool]]) -> None:
    assert split_code_spans(text) == parts


def test_inline_text_escapes_its_prose_and_keeps_its_spans() -> None:
    text = f"Assert *this* in {code_span('a_b*[c](d)')}"
    assert _inline(text) == "Assert \\*this\\* in `a_b*[c](d)`"
