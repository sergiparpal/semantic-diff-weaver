"""Independent risk, confidence, and obligation-priority scoring."""

from __future__ import annotations

from collections.abc import Sequence

from .models import (
    RISK_ORDER,
    BehaviorCategory,
    CandidateTest,
    Origin,
    RiskLabel,
    ScoreExplanation,
    Verdict,
    VerdictConfig,
    VerdictThreshold,
)
from .path_policy import WeightedPattern, critical_weight
from .semantic_candidates import SemanticCandidate
from .taxonomy import CATEGORY_PROFILES

# How a behavior's four risk axes combine into its 0-100 score, and where each label starts.
# Named because the Claude Code skill states them, so a model can explain a score without the
# tool's source; `tests/contract/test_claude_code_plugin.py` keeps the two in step.
IMPACT_WEIGHT = 0.35
CRITICAL_PATH_WEIGHT = 0.25
TEST_GAP_WEIGHT = 0.25
CHANGE_SURFACE_WEIGHT = 0.15
CRITICAL_FROM = 80
HIGH_FROM = 60
MEDIUM_FROM = 30


def risk_label(score: int | float) -> RiskLabel:
    if score >= CRITICAL_FROM:
        return RiskLabel.CRITICAL
    if score >= HIGH_FROM:
        return RiskLabel.HIGH
    if score >= MEDIUM_FROM:
        return RiskLabel.MEDIUM
    return RiskLabel.LOW


def _reaches(risk: RiskLabel, threshold: VerdictThreshold) -> bool:
    return threshold != "none" and RISK_ORDER.index(risk) >= RISK_ORDER.index(RiskLabel(threshold))


def advisory_verdict(
    overall_risk: RiskLabel, policy: VerdictConfig, *, incomplete: bool
) -> Verdict:
    """The verdict for an analysis's overall risk under the configured thresholds.

    `incomplete` is an analysis that left scope unread or compared a commit with itself: it is at
    least `warn`, so no unread file and no uncommitted change can read as a passed review.
    """
    if _reaches(overall_risk, policy.review_required_at):
        return Verdict.REVIEW_REQUIRED
    if incomplete or _reaches(overall_risk, policy.warn_at):
        return Verdict.WARN
    return Verdict.PASS


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


# Grounded-coverage adjustments to the static test-gap axis. Deliberately small: an
# ingested report says a changed *line* was executed, which is real evidence but is weaker
# than a test that asserts the changed behavior. Uncovered widens the gap, covered narrows
# it, and an unmatched file leaves the static estimate alone.
COVERAGE_GAP_ADJUSTMENT = {"uncovered": 10, "covered": -15, "unknown": 0}


def score_risk(
    candidate: SemanticCandidate,
    candidate_tests: list[CandidateTest],
    critical_paths: Sequence[WeightedPattern],
    coverage_state: str | None = None,
) -> tuple[int, RiskLabel, ScoreExplanation]:
    """Score one candidate's risk from its own evidence and the configured critical paths.

    The critical-path axis defaults to 10 for an unmatched path, so an explicit ``weight: 0``
    entry scores *below* leaving a path unconfigured. That asymmetry is deliberate — listing a
    path at zero is a statement that it is not critical, which is stronger than saying nothing.

    ``coverage_state`` is the grounded result from an ingested coverage report, when one was
    supplied. It adjusts the test-gap axis only, so an uncovered change on a critical path
    ranks above a covered one without letting coverage override behavioral impact.
    """
    impact = CATEGORY_PROFILES[candidate.category].impact
    if candidate.symbol.rsplit(".", 1)[-1].startswith("_"):
        impact = max(0, impact - 8)
    critical = critical_weight(candidate.path, critical_paths, default=10)
    if not candidate_tests:
        test_gap = 90
    elif max(item.match_score for item in candidate_tests) < 0.60:
        test_gap = 65
    else:
        test_gap = 45
    test_gap = max(0, min(100, test_gap + COVERAGE_GAP_ADJUSTMENT.get(coverage_state or "", 0)))
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
    score = round(
        impact * IMPACT_WEIGHT
        + critical * CRITICAL_PATH_WEIGHT
        + test_gap * TEST_GAP_WEIGHT
        + surface * CHANGE_SURFACE_WEIGHT
    )
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
