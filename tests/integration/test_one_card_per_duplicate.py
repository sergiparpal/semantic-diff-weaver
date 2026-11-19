"""Findings of one symbol that share their test obligations are one card in the brief.

Two categories of one change can write the same scenario. The Action's field test listed a changed
`except` as a dependency interaction and again as an error handling change, each with the same
obligation, and a reader met the same test twice under two names. The analysis keeps each
finding, so its categories and the benchmark's measures stay as they were. The brief names them
once, with every category, and the obligations list still covers each one.
"""

from __future__ import annotations

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
    every obligation, by the first one alone, or by one of its own."""
    first = result.behavior_changes[0]
    twin = first.model_copy(
        update={"id": "bc-002", "category": BehaviorCategory.OUTPUT_CONTRACT, **update}
    )
    obligations = []
    for index, obligation in enumerate(result.test_obligations):
        ids = list(obligation.behavior_change_ids)
        if covered == "all" or (covered == "one" and index == 0):
            ids.append("bc-002")
        obligations.append(obligation.model_copy(update={"behavior_change_ids": ids}))
    if covered == "own":
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


def test_the_analysis_keeps_each_finding(repo_factory) -> None:
    result = _with_twin(_result(repo_factory))
    assert [item.id for item in result.behavior_changes] == ["bc-001", "bc-002"]
    render_markdown(result)
    assert [item.id for item in result.behavior_changes] == ["bc-001", "bc-002"]
