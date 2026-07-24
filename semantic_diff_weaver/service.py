"""Typed orchestration for the bounded read-only semantic diff pipeline."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from .ast_diff import StructuralDelta, analyze_ast
from .config import load_config
from .errors import ErrorCode, WeaverError
from .git_diff import DiffCollection, GitRepository, collect_diff
from .models import (
    AnalysisResult,
    AnalyzeRequest,
    BehaviorChange,
    LlmStatus,
    OmittedScope,
    Origin,
    Presentation,
    RepositoryIdentity,
    RiskLabel,
    ScopeMetadata,
    Summary,
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
            if "/" not in path
            and not exclusion_reason(path)
            and not any(glob_matches(path, pattern) for pattern in config.paths.exclude)
            if path.rsplit("/", 1)[-1].casefold() in README_NAMES
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


def _deduplicate_candidates(candidates: list[SemanticCandidate]) -> list[SemanticCandidate]:
    output: list[SemanticCandidate] = []
    for candidate in candidates:
        candidate_evidence = {item.id for item in candidate.evidence}
        existing = next(
            (
                item
                for item in output
                if item.path == candidate.path
                and item.symbol == candidate.symbol
                and item.category is candidate.category
                and (
                    candidate_evidence & {evidence.id for evidence in item.evidence}
                    or canonical_phrase(item.observable_impact)
                    == canonical_phrase(candidate.observable_impact)
                )
            ),
            None,
        )
        if existing is None:
            output.append(candidate)
            continue
        if candidate.confidence_baseline > existing.confidence_baseline:
            existing.summary = candidate.summary
            existing.observable_impact = candidate.observable_impact
            existing.confidence_baseline = candidate.confidence_baseline
        evidence_by_id = {item.id: item for item in existing.evidence}
        for evidence in candidate.evidence:
            evidence_by_id.setdefault(evidence.id, evidence)
        existing.evidence = [evidence_by_id[key] for key in sorted(evidence_by_id)]
        existing.assumptions = sorted(set([*existing.assumptions, *candidate.assumptions]))
        existing.rule_ids = sorted(set([*existing.rule_ids, *candidate.rule_ids]))
        existing.related_calls.update(candidate.related_calls)
        existing.related_paths.update(candidate.related_paths)
        if candidate.origin is Origin.LLM_SUPPORTED:
            existing.origin = Origin.LLM_SUPPORTED
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


def _record_omitted(omitted: list[OmittedScope], reason: str, count: int) -> None:
    if count:
        omitted.append(OmittedScope(reason=reason, count=count))


def _collect_scope(
    collection: DiffCollection,
    ast_result: Any,
    config: WeaverConfig,
) -> tuple[list[StructuralDelta], int, list[OmittedScope], bool, bool, int]:
    incomplete_exclusions = sum(
        collection.excluded_counts.get(reason, 0) for reason in INCOMPLETE_EXCLUSION_REASONS
    )
    omitted: list[OmittedScope] = [
        OmittedScope(reason=reason, count=count)
        for reason, count in sorted(collection.omitted_counts.items())
        if count
    ]
    scope_truncated = collection.truncated or bool(incomplete_exclusions)
    confidence_truncated = collection.truncated or bool(incomplete_exclusions)
    syntax_failed_files = ast_result.failed_files - ast_result.resource_limited_files
    if syntax_failed_files:
        _record_omitted(omitted, "parse_incomplete_files", syntax_failed_files)
        scope_truncated = True
    if ast_result.resource_limited_files:
        _record_omitted(omitted, "ast_resource_limit", ast_result.resource_limited_files)
        scope_truncated = True
    deltas = ast_result.deltas
    changed_symbols = ast_result.changed_symbols
    if changed_symbols > config.rules.max_changed_symbols:
        deltas, omitted_count = _prioritize_deltas(deltas, config.rules.max_changed_symbols, config)
        _record_omitted(omitted, "changed_symbol_limit", omitted_count)
        changed_symbols = config.rules.max_changed_symbols
        scope_truncated = True
        confidence_truncated = True
    return (
        deltas,
        changed_symbols,
        omitted,
        scope_truncated,
        confidence_truncated,
        incomplete_exclusions,
    )


def _filter_reportable(
    candidates: list[SemanticCandidate],
    config: WeaverConfig,
    *,
    confidence_truncated: bool,
) -> tuple[list[SemanticCandidate], dict[int, float], int, int, list[str], list[OmittedScope]]:
    reportable: list[SemanticCandidate] = []
    confidence_by_index: dict[int, float] = {}
    warnings: list[str] = []
    omitted: list[OmittedScope] = []
    low_confidence_omitted = 0
    refactors_omitted = 0
    for candidate in candidates:
        confidence = confidence_score(candidate, truncated=confidence_truncated)
        if (
            candidate.category.value == "refactor_likely_no_behavior_change"
            and not config.rules.emit_low_risk_refactors
        ):
            refactors_omitted += 1
            continue
        if confidence < config.rules.minimum_report_confidence:
            low_confidence_omitted += 1
            continue
        confidence_by_index[id(candidate)] = confidence
        reportable.append(candidate)
    if low_confidence_omitted:
        warnings.append(
            f"Moved {low_confidence_omitted} finding(s) below the minimum confidence into limitations."
        )
        _record_omitted(omitted, "minimum_confidence", low_confidence_omitted)
    if refactors_omitted:
        _record_omitted(omitted, "low_risk_refactor_policy", refactors_omitted)
    return (
        reportable,
        confidence_by_index,
        low_confidence_omitted,
        refactors_omitted,
        warnings,
        omitted,
    )


def _materialize_behaviors(
    reportable: list[SemanticCandidate],
    confidence_by_index: dict[int, float],
    mapped_by_index: dict[int, list[Any]],
    config: WeaverConfig,
    *,
    fallback_mode: bool,
    partial_fallback: bool,
) -> tuple[list[BehaviorChange], dict[str, list[Any]]]:
    behaviors: list[BehaviorChange] = []
    tests_by_behavior: dict[str, list[Any]] = {}
    for index, candidate in enumerate(reportable, start=1):
        candidate_tests = mapped_by_index.get(index - 1, [])
        risk_score, risk, explanation = score_risk(candidate, candidate_tests, config)
        confidence = confidence_by_index[id(candidate)]
        presentation = (
            Presentation.REVIEW_QUESTION
            if risk in {RiskLabel.HIGH, RiskLabel.CRITICAL}
            and confidence < config.rules.review_question_confidence
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
            confidence=confidence,
            evidence=candidate.evidence,
            assumptions=candidate.assumptions,
            presentation=presentation,
            origin=origin,
            score_explanation=explanation,
        )
        behaviors.append(behavior)
        tests_by_behavior[behavior.id] = candidate_tests
    return behaviors, tests_by_behavior


def _summary_metrics(
    behaviors: list[BehaviorChange],
    obligations: list[Any],
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
    ast_result: Any,
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
            lambda: bool(interpreted.omitted_batches or interpreted.truncated_evidence_symbols),
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


def analyze(arguments: dict[str, Any], *, llm: Any = None) -> dict[str, Any]:
    """Analyze committed Python changes and return the requested transport dictionary."""
    request, repo, base_commit, head_commit, config, config_warnings = _bootstrap(arguments)
    collection = collect_diff(repo, base_commit, head_commit, config)
    ast_result = analyze_ast(collection.files)
    (
        deltas,
        changed_symbols,
        omitted,
        scope_truncated,
        confidence_truncated,
        incomplete_exclusions,
    ) = _collect_scope(collection, ast_result, config)
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
    if interpreted.omitted_batches:
        _record_omitted(omitted, "llm_batch_limit", interpreted.omitted_batches)
        scope_truncated = True
        confidence_truncated = True
    if interpreted.truncated_evidence_symbols:
        _record_omitted(omitted, "model_evidence_limit", interpreted.truncated_evidence_symbols)
        scope_truncated = True
        confidence_truncated = True
    candidates = _deduplicate_candidates(interpreted.candidates)
    (
        reportable,
        confidence_by_index,
        low_confidence_omitted,
        _refactors_omitted,
        filter_warnings,
        filter_omitted,
    ) = _filter_reportable(candidates, config, confidence_truncated=confidence_truncated)
    omitted.extend(filter_omitted)
    warnings = [
        *config_warnings,
        *collection.warnings,
        *ast_result.warnings,
        *interpreted.warnings,
        *filter_warnings,
    ]
    test_index = (
        build_test_index(repo, head_commit, config)
        if reportable and config.rules.max_candidate_tests_per_obligation
        else TestIndex(tests=[], incomplete=False, warnings=[])
    )
    warnings.extend(test_index.warnings)
    mapped_by_index = map_candidate_tests(reportable, test_index, config)
    fallback_mode = not interpreted.status.available
    partial_fallback = bool(interpreted.status.failures or interpreted.omitted_batches)
    behaviors, tests_by_behavior = _materialize_behaviors(
        reportable,
        confidence_by_index,
        mapped_by_index,
        config,
        fallback_mode=fallback_mode,
        partial_fallback=partial_fallback,
    )
    obligations, omitted_obligations = generate_obligations(
        behaviors,
        tests_by_behavior,
        test_index.incomplete,
        config,
        interpreted.suggestions,
    )
    if omitted_obligations:
        _record_omitted(omitted, "global_obligation_limit", omitted_obligations)
        warnings.append(
            f"Omitted {omitted_obligations} lower-priority obligation(s) due to the global cap."
        )
    overall_risk, overall_score, overall_confidence, risk_counts = _summary_metrics(
        behaviors,
        obligations,
        scope_truncated=scope_truncated,
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
            omitted=omitted,
            changed_lines=collection.changed_lines,
            changed_symbols=changed_symbols,
            truncated=scope_truncated or bool(omitted_obligations),
        ),
        behavior_changes=behaviors,
        test_obligations=obligations,
        warnings=sorted(set(warnings)),
        limitations=limitations,
        llm=interpreted.status if deterministic else LlmStatus(),
        deterministic_mode=fallback_mode or partial_fallback,
    )
    return render_transport(analysis, request.output_format)
