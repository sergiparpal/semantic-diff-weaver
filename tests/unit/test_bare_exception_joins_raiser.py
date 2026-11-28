"""A finding on an added exception class with nothing of its own joins the finding on code that
raises it.

The Action's field test on claim-ledger had two findings on such classes, `Refused(Exception)` and
`LabelError(Exception)`. Each asked to "characterize the new" class with a representative input.
Raising one tests only what it inherits. What it means is where code raises it, and the findings on
the functions that raise them said what to test.
"""

from __future__ import annotations

import pytest

from semantic_diff_weaver.ast_diff import extract_symbols
from semantic_diff_weaver.evidence_scenarios import Change
from semantic_diff_weaver.models import (
    BehaviorCategory,
    BehaviorChange,
    Evidence,
    Origin,
    Presentation,
    RiskLabel,
    ScoreExplanation,
)
from semantic_diff_weaver.service import _join_exception_to_raiser, analyze

LABELS_OLD = "def main():\n    return 0\n"
LABEL_ERROR = 'class LabelError(Exception):\n    """A label file that cannot be read."""\n'
READ_LABELS = (
    "def read_labels(text):\n"
    "    if not text:\n"
    "        raise LabelError('no labels')\n"
    "    return text.split()\n"
)


def _analysis(repo_factory, old: dict[str, str], new: dict[str, str]) -> dict:
    repo, base, head = repo_factory(old, new)
    return analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}
    )


def _symbols(analysis: dict) -> list[str]:
    return [item["evidence"][0]["symbol"] for item in analysis["behavior_changes"]]


def _titles(analysis: dict) -> list[str]:
    return [item["title"] for item in analysis["test_obligations"]]


def test_extraction_marks_an_exception_class_with_nothing_of_its_own() -> None:
    source = (
        "class Base(Exception):\n    pass\n"
        "class Specific(Base):\n    '''Raised when it is specific.'''\n"
        "class Named(ValueError):\n    ...\n"
        "class Remote(errors.RemoteError):\n    pass\n"
        "class Coded(Exception):\n    code = 2\n"
        "class Told(Exception):\n    def __str__(self):\n        return 'told'\n"
        "class Plain:\n    pass\n"
        "@final\nclass Registered(Exception):\n    pass\n"
        "class Looped(Looped):\n    pass\n"
    )
    bare = {item.qualified_name for item in extract_symbols(source) if item.bare_exception}
    assert bare == {"Base", "Specific", "Named", "Remote"}


def test_an_exception_with_nothing_of_its_own_joins_the_function_that_raises_it(
    repo_factory,
) -> None:
    analysis = _analysis(
        repo_factory,
        {"labels.py": LABELS_OLD},
        {"labels.py": f"{LABEL_ERROR}\n\n{READ_LABELS}\n\n{LABELS_OLD}"},
    )
    assert _symbols(analysis) == ["read_labels"]
    (finding,) = analysis["behavior_changes"]
    # The raiser's evidence first, the class's after it.
    assert [(item["symbol"], item["kind"]) for item in finding["evidence"]] == [
        ("read_labels", "symbol_added"),
        ("LabelError", "symbol_added"),
    ]
    # The class's own obligation went with it: the raiser's outcomes say what to test.
    assert not any("LabelError`" in title for title in _titles(analysis))
    assert all(item["behavior_change_ids"] == ["bc-001"] for item in analysis["test_obligations"])


def test_the_first_raiser_in_the_class_s_own_file_takes_it(repo_factory) -> None:
    errors_new = (
        f"{LABEL_ERROR}\n\n{READ_LABELS}\n\n"
        "def read_more(text):\n    if text is None:\n        raise LabelError('none')\n"
        "    return text\n"
    )
    client_new = (
        "from errors import LabelError\n\n\n"
        "def fetch(x):\n    if not x:\n        raise LabelError('empty')\n    return x\n"
    )
    analysis = _analysis(
        repo_factory,
        {"a_client.py": LABELS_OLD, "errors.py": LABELS_OLD},
        {"a_client.py": client_new, "errors.py": f"{errors_new}\n\n{LABELS_OLD}"},
    )
    assert "LabelError" not in _symbols(analysis)
    carrying = [
        finding["evidence"][0]["symbol"]
        for finding in analysis["behavior_changes"]
        if any(item["symbol"] == "LabelError" for item in finding["evidence"])
    ]
    assert carrying == ["read_labels"]


def test_a_raise_added_to_a_changed_function_takes_it(repo_factory) -> None:
    old = "def check(x):\n    return x\n"
    new = (
        "class Refused(Exception):\n    pass\n\n\n"
        "def check(x):\n    if not x:\n        raise Refused('x')\n    return x\n"
    )
    analysis = _analysis(repo_factory, {"ship.py": old}, {"ship.py": new})
    assert "Refused" not in _symbols(analysis)
    assert any(
        item["symbol"] == "Refused"
        for finding in analysis["behavior_changes"]
        if finding["evidence"][0]["symbol"] == "check"
        for item in finding["evidence"]
    )


@pytest.mark.parametrize(
    ("case", "added"),
    [
        (
            "a method of its own",
            "class LabelError(Exception):\n    def __str__(self):\n        return 'label'\n",
        ),
        ("a class attribute", "class LabelError(Exception):\n    code = 2\n"),
        ("a decorator", "@final\nclass LabelError(Exception):\n    pass\n"),
        ("no exception among its bases", "class LabelError:\n    pass\n"),
    ],
)
def test_a_class_with_something_of_its_own_keeps_its_finding(
    repo_factory, case: str, added: str
) -> None:
    analysis = _analysis(
        repo_factory,
        {"labels.py": LABELS_OLD},
        {"labels.py": f"{added}\n\n{READ_LABELS}\n\n{LABELS_OLD}"},
    )
    assert "LabelError" in _symbols(analysis), case


def test_an_exception_no_finding_raises_keeps_its_finding(repo_factory) -> None:
    analysis = _analysis(
        repo_factory, {"labels.py": LABELS_OLD}, {"labels.py": f"{LABEL_ERROR}\n\n{LABELS_OLD}"}
    )
    assert _symbols(analysis) == ["LabelError"]


def test_a_removed_exception_keeps_its_finding(repo_factory) -> None:
    analysis = _analysis(
        repo_factory,
        {"labels.py": f"{LABEL_ERROR}\n\n{READ_LABELS}\n\n{LABELS_OLD}"},
        {"labels.py": LABELS_OLD},
    )
    assert "LabelError" in _symbols(analysis)


def _behavior(identifier: str, symbol: str, presentation: Presentation) -> BehaviorChange:
    return BehaviorChange(
        id=identifier,
        category=BehaviorCategory.UNKNOWN,
        summary="A callable or structural symbol was added.",
        observable_impact="New behavior becomes reachable.",
        risk=RiskLabel.MEDIUM,
        risk_score=50,
        confidence=0.69,
        evidence=[
            Evidence(
                id=f"ev-{identifier[3:]}",
                path="labels.py",
                symbol=symbol,
                kind="symbol_added",
                new_lines={"start": 1, "end": 2},
            )
        ],
        presentation=presentation,
        origin=Origin.DETERMINISTIC,
        score_explanation=ScoreExplanation(
            behavioral_impact=50,
            critical_path_weight=0,
            test_gap_weight=90,
            change_surface_weight=10,
        ),
    )


def test_a_review_question_on_an_exception_stays() -> None:
    behaviors = [
        _behavior("bc-001", "LabelError", Presentation.REVIEW_QUESTION),
        _behavior("bc-002", "read_labels", Presentation.FINDING),
    ]
    changes = {
        "ev-001": Change(kind="symbol_added", bare_exception=True),
        "ev-002": Change(kind="symbol_added", exits=(("raise", "LabelError('no labels')", ()),)),
    }
    kept, _, _ = _join_exception_to_raiser(behaviors, [], [], changes)
    assert [item.id for item in kept] == ["bc-001", "bc-002"]
