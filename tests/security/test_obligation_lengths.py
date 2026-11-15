"""A repository's names and code cannot make an obligation too long to validate.

An obligation's title holds 300 characters and its given, when, and then 1000 each, and one that
does not validate fails the whole analysis. Each fragment an obligation quotes is bounded, but an
added callable's obligation lists several of its outcomes and the paths to them, and a private
helper's names the code calling it, so a pull request with long enough names, signatures, and
tests kept itself from being reviewed at all.
"""

from __future__ import annotations

from semantic_diff_weaver.models import MAX_OBLIGATION_TEXT_CHARS, MAX_OBLIGATION_TITLE_CHARS
from semantic_diff_weaver.service import analyze
from semantic_diff_weaver.textutil import split_code_spans

BASE = '"""Tools."""\n'
FIELDS = ("title", "given", "when", "then")


def _long(name: str) -> str:
    """An expression longer than any fragment an obligation quotes whole."""
    return " + ".join(f"{name}{index}" for index in range(40))


def _obligations(repo_factory, old: str, new: str) -> list[dict]:
    repo, base, head = repo_factory({"m.py": old}, {"m.py": new})
    envelope = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "both"}
    )
    assert envelope["success"], envelope.get("message")
    return envelope["analysis"]["test_obligations"]


def _fits(obligation: dict) -> bool:
    """Within its limits, and with no code span left open where a cut would have left one."""
    limits = {"title": MAX_OBLIGATION_TITLE_CHARS}
    return all(
        len(obligation[field]) <= limits.get(field, MAX_OBLIGATION_TEXT_CHARS)
        and not any("`" in part for part, code in split_code_spans(obligation[field]) if not code)
        for field in FIELDS
    )


def test_an_added_function_with_a_long_signature_and_long_outcomes_is_still_analyzed(
    repo_factory,
) -> None:
    parameters = ", ".join(f"parameter_number_{index}: int = {index}" for index in range(30))
    lines = [f"def wide({parameters}):"]
    for value in "VWZ":
        for test in (f"{value.lower()}a", f"{value.lower()}b"):
            lines += [f"    if ({_long(test)}) > 0:", f"        return {_long(value)}"]
    added = "\n".join([*lines, f"    return {_long('U')}"])
    obligations = _obligations(repo_factory, BASE, f"{BASE}\n\n{added}\n")
    assert obligations
    assert all(_fits(item) for item in obligations)


def test_a_helper_called_by_long_names_through_long_paths_is_still_analyzed(repo_factory) -> None:
    helper = "_" + "h" * 115
    callers = [f"caller_number_{index}_" + "c" * 100 for index in range(4)]
    nested = "".join(
        f"{'    ' * (level + 1)}if ({_long(f't{level}')}) > 0:\n" for level in range(4)
    )
    added = f"def {helper}(x):\n{nested}{'    ' * 5}return {_long('V')}\n    return {_long('U')}\n"
    old = BASE + "".join(f"\n\ndef {name}(x):\n    return x + 1\n" for name in callers)
    new = f"{BASE}\n\n{added}" + "".join(
        f"\n\ndef {name}(x):\n    return {helper}(x) + 1\n" for name in callers
    )
    obligations = _obligations(repo_factory, old, new)
    assert all(_fits(item) for item in obligations)
    (own,) = [item for item in obligations if item["title"].startswith("Exercise")]
    # Its title names the first caller and how many more, and its `given` how many conditions
    # the path to its first outcome has, where naming each would not fit.
    assert own["title"].endswith(f"through `{callers[0]}` and 3 more")
    assert "on a path through 4 conditions, or returns `U0 + U1" in own["given"]


def test_a_helper_whose_outcome_reads_long_locals_is_still_analyzed(repo_factory) -> None:
    helper = "_" + "h" * 115
    callers = [f"caller_number_{index}_" + "c" * 100 for index in range(3)]
    assigned = "".join(f"    local_{index} = {_long(f'v{index}')}\n" for index in range(4))
    reads = " + ".join(f"local_{index}" for index in range(4))
    added = f"def {helper}(x):\n{assigned}    if x:\n        return {reads}\n    return 0\n"
    old = BASE + "".join(f"\n\ndef {name}(x):\n    return x + 1\n" for name in callers)
    new = f"{BASE}\n\n{added}" + "".join(
        f"\n\ndef {name}(x):\n    return {helper}(x) + 1\n" for name in callers
    )
    obligations = _obligations(repo_factory, old, new)
    assert all(_fits(item) for item in obligations)
    (own,) = [item for item in obligations if item["title"].startswith("Exercise")]
    assert "`local_0` is `v00 + v01" in own["given"]
