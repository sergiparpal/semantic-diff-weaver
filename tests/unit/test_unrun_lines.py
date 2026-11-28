"""The changed lines no test ran: which lines are a symbol's own, and how an obligation names them.

In the Action's field test, a callable the change added asked for each of its outcomes, and the
change's own tests had already run every changed line of it but one error path: an `except` clause
and what it raised, or a `raise` under a check. A coverage report says which lines those are, so
the obligation names them, and only the lines of its own symbol: a function's span holds the
functions nested in it, and a module's the whole file.
"""

from __future__ import annotations

from semantic_diff_weaver.ast_diff import analyze_ast
from semantic_diff_weaver.coverage_map import CoverageMap, FileCoverage
from semantic_diff_weaver.evidence_scenarios import (
    MAX_UNRUN_QUOTED_LINES,
    UnrunLine,
    with_unrun_lines,
)
from semantic_diff_weaver.models import (
    MAX_OBLIGATION_TEXT_CHARS,
    BehaviorCategory,
    BehaviorChange,
    Evidence,
    ObligationType,
    Origin,
    Presentation,
    RiskLabel,
    ScoreExplanation,
    WeaverConfig,
)
from semantic_diff_weaver.obligations import _name_unrun_lines, generate_obligations
from semantic_diff_weaver.source import SourceHunk, SourceRevisionPair


def _own_lines(
    old: str, new: str, *hunks: tuple[int, int, int, int]
) -> dict[str, set[tuple[int, ...]]]:
    """Each changed symbol's `own_lines`, as its deltas carry them, for the hunks of a diff with
    `--unified=0`: each its old start and count, and its new start and count."""
    pair = SourceRevisionPair(
        path="m.py",
        old_path="m.py",
        new_path="m.py",
        hunks=tuple(
            SourceHunk(f"hunk-{index:03d}", *hunk) for index, hunk in enumerate(hunks, start=1)
        ),
        old_text=old,
        new_text=new,
    )
    found: dict[str, set[tuple[int, ...]]] = {}
    for delta in analyze_ast([pair]).deltas:
        found.setdefault(delta.symbol, set()).add(delta.own_lines)
    return found


def test_a_functions_own_lines_leave_out_what_it_holds_and_its_docstring() -> None:
    old = (
        "def outer(x):\n"
        '    """Old words."""\n'
        "    def inner(y):\n"
        "        return y + 1\n"
        "    return inner(x)\n"
    )
    new = (
        "def outer(x):\n"
        '    """New words."""\n'
        "    def inner(y):\n"
        "        return y + 2\n"
        "    return inner(x) * 2\n"
    )
    # The docstring, `inner`'s return, and `outer`'s changed.
    found = _own_lines(old, new, (2, 1, 2, 1), (4, 2, 4, 2))
    assert found["outer"] == {(5,)}
    assert found["outer.inner"] == {(4,)}


def test_a_modules_own_lines_are_its_statements_outside_every_definition() -> None:
    old = "LIMIT = 1\n\n\ndef read():\n    return LIMIT\n"
    new = "LIMIT = 2\n\n\ndef read():\n    return LIMIT + 1\n"
    found = _own_lines(old, new, (1, 1, 1, 1), (5, 1, 5, 1))
    assert found["<module>"] == {(1,)}
    assert found["read"] == {(5,)}


def test_an_added_callables_own_lines_skip_its_docstring_and_comments() -> None:
    old = "def kept():\n    return 1\n"
    new = (
        "def kept():\n"
        "    return 1\n"
        "\n"
        "\n"
        "def added(x):\n"
        '    """What it does."""\n'
        "    # Why.\n"
        "    if x:\n"
        "        return 2\n"
        "    return 3\n"
    )
    found = _own_lines(old, new, (2, 0, 3, 8))
    assert found["added"] == {(5, 8, 9, 10)}
    # `kept` did not change: the insertion beside it holds no line of its own.
    assert found.get("kept", {()}) == {()}


def test_a_removed_symbol_has_no_own_lines() -> None:
    old = "def kept():\n    return 1\n\n\ndef gone():\n    return 2\n"
    new = "def kept():\n    return 1\n"
    assert _own_lines(old, new, (3, 4, 2, 0))["gone"] == {()}


def test_the_report_names_only_lines_it_measured_and_no_test_ran() -> None:
    coverage = CoverageMap(
        source="coverage.py-json",
        files={"src/m.py": FileCoverage(covered=frozenset({1, 2}), uncovered=frozenset({3, 4}))},
    )
    assert coverage.unexecuted("src/m.py", [1, 3, 4, 9]) == frozenset({3, 4})
    # A file the report does not cover says nothing of its lines.
    assert coverage.unexecuted("src/other.py", [1, 2]) == frozenset()


def test_one_line_is_named_and_quoted() -> None:
    given = with_unrun_lines("`f` is new", [UnrunLine("m.py", 7, "raise Refused('no')")])
    assert given == "`f` is new; no test ran the changed line 7 of `m.py`: `raise Refused('no')`"


def test_runs_of_lines_are_named_by_file_and_the_rest_counted() -> None:
    lines = [
        UnrunLine("a.py", 9, "return 0"),
        UnrunLine("a.py", 3, "except OSError:"),
        UnrunLine("a.py", 4, "continue"),
        UnrunLine("a.py", 5, "pass"),
        UnrunLine("b.py", 2, "raise E"),
        # The same line twice, as two findings of one obligation name it, is named once.
        UnrunLine("b.py", 2, "raise E"),
    ]
    assert MAX_UNRUN_QUOTED_LINES == 3
    assert with_unrun_lines("G", lines) == (
        "G; no test ran the changed lines 3-5, 9 of `a.py` and 2 of `b.py`: "
        "`except OSError:`, `continue`, `pass`, and 2 more"
    )


def test_a_long_given_quotes_fewer_lines_and_at_least_names_them() -> None:
    lines = [UnrunLine("m.py", number, "x" * 70) for number in (10, 20)]
    clause = "; no test ran the changed lines 10, 20 of `m.py`"
    quote = f": `{'x' * 70}`"
    # Room for the numbers and one quote, not two: one is quoted and the other counted.
    given = "g" * (MAX_OBLIGATION_TEXT_CHARS - len(clause) - len(quote) - len(" and 1 more"))
    assert with_unrun_lines(given, lines) == f"{given}{clause}{quote} and 1 more"
    # Room for the numbers alone.
    given = "g" * (MAX_OBLIGATION_TEXT_CHARS - len(clause))
    assert with_unrun_lines(given, lines) == f"{given}{clause}"
    # Not even for them: the Given stays as it was.
    given = "g" * (MAX_OBLIGATION_TEXT_CHARS - len(clause) + 1)
    assert with_unrun_lines(given, lines) == given


def _behavior(
    index: int,
    presentation: Presentation = Presentation.FINDING,
    category: BehaviorCategory = BehaviorCategory.ERROR_HANDLING,
    risk_score: int = 70,
) -> BehaviorChange:
    return BehaviorChange(
        id=f"bc-{index:03d}",
        category=category,
        summary="Changed behavior.",
        observable_impact="An outcome appears to differ.",
        risk=RiskLabel.HIGH,
        risk_score=risk_score,
        confidence=0.4 if presentation is Presentation.REVIEW_QUESTION else 0.8,
        evidence=[Evidence(id=f"ev-{index:03d}", path="src/a.py", kind="unknown_structure")],
        assumptions=[],
        presentation=presentation,
        origin=Origin.DETERMINISTIC,
        score_explanation=ScoreExplanation(
            behavioral_impact=70,
            critical_path_weight=10,
            test_gap_weight=90,
            change_surface_weight=30,
        ),
    )


def _givens(behaviors, unrun):
    obligations, _ = generate_obligations(
        behaviors, {}, False, WeaverConfig().rules, unrun_lines=unrun
    )
    return obligations


def test_without_a_report_no_obligation_changes() -> None:
    behaviors = [_behavior(1)]
    assert _givens(behaviors, None) == _givens(behaviors, {})


def test_the_first_test_obligation_of_a_finding_names_its_lines() -> None:
    unrun = {"bc-001": (UnrunLine("src/a.py", 12, "raise E"),)}
    plain = _givens([_behavior(1)], None)
    named = _givens([_behavior(1)], unrun)
    first = next(item for item in plain if item.type is not ObligationType.REVIEW)
    changed = [
        item
        for item in named
        if item.given != next(other.given for other in plain if other.title == item.title)
    ]
    assert [item.title for item in changed] == [first.title]
    assert (
        changed[0].given
        == f"{first.given}; no test ran the changed line 12 of `src/a.py`: `raise E`"
    )


def test_a_review_question_leaves_its_lines_to_the_obligation_that_asks_for_a_test() -> None:
    """Two review questions share their review obligation, and neither names lines there."""
    unrun = {
        "bc-001": (UnrunLine("src/a.py", 12, "raise E"),),
        "bc-002": (UnrunLine("src/a.py", 30, "return"),),
    }
    questions = [_behavior(index, Presentation.REVIEW_QUESTION) for index in (1, 2)]
    named = _givens(questions, unrun)
    review = [item for item in named if item.type is ObligationType.REVIEW]
    assert [item.behavior_change_ids for item in review] == [["bc-001", "bc-002"]]
    assert all("no test ran" not in item.given for item in review)
    assert sum("no test ran" in item.given for item in named) == 1


def test_one_obligation_for_two_findings_names_each_line_once() -> None:
    """Two findings of one category share their obligations, and each names its lines there."""
    unrun = {
        "bc-001": (UnrunLine("src/a.py", 12, "raise E"),),
        "bc-002": (UnrunLine("src/a.py", 12, "raise E"), UnrunLine("src/a.py", 30, "return")),
    }
    named = _givens([_behavior(1), _behavior(2)], unrun)
    quoting = [item for item in named if "no test ran" in item.given]
    assert len(quoting) == 1
    assert quoting[0].behavior_change_ids == ["bc-001", "bc-002"]
    assert "no test ran the changed lines 12, 30 of `src/a.py`" in quoting[0].given


def test_findings_of_one_symbol_with_tests_of_their_own_name_its_lines_once() -> None:
    """meal-manager's pull request 55: a handler's contract and its failure trigger each asked for
    a test, and both named the same rollback and messages. The first obligation names them."""
    lines = (UnrunLine("src/a.py", 12, "raise E"), UnrunLine("src/a.py", 30, "return"))
    behaviors = [
        _behavior(1, category=BehaviorCategory.ERROR_HANDLING, risk_score=40),
        _behavior(2, category=BehaviorCategory.OUTPUT_CONTRACT, risk_score=80),
    ]
    named = _givens(behaviors, {"bc-001": lines, "bc-002": lines})
    quoting = [item for item in named if "no test ran" in item.given]
    assert len(quoting) == 1
    assert quoting[0].behavior_change_ids == ["bc-002"]
    assert quoting[0].priority == max(item.priority for item in named)


def test_a_finding_with_no_obligation_names_its_lines_nowhere() -> None:
    """Every finding writes one scenario at least, but a finding without one must not fail."""
    _name_unrun_lines(
        [_behavior(1)], {"bc-001": (UnrunLine("src/a.py", 12, "raise E"),)}, {}, {}, {}
    )
