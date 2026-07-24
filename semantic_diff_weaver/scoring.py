"""Independent risk, confidence, and obligation-priority scoring."""

from __future__ import annotations

from .models import (
    BehaviorCategory,
    CandidateTest,
    Origin,
    RiskLabel,
    ScoreExplanation,
    WeaverConfig,
)
from .path_policy import critical_weight
from .semantic_candidates import SemanticCandidate

IMPACT = {
    BehaviorCategory.AUTHORIZATION: 92,
    BehaviorCategory.SIDE_EFFECT: 84,
    BehaviorCategory.STATE_TRANSITION: 78,
    BehaviorCategory.ERROR_HANDLING: 72,
    BehaviorCategory.OUTPUT_CONTRACT: 70,
    BehaviorCategory.RETRY_TIMEOUT: 68,
    BehaviorCategory.VALIDATION: 65,
    BehaviorCategory.BOUNDARY: 64,
    BehaviorCategory.DEPENDENCY_INTERACTION: 62,
    BehaviorCategory.ORDERING: 60,
    BehaviorCategory.DEFAULT_BEHAVIOR: 58,
    BehaviorCategory.UNKNOWN: 55,
    BehaviorCategory.REFACTOR: 12,
}


def risk_label(score: int | float) -> RiskLabel:
    if score >= 80:
        return RiskLabel.CRITICAL
    if score >= 60:
        return RiskLabel.HIGH
    if score >= 30:
        return RiskLabel.MEDIUM
    return RiskLabel.LOW


def confidence_score(candidate: SemanticCandidate, *, truncated: bool = False) -> float:
    parser = sum(item.parser_complete for item in candidate.evidence) / len(candidate.evidence)
    context = 0.75 if not candidate.assumptions else 0.62
    agreement = 0.95 if candidate.origin is Origin.LLM_SUPPORTED else 0.85
    observability = (
        0.90
        if candidate.category
        in {
            BehaviorCategory.BOUNDARY,
            BehaviorCategory.DEFAULT_BEHAVIOR,
            BehaviorCategory.ERROR_HANDLING,
            BehaviorCategory.OUTPUT_CONTRACT,
            BehaviorCategory.STATE_TRANSITION,
        }
        else 0.70
    )
    assumption_penalty = min(0.25, len(candidate.assumptions) * 0.06)
    truncation_penalty = 0.12 if truncated else 0.0
    value = (
        candidate.confidence_baseline * 0.30
        + parser * 0.20
        + context * 0.15
        + agreement * 0.25
        + observability * 0.10
        - assumption_penalty
        - truncation_penalty
    )
    return round(max(0.0, min(1.0, value)), 3)


def score_risk(
    candidate: SemanticCandidate,
    candidate_tests: list[CandidateTest],
    config: WeaverConfig,
) -> tuple[int, RiskLabel, ScoreExplanation]:
    impact = IMPACT[candidate.category]
    if candidate.symbol.rsplit(".", 1)[-1].startswith("_"):
        impact = max(0, impact - 8)
    critical = critical_weight(candidate.path, config.critical_paths, default=10)
    if not candidate_tests:
        test_gap = 90
    elif max(item.match_score for item in candidate_tests) < 0.60:
        test_gap = 65
    else:
        test_gap = 45
    surface = min(
        100,
        20
        + len(candidate.evidence) * 10
        + (20 if any(item.kind == "signature_change" for item in candidate.evidence) else 0)
        + (
            20
            if candidate.category
            in {BehaviorCategory.SIDE_EFFECT, BehaviorCategory.DEPENDENCY_INTERACTION}
            else 0
        )
        + (20 if len(candidate.related_paths) > 1 else 0),
    )
    explanation = ScoreExplanation(
        behavioral_impact=impact,
        critical_path_weight=critical,
        test_gap_weight=test_gap,
        change_surface_weight=surface,
    )
    score = round(impact * 0.35 + critical * 0.25 + test_gap * 0.25 + surface * 0.15)
    return score, risk_label(score), explanation


def obligation_priority(
    risk_score: int, scenario_relevance: int, test_gap: int, confidence: float
) -> int:
    return round(
        max(
            0,
            min(
                100,
                risk_score * 0.60
                + scenario_relevance * 0.20
                + test_gap * 0.15
                + confidence * 100 * 0.05,
            ),
        )
    )
