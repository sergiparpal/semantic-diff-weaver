from __future__ import annotations

from dataclasses import replace

import pytest

from semantic_diff_weaver.ast_diff import StructuralDelta
from semantic_diff_weaver.models import CandidateTest, LineRange, RiskLabel, WeaverConfig
from semantic_diff_weaver.scoring import (
    confidence_score,
    obligation_priority,
    risk_label,
    score_risk,
)
from semantic_diff_weaver.semantic_candidates import build_candidates


def authorization_candidate():
    return build_candidates(
        [
            StructuralDelta(
                path="src/auth/policy.py",
                symbol="authorize",
                kind="condition_change",
                old="user.is_owner",
                new="user.is_admin",
                old_lines=LineRange(start=2, end=2),
                new_lines=LineRange(start=2, end=2),
                hunk_id="src/auth/policy.py#hunk-001",
            )
        ]
    )[0]


@pytest.mark.parametrize(
    ("score", "label"),
    [
        (0, RiskLabel.LOW),
        (29, RiskLabel.LOW),
        (30, RiskLabel.MEDIUM),
        (59, RiskLabel.MEDIUM),
        (60, RiskLabel.HIGH),
        (79, RiskLabel.HIGH),
        (80, RiskLabel.CRITICAL),
        (100, RiskLabel.CRITICAL),
    ],
)
def test_exact_risk_boundaries(score: int, label: RiskLabel) -> None:
    assert risk_label(score) is label


def test_risk_uses_exact_weighted_formula_and_static_gap() -> None:
    config = WeaverConfig.model_validate(
        {"critical_paths": [{"pattern": "src/auth/**", "weight": 100}]}
    )
    candidate = authorization_candidate()
    score, label, explanation = score_risk(candidate, [], config.critical_paths)
    expected = round(
        explanation.behavioral_impact * 0.35
        + explanation.critical_path_weight * 0.25
        + explanation.test_gap_weight * 0.25
        + explanation.change_surface_weight * 0.15
    )
    assert score == expected
    assert label is RiskLabel.CRITICAL


def test_candidate_never_removes_test_gap() -> None:
    test = CandidateTest(
        path="tests/auth/test_policy.py",
        symbol="test_authorize",
        match_score=1.0,
        match_reasons=["direct module or symbol import"],
    )
    _, _, explanation = score_risk(authorization_candidate(), [test], WeaverConfig().critical_paths)
    assert explanation.test_gap_weight > 0


def test_private_symbols_and_weak_candidates_adjust_risk_components() -> None:
    candidate = replace(authorization_candidate(), symbol="_authorize")
    weak = CandidateTest(
        path="tests/auth/test_policy.py",
        symbol="test_authorize",
        match_score=0.5,
        match_reasons=["mirrored source/test path"],
    )
    _, _, explanation = score_risk(candidate, [weak], WeaverConfig().critical_paths)
    assert explanation.behavioral_impact < 92
    assert explanation.test_gap_weight == 65


def test_confidence_is_independent_and_truncation_penalizes() -> None:
    candidate = authorization_candidate()
    assert confidence_score(candidate, truncated=True) < confidence_score(
        candidate, truncated=False
    )


def test_priority_formula_is_bounded() -> None:
    assert obligation_priority(100, 100, 100, 1.0) == 100
    assert obligation_priority(0, 0, 0, 0.0) == 0
