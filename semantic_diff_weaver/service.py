"""Typed orchestration for the bounded read-only semantic diff pipeline."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from .ast_diff import AstAnalysis, StructuralDelta, analyze_ast
from .config import load_config
from .coverage_map import CoverageMap, load_coverage, summarize
from .errors import ErrorCode, WeaverError
from .git_diff import DiffCollection, GitRepository, collect_diff
from .llm_client import LlmClient
from .models import (
    AnalysisResult,
    AnalyzeRequest,
    BehaviorCategory,
    BehaviorChange,
    CandidateTest,
    CoverageSummary,
    LineRange,
    LlmStatus,
    OmittedScope,
    Origin,
    Presentation,
    RepositoryIdentity,
    RiskLabel,
    ScopeMetadata,
    Summary,
    TestObligation,
    WeaverConfig,
)
from .obligations import generate_obligations
from .path_policy import (
    critical_weight,
    ensure_authorized_path,
    exclusion_reason,
    glob_matches,
    redact_text,
)
from .renderer import render_transport
from .scoring import confidence_score, score_risk
from .semantic_candidates import SemanticCandidate, build_candidates
from .semantic_interpreter import InterpreterResult, interpret_candidates
from .test_mapper import TestIndex, build_test_index, map_candidate_tests
from .textutil import canonical_phrase

HIGH_IMPACT_DELTA_KINDS = {
    "signature_change",
    "comparison_change",
    "raise_change",
    "call_change",
}
README_NAMES = frozenset({"readme", "readme.md", "readme.rst", "readme.txt"})
INCOMPLETE_EXCLUSION_REASONS = (
    "aggregate_source_limit",
    "binary",
    "oversized_or_non_utf8",
    "symlink_or_gitlink",
)


def _validation_error(exc: ValidationError) -> WeaverError:
    location = ".".join(str(item) for item in exc.errors()[0].get("loc", ())) or "request"
    return WeaverError(
        ErrorCode.CONFIGURATION_ERROR,
        f"Invalid tool argument at {location}.",
        "Check required fields, output_format, include/exclude patterns, and unknown fields.",
    )


def _prioritize_deltas(
    deltas: list[StructuralDelta], maximum: int, config: WeaverConfig
) -> tuple[list[StructuralDelta], int]:
    grouped: dict[tuple[str, str], list[StructuralDelta]] = defaultdict(list)
    for delta in deltas:
        grouped[(delta.path, delta.symbol)].append(delta)
    ordered = sorted(
        grouped.items(),
        key=lambda item: (
            -critical_weight(item[0][0], config.critical_paths),
            -sum(delta.kind in HIGH_IMPACT_DELTA_KINDS for delta in item[1]),
            item[0],
        ),
    )
    selected = ordered[:maximum]
    return [delta for _, items in selected for delta in items], max(0, len(ordered) - maximum)


def _read_readme_excerpt(repo: GitRepository, head_commit: str, config: WeaverConfig) -> str | None:
    if config.rules.max_readme_chars == 0:
        return None
    try:
        repository_files = repo.list_files(head_commit)
    except WeaverError:
        return None
    readme = next(
        (
            path
            for path in repository_files
            # Repository-root only, so the path is already its own basename.
            if "/" not in path
            and path.casefold() in README_NAMES
            and not exclusion_reason(path)
            and not any(glob_matches(path, pattern) for pattern in config.paths.exclude)
        ),
        None,
    )
    if readme is None:
        return None
    source = repo.read_blob(
        head_commit,
        readme,
        min(config.rules.max_file_bytes, max(4096, config.rules.max_readme_chars * 4)),
    )
    if source is None:
        return None
    return redact_text(source, max_chars=config.rules.max_readme_chars)


@dataclass
class _DedupEntry:
    """A retained candidate's slot plus the derived keys the merge scan would otherwise recompute.

    Holds the output index rather than the candidate itself, because candidates are immutable
    and merging therefore replaces the retained entry instead of editing it.
    """

    index: int
    evidence_ids: set[str]
    phrase: str


def _merge_duplicate(existing: SemanticCandidate, incoming: SemanticCandidate) -> SemanticCandidate:
    """Fold a duplicate into the retained candidate, keeping the retained ``(path, symbol)``."""
    evidence_by_id = {item.id: item for item in existing.evidence}
    for evidence in incoming.evidence:
        evidence_by_id.setdefault(evidence.id, evidence)
    stronger = incoming.confidence_baseline > existing.confidence_baseline
    return replace(
        existing,
        summary=incoming.summary if stronger else existing.summary,
        observable_impact=incoming.observable_impact if stronger else existing.observable_impact,
        confidence_baseline=max(existing.confidence_baseline, incoming.confidence_baseline),
        evidence=tuple(evidence_by_id[key] for key in sorted(evidence_by_id)),
        assumptions=tuple(sorted({*existing.assumptions, *incoming.assumptions})),
        rule_ids=tuple(sorted({*existing.rule_ids, *incoming.rule_ids})),
        related_calls=existing.related_calls | incoming.related_calls,
        related_paths=existing.related_paths | incoming.related_paths,
        origin=(
            Origin.LLM_SUPPORTED if incoming.origin is Origin.LLM_SUPPORTED else existing.origin
        ),
    )


def _deduplicate_candidates(candidates: list[SemanticCandidate]) -> list[SemanticCandidate]:
    output: list[SemanticCandidate] = []
    # Bucketing by identity keeps the scan to same-symbol peers, and each entry carries its
    # evidence set and normalized phrase so neither is rebuilt per comparison.
    buckets: dict[tuple[str, str, BehaviorCategory], list[_DedupEntry]] = defaultdict(list)
    for candidate in candidates:
        candidate_evidence = {item.id for item in candidate.evidence}
        candidate_phrase = canonical_phrase(candidate.observable_impact)
        bucket = buckets[(candidate.path, candidate.symbol, candidate.category)]
        entry = next(
            (
                item
                for item in bucket
                if candidate_evidence & item.evidence_ids or item.phrase == candidate_phrase
            ),
            None,
        )
        if entry is None:
            bucket.append(_DedupEntry(len(output), candidate_evidence, candidate_phrase))
            output.append(candidate)
            continue
        existing = output[entry.index]
        entry.evidence_ids |= candidate_evidence
        if candidate.confidence_baseline > existing.confidence_baseline:
            entry.phrase = candidate_phrase
        output[entry.index] = _merge_duplicate(existing, candidate)
    return output


def _bootstrap(
    arguments: dict[str, Any],
) -> tuple[AnalyzeRequest, GitRepository, str, str, WeaverConfig, list[str]]:
    try:
        request = AnalyzeRequest.model_validate(arguments)
    except ValidationError as exc:
        raise _validation_error(exc) from exc
    authorized_request_path = ensure_authorized_path(Path(request.repo_path))
    repo = GitRepository.open(str(authorized_request_path))
    ensure_authorized_path(repo.root)
    base_commit = repo.resolve_ref(request.base_ref)
    head_commit = repo.resolve_ref(request.head_ref)
    config, config_warnings = load_config(repo.root, request)
    return request, repo, base_commit, head_commit, config, config_warnings


@dataclass
class _PipelineState:
    """Cross-stage accounting every stage contributes to.

    Scope omissions, warnings, and the two truncation flags used to be seven separate values
    threaded through six-element return tuples. Collecting them here means a stage records what
    it dropped at the point it drops it, and adding a new omission reason touches one call site.
    """

    omitted: list[OmittedScope] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    scope_truncated: bool = False
    confidence_truncated: bool = False

    def record_omitted(
        self, reason: str, count: int, *, scope: bool = False, confidence: bool = False
    ) -> None:
        """Record a non-zero omission and, when asked, the truncation it implies."""
        if not count:
            return
        self.omitted.append(OmittedScope(reason=reason, count=count))
        self.scope_truncated |= scope
        self.confidence_truncated |= confidence


@dataclass(frozen=True)
class _ScoredCandidate:
    """A reportable candidate carried together with everything later stages derived for it.

    Replaces two parallel dictionaries that were keyed differently — one by ``id(candidate)``
    and one by list position — and could disagree the moment a candidate was copied.
    """

    candidate: SemanticCandidate
    confidence: float
    tests: list[CandidateTest] = field(default_factory=list)


def _collect_scope(
    collection: DiffCollection,
    ast_result: AstAnalysis,
    config: WeaverConfig,
    state: _PipelineState,
) -> tuple[list[StructuralDelta], int, int]:
    incomplete_exclusions = sum(
        collection.excluded_counts.get(reason, 0) for reason in INCOMPLETE_EXCLUSION_REASONS
    )
    state.omitted.extend(
        OmittedScope(reason=reason, count=count)
        for reason, count in sorted(collection.omitted_counts.items())
        if count
    )
    truncated_input = collection.truncated or bool(incomplete_exclusions)
    state.scope_truncated |= truncated_input
    state.confidence_truncated |= truncated_input
    state.record_omitted(
        "parse_incomplete_files",
        ast_result.failed_files - ast_result.resource_limited_files,
        scope=True,
    )
    state.record_omitted("ast_resource_limit", ast_result.resource_limited_files, scope=True)
    deltas = ast_result.deltas
    changed_symbols = ast_result.changed_symbols
    if changed_symbols > config.rules.max_changed_symbols:
        deltas, omitted_count = _prioritize_deltas(deltas, config.rules.max_changed_symbols, config)
        # Reaching the cap is itself the truncation, independently of how many symbol groups
        # the prioritization happened to shed, so the flags are not conditional on the count.
        state.scope_truncated = True
        state.confidence_truncated = True
        state.record_omitted("changed_symbol_limit", omitted_count)
        changed_symbols = config.rules.max_changed_symbols
    return deltas, changed_symbols, incomplete_exclusions


def _filter_reportable(
    candidates: list[SemanticCandidate],
    config: WeaverConfig,
    state: _PipelineState,
) -> tuple[list[_ScoredCandidate], int]:
    reportable: list[_ScoredCandidate] = []
    low_confidence_omitted = 0
    refactors_omitted = 0
    for candidate in candidates:
        if (
            candidate.category is BehaviorCategory.REFACTOR
            and not config.rules.emit_low_risk_refactors
        ):
            refactors_omitted += 1
            continue
        confidence = confidence_score(candidate, truncated=state.confidence_truncated)
        if confidence < config.rules.minimum_report_confidence:
            low_confidence_omitted += 1
            continue
        reportable.append(_ScoredCandidate(candidate=candidate, confidence=confidence))
    if low_confidence_omitted:
        state.warnings.append(
            f"Moved {low_confidence_omitted} finding(s) below the minimum confidence into limitations."
        )
        state.record_omitted("minimum_confidence", low_confidence_omitted)
    state.record_omitted("low_risk_refactor_policy", refactors_omitted)
    return reportable, low_confidence_omitted


def _load_coverage(config: WeaverConfig, state: _PipelineState) -> CoverageMap | None:
    """Ingest the configured coverage report, if any.

    The report is untrusted input data: bounded, parsed with the standard library, and never
    executed. An unreadable report is a hard `COVERAGE_UNREADABLE` error rather than a silent
    downgrade — a reviewer who asked for grounded coverage should not be handed ungrounded
    output that looks identical.
    """
    report_path = config.coverage.report_path
    if not report_path:
        return None
    return load_coverage(ensure_authorized_path(Path(report_path)), config)


def _coverage_ranges(candidate: SemanticCandidate) -> list[tuple[str, LineRange]]:
    """The changed line ranges a finding rests on, on the head side."""
    return [
        (item.path, item.new_lines) for item in candidate.evidence if item.new_lines is not None
    ]


def _candidate_coverage_state(coverage: CoverageMap, candidate: SemanticCandidate) -> str:
    """Grounded verdict for one finding: unanimous or nothing.

    Any disagreement, or any range the report does not cover, yields `unknown`, which leaves
    the existing static semantics in place.
    """
    states = {coverage.status_for(path, span) for path, span in _coverage_ranges(candidate)}
    if states == {"covered"}:
        return "covered"
    if states == {"uncovered"}:
        return "uncovered"
    return "unknown"


def _materialize_behaviors(
    reportable: list[_ScoredCandidate],
    config: WeaverConfig,
    *,
    fallback_mode: bool,
    partial_fallback: bool,
    coverage: CoverageMap | None = None,
) -> tuple[list[BehaviorChange], dict[str, list[CandidateTest]], dict[str, str]]:
    behaviors: list[BehaviorChange] = []
    tests_by_behavior: dict[str, list[CandidateTest]] = {}
    coverage_states: dict[str, str] = {}
    for index, scored in enumerate(reportable, start=1):
        candidate = scored.candidate
        coverage_state = (
            _candidate_coverage_state(coverage, candidate) if coverage is not None else None
        )
        risk_score, risk, explanation = score_risk(
            candidate, scored.tests, config.critical_paths, coverage_state
        )
        presentation = (
            Presentation.REVIEW_QUESTION
            if risk in {RiskLabel.HIGH, RiskLabel.CRITICAL}
            and scored.confidence < config.rules.review_question_confidence
            else Presentation.FINDING
        )
        origin = candidate.origin
        if (fallback_mode or partial_fallback) and origin is Origin.DETERMINISTIC:
            origin = Origin.DETERMINISTIC_FALLBACK
        behavior = BehaviorChange(
            id=f"bc-{index:03d}",
            category=candidate.category,
            summary=candidate.summary,
            observable_impact=candidate.observable_impact,
            risk=risk,
            risk_score=risk_score,
            confidence=scored.confidence,
            evidence=list(candidate.evidence),
            assumptions=list(candidate.assumptions),
            presentation=presentation,
            origin=origin,
            score_explanation=explanation,
        )
        behaviors.append(behavior)
        tests_by_behavior[behavior.id] = scored.tests
        if coverage_state is not None:
            coverage_states[behavior.id] = coverage_state
    return behaviors, tests_by_behavior, coverage_states


def _summary_metrics(
    behaviors: list[BehaviorChange],
    obligations: list[TestObligation],
    *,
    scope_truncated: bool,
    failed_files: int,
) -> tuple[RiskLabel, int, float, dict[RiskLabel, int]]:
    if behaviors:
        highest = max(behaviors, key=lambda item: item.risk_score)
        overall_risk = highest.risk
        overall_score = highest.risk_score
        obligation_weights = Counter(
            behavior_id
            for obligation in obligations
            for behavior_id in obligation.behavior_change_ids
        )
        total_weight = sum(max(1, obligation_weights[item.id]) for item in behaviors)
        overall_confidence = round(
            sum(item.confidence * max(1, obligation_weights[item.id]) for item in behaviors)
            / total_weight,
            3,
        )
    else:
        overall_risk = RiskLabel.LOW
        overall_score = 0
        overall_confidence = 0.0 if scope_truncated or failed_files else 1.0
    risk_counts = {label: 0 for label in RiskLabel}
    for behavior in behaviors:
        risk_counts[behavior.risk] += 1
    return overall_risk, overall_score, overall_confidence, risk_counts


def _build_limitations(
    *,
    collection: DiffCollection,
    ast_result: AstAnalysis,
    incomplete_exclusions: int,
    low_confidence_omitted: int,
    interpreted: InterpreterResult,
    fallback_mode: bool,
    partial_fallback: bool,
    deterministic: list[SemanticCandidate],
) -> list[str]:
    limitations = [
        "Candidate test mapping is static and does not prove runtime coverage.",
        "Only committed Python source at the resolved refs was inspected.",
        "Repository code and tests were not imported, executed, built, installed, or modified.",
    ]
    rules: list[tuple[Callable[[], bool], str]] = [
        (
            lambda: not collection.files,
            "The bounded diff contained no included changed Python source.",
        ),
        (
            lambda: bool(collection.files) and not ast_result.deltas,
            "The included Python change contained no reportable behavior-bearing structural delta.",
        ),
        (
            lambda: bool(low_confidence_omitted),
            f"{low_confidence_omitted} low-confidence finding(s) were not presented as facts.",
        ),
        (
            lambda: bool(ast_result.failed_files),
            f"{ast_result.failed_files} changed Python file(s) had incomplete parser context.",
        ),
        (
            lambda: bool(ast_result.resource_limited_files),
            f"{ast_result.resource_limited_files} changed Python file(s) exceeded immutable AST "
            "safety budgets.",
        ),
        (
            lambda: bool(incomplete_exclusions),
            f"{incomplete_exclusions} included Python file(s) could not be inspected within "
            "the immutable source-safety bounds.",
        ),
        (
            lambda: collection.truncated,
            "Only prioritized critical-path scope was analyzed due to resource limits.",
        ),
        (
            lambda: bool(
                interpreted.omitted_batches
                or interpreted.oversized_symbols
                or interpreted.truncated_evidence_symbols
            ),
            "Some optional model interpretation context was omitted or truncated.",
        ),
        (
            lambda: fallback_mode and bool(deterministic),
            "LLM interpretation was unavailable; deterministic fallback was used.",
        ),
        (
            lambda: (not fallback_mode) and partial_fallback,
            "LLM interpretation was partial; deterministic fallback was retained for uncovered "
            "evidence.",
        ),
    ]
    for predicate, message in rules:
        if predicate():
            limitations.append(message)
    return limitations


def _coverage_summary(
    coverage: CoverageMap | None,
    reportable: list[_ScoredCandidate],
    state: _PipelineState,
) -> CoverageSummary | None:
    """Report what the ingested coverage report actually matched.

    Unmatched files are surfaced as a warning rather than absorbed silently: a coverage report
    written against a different working directory would otherwise look exactly like a
    repository with no tests.
    """
    if coverage is None:
        return None
    ranges = [span for item in reportable for span in _coverage_ranges(item.candidate)]
    changed, covered, uncovered = summarize(coverage, ranges)
    if coverage.unmatched_files:
        state.warnings.append(
            f"Coverage report matched {coverage.matched_files} changed file(s) and did not "
            f"match {coverage.unmatched_files}; unmatched files are reported as unknown, not "
            "as uncovered. Check that the report's paths share a suffix with the repository's."
        )
    return CoverageSummary(
        source=coverage.source,
        matched_files=coverage.matched_files,
        unmatched_files=coverage.unmatched_files,
        changed_lines=changed,
        covered_lines=covered,
        uncovered_lines=uncovered,
    )


def analyze(arguments: dict[str, Any], *, llm: LlmClient | None = None) -> dict[str, Any]:
    """Analyze committed Python changes and return the requested transport dictionary."""
    request, repo, base_commit, head_commit, config, config_warnings = _bootstrap(arguments)
    state = _PipelineState(warnings=list(config_warnings))
    coverage = _load_coverage(config, state)
    collection = collect_diff(repo, base_commit, head_commit, config)
    state.warnings.extend(collection.warnings)
    ast_result = analyze_ast([item.as_revision_pair() for item in collection.files])
    state.warnings.extend(ast_result.warnings)
    deltas, changed_symbols, incomplete_exclusions = _collect_scope(
        collection, ast_result, config, state
    )
    deterministic = build_candidates(deltas, config)
    readme_excerpt = (
        _read_readme_excerpt(repo, head_commit, config)
        if deterministic and llm is not None and config.rules.max_llm_calls
        else None
    )
    interpreted = interpret_candidates(
        deterministic,
        llm,
        config,
        readme_excerpt=readme_excerpt,
    )
    state.warnings.extend(interpreted.warnings)
    state.record_omitted(
        "llm_batch_limit", interpreted.omitted_batches, scope=True, confidence=True
    )
    state.record_omitted(
        "model_input_symbol_limit",
        interpreted.oversized_symbols,
        scope=True,
        confidence=True,
    )
    state.record_omitted(
        "model_evidence_limit",
        interpreted.truncated_evidence_symbols,
        scope=True,
        confidence=True,
    )
    candidates = _deduplicate_candidates(interpreted.candidates)
    reportable, low_confidence_omitted = _filter_reportable(candidates, config, state)
    test_index = (
        build_test_index(repo, head_commit, config)
        if reportable and config.rules.max_candidate_tests_per_obligation
        else TestIndex(tests=[], incomplete=False, warnings=[])
    )
    state.warnings.extend(test_index.warnings)
    mapped_by_index = map_candidate_tests(
        [item.candidate for item in reportable], test_index, config
    )
    reportable = [
        replace(item, tests=mapped_by_index.get(index, [])) for index, item in enumerate(reportable)
    ]
    fallback_mode = not interpreted.status.available
    partial_fallback = bool(interpreted.status.failures or interpreted.omitted_batches)
    behaviors, tests_by_behavior, coverage_states = _materialize_behaviors(
        reportable,
        config,
        fallback_mode=fallback_mode,
        partial_fallback=partial_fallback,
        coverage=coverage,
    )
    obligations, omitted_obligations = generate_obligations(
        behaviors,
        tests_by_behavior,
        test_index.incomplete,
        config.rules,
        interpreted.suggestions,
        coverage_states,
    )
    coverage_summary = _coverage_summary(coverage, reportable, state)
    if omitted_obligations:
        state.record_omitted("global_obligation_limit", omitted_obligations, scope=True)
        state.warnings.append(
            f"Omitted {omitted_obligations} lower-priority obligation(s) due to the global cap."
        )
    overall_risk, overall_score, overall_confidence, risk_counts = _summary_metrics(
        behaviors,
        obligations,
        scope_truncated=state.scope_truncated,
        failed_files=ast_result.failed_files,
    )
    limitations = _build_limitations(
        collection=collection,
        ast_result=ast_result,
        incomplete_exclusions=incomplete_exclusions,
        low_confidence_omitted=low_confidence_omitted,
        interpreted=interpreted,
        fallback_mode=fallback_mode,
        partial_fallback=partial_fallback,
        deterministic=deterministic,
    )
    analysis = AnalysisResult(
        analysis_id=f"sdw_{uuid4().hex}",
        repository=RepositoryIdentity(
            base_ref=request.base_ref,
            head_ref=request.head_ref,
            base_commit=base_commit,
            head_commit=head_commit,
        ),
        summary=Summary(
            changed_files=collection.changed_files_total,
            changed_symbols=changed_symbols,
            behavior_changes=len(behaviors),
            test_obligations=len(obligations),
            overall_risk=overall_risk,
            risk_score=overall_score,
            overall_confidence=overall_confidence,
            risk_counts=risk_counts,
        ),
        scope=ScopeMetadata(
            changed_files_total=collection.changed_files_total,
            analyzed_files=sorted(item.path for item in collection.files),
            excluded_counts=collection.excluded_counts,
            omitted=state.omitted,
            changed_lines=collection.changed_lines,
            changed_symbols=changed_symbols,
            truncated=state.scope_truncated,
        ),
        behavior_changes=behaviors,
        test_obligations=obligations,
        warnings=sorted(set(state.warnings)),
        limitations=limitations,
        coverage=coverage_summary,
        llm=interpreted.status if deterministic else LlmStatus(),
        deterministic_mode=fallback_mode or partial_fallback,
    )
    return render_transport(analysis, request.output_format)
