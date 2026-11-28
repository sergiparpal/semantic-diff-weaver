"""Findings of one symbol that share their test obligations are one card in the brief, and so are
the findings of a change and of each function around it that changed only through it.

Two categories of one change can write the same scenario. The Action's field test listed a changed
`except` as a dependency interaction and again as an error handling change, each with the same
obligation, and a reader met the same test twice under two names. It also listed a decorator's
changed `runner` and the factory around it, which shares its obligation, as two cards with one
test. The analysis keeps each finding, so its categories and the benchmark's measures stay as they
were. The brief names them once, with every category, and the obligations list still covers each
one.
"""

from __future__ import annotations

import pytest

from semantic_diff_weaver.models import (
    AnalysisResult,
    BehaviorCategory,
    Evidence,
    Presentation,
    RiskLabel,
)
from semantic_diff_weaver.renderer import render_markdown
from semantic_diff_weaver.service import analyze


def _result(repo_factory) -> AnalysisResult:
    repo, base, head = repo_factory(
        {"src/api.py": "def check(x):\n    return x < 1\n"},
        {"src/api.py": "def check(x):\n    return x <= 1\n"},
    )
    envelope = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}
    )
    result = AnalysisResult.model_validate(envelope)
    assert len(result.behavior_changes) == 1
    assert len(result.test_obligations) > 1
    return result


def _with_twin(result: AnalysisResult, *, covered: str = "all", **update) -> AnalysisResult:
    """The result with a second finding on the first one's symbol, in another category, covered by
    every obligation, by the first one alone, by one of its own, or by every obligation and one of
    its own."""
    first = result.behavior_changes[0]
    twin = first.model_copy(
        update={"id": "bc-002", "category": BehaviorCategory.OUTPUT_CONTRACT, **update}
    )
    obligations = []
    for index, obligation in enumerate(result.test_obligations):
        ids = list(obligation.behavior_change_ids)
        if covered in {"all", "more"} or (covered == "one" and index == 0):
            ids.append("bc-002")
        obligations.append(obligation.model_copy(update={"behavior_change_ids": ids}))
    if covered in {"own", "more"}:
        own = result.test_obligations[0].model_copy(
            update={"id": "to-099", "behavior_change_ids": ["bc-002"], "title": "Its own test"}
        )
        obligations.append(own)
    return result.model_copy(
        update={"behavior_changes": [first, twin], "test_obligations": obligations}
    )


def _cards(markdown: str) -> list[str]:
    section = markdown.split("### Inferred behavior changes")[1].split("### ")[0]
    return [line for line in section.splitlines() if line.startswith("- **")]


def test_findings_of_one_symbol_with_the_same_obligations_are_one_card(repo_factory) -> None:
    markdown = render_markdown(_with_twin(_result(repo_factory)))
    (card,) = _cards(markdown)
    assert card.startswith(
        "- **Findings `bc-001`, `bc-002` · boundary\\_change, output\\_contract\\_change · "
    )
    assert "One change of one symbol, read as each category" in markdown
    # Its evidence, which both findings cite, is shown once.
    assert markdown.count("  - Evidence `ev-001`:") == 1
    # The obligations still name each finding they cover.
    assert "covers `bc-001`, `bc-002`)" in markdown


def test_a_finding_whose_obligations_the_other_holds_joins_its_card(repo_factory) -> None:
    (card,) = _cards(render_markdown(_with_twin(_result(repo_factory), covered="one")))
    assert card.startswith("- **Findings `bc-001`, `bc-002` · ")


def test_a_finding_with_an_obligation_of_its_own_keeps_its_card(repo_factory) -> None:
    cards = _cards(render_markdown(_with_twin(_result(repo_factory), covered="own")))
    assert [card.split(" · ")[0] for card in cards] == [
        "- **Finding `bc-001`",
        "- **Finding `bc-002`",
    ]


def test_another_symbol_s_finding_keeps_its_card(repo_factory) -> None:
    result = _result(repo_factory)
    elsewhere = (
        result.behavior_changes[0]
        .evidence[0]
        .model_copy(update={"id": "ev-002", "symbol": "other"})
    )
    cards = _cards(render_markdown(_with_twin(result, evidence=[elsewhere])))
    assert len(cards) == 2


def test_a_review_question_keeps_its_card(repo_factory) -> None:
    result = _with_twin(_result(repo_factory), presentation=Presentation.REVIEW_QUESTION)
    cards = _cards(render_markdown(result))
    assert cards[0].startswith("- **Finding `bc-001` · ")
    assert cards[1].startswith("- **Review question `bc-002` · ")


def test_the_card_carries_the_riskiest_finding_s_risk_and_every_summary(repo_factory) -> None:
    result = _with_twin(
        _result(repo_factory),
        risk=RiskLabel.HIGH,
        risk_score=90,
        confidence=0.5,
        summary="The value it returns changed.",
        evidence=[
            Evidence(id="ev-002", path="src/api.py", symbol="check", kind="return_change"),
        ],
    )
    markdown = render_markdown(result)
    (card,) = _cards(markdown)
    assert " · high risk · 50% confidence:** " in card
    assert card.endswith(" The value it returns changed\\.")
    # The evidence of each finding is shown, once each.
    assert markdown.count("  - Evidence `ev-001`:") == 1
    assert markdown.count("  - Evidence `ev-002`:") == 1


def _around(result: AnalysisResult, symbol: str = "check.inner", **twin) -> AnalysisResult:
    """The result with its finding moved to `symbol`, beside a second finding of `check` that
    changed only as the code nested in it did, as `SDW-RULE-NESTED` reads a function."""
    first = result.behavior_changes[0]
    moved = first.model_copy(
        update={"evidence": [first.evidence[0].model_copy(update={"symbol": symbol})]}
    )
    around = Evidence(
        id="ev-002",
        path="src/api.py",
        symbol="check",
        kind="unknown_structure",
        old="body changed",
        new="body changed",
    )
    return _with_twin(
        result.model_copy(update={"behavior_changes": [moved]}),
        **{"evidence": [around], **twin},
    )


@pytest.mark.parametrize("covered", ["all", "one"])
def test_a_function_that_changed_only_through_a_nested_one_joins_its_card(
    repo_factory, covered: str
) -> None:
    """Its obligations are all, or some, of the nested function's."""
    markdown = render_markdown(_around(_result(repo_factory), covered=covered))
    (card,) = _cards(markdown)
    assert card.startswith("- **Findings `bc-001`, `bc-002` · ")
    assert "One change, and each function around it that changed only through it" in markdown
    assert "One change of one symbol" not in markdown
    assert markdown.count("  - Evidence `ev-001`:") == 1
    assert markdown.count("  - Evidence `ev-002`:") == 1


CHANGED_CALL = Evidence(id="ev-002", path="src/api.py", symbol="check", kind="call_change")


@pytest.mark.parametrize(
    ("case", "symbol", "twin"),
    [
        ("an obligation of its own", "check.inner", {"covered": "own"}),
        ("the nested one's and one of its own", "check.inner", {"covered": "more"}),
        ("code of its own that changed", "check.inner", {"evidence": [CHANGED_CALL]}),
        ("a function named alike, not nested", "checker", {}),
    ],
)
def test_a_function_around_keeps_its_card_where_it_says_more(
    repo_factory, case: str, symbol: str, twin: dict
) -> None:
    """With an obligation of its own, a delta that names what its own code changed, or another
    function's name, the finding of `check` says something the nested one's card does not."""
    cards = _cards(render_markdown(_around(_result(repo_factory), symbol, **twin)))
    assert len(cards) == 2, case


def test_a_decorator_factory_whose_docstring_changed_with_its_wrapper_is_one_card(
    repo_factory,
) -> None:
    """The factory's docstring changed beside the wrapper's condition: the factory shares the
    wrapper's obligation instead of quoting its docstring, and the brief says the change once."""
    old = (
        "def guard(fn):\n"
        '    """Wrap fn: calls with small inputs return 0."""\n'
        "    def run(x):\n"
        "        if x > 1:\n"
        "            return fn(x)\n"
        "        return 0\n"
        "    return run\n"
    )
    new = old.replace("small inputs", "inputs of 1 or less").replace("x > 1", "x >= 1")
    repo, base, head = repo_factory({"src/api.py": old}, {"src/api.py": new})
    envelope = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}
    )
    result = AnalysisResult.model_validate(envelope)
    assert {item.evidence[0].symbol for item in result.behavior_changes} == {"guard", "guard.run"}
    assert not any("small inputs" in item.given for item in result.test_obligations)
    markdown = render_markdown(result)
    (card,) = _cards(markdown)
    assert card.startswith("- **Findings `bc-001`, `bc-002` · ")
    assert "One change, and each function around it that changed only through it" in markdown


def test_the_analysis_keeps_each_finding(repo_factory) -> None:
    result = _with_twin(_result(repo_factory))
    assert [item.id for item in result.behavior_changes] == ["bc-001", "bc-002"]
    render_markdown(result)
    assert [item.id for item in result.behavior_changes] == ["bc-001", "bc-002"]
