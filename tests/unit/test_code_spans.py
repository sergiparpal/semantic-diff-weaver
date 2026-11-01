"""A code span shows its text as it is: Markdown reads no escape inside one."""

from __future__ import annotations

import pytest

from semantic_diff_weaver.renderer import _code

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
