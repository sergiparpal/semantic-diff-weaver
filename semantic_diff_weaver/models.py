"""Validated domain and transport models for schema version 1.0."""

from __future__ import annotations

from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import ErrorCode

SCHEMA_VERSION: Literal["1.0"] = "1.0"
MAX_PATH_CHARS = 4096
MAX_REF_CHARS = 1024
MAX_PATTERN_CHARS = 512
MAX_PATH_PATTERNS = 256
RelativePattern = Annotated[str, Field(min_length=1, max_length=MAX_PATTERN_CHARS)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def _repository_relative_posix_path(value: str) -> str:
    """Validate paths embedded in public output without importing path-policy logic."""
    if not value or "\x00" in value or "\\" in value:
        raise ValueError("path must be a non-empty repository-relative POSIX path")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise ValueError("path must be repository-relative")
    if any(part in {"", ".", ".."} for part in posix.parts):
        raise ValueError("path contains traversal or an empty component")
    return posix.as_posix()


class OutputFormat(StrEnum):
    JSON = "json"
    MARKDOWN = "markdown"
    BOTH = "both"


class RiskLabel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class BehaviorCategory(StrEnum):
    BOUNDARY = "boundary_change"
    VALIDATION = "validation_change"
    ERROR_HANDLING = "error_handling_change"
    STATE_TRANSITION = "state_transition_change"
    AUTHORIZATION = "authorization_change"
    RETRY_TIMEOUT = "retry_timeout_change"
    OUTPUT_CONTRACT = "output_contract_change"
    SIDE_EFFECT = "side_effect_change"
    ORDERING = "ordering_change"
    DEFAULT_BEHAVIOR = "default_behavior_change"
    DEPENDENCY_INTERACTION = "dependency_interaction_change"
    REFACTOR = "refactor_likely_no_behavior_change"
    UNKNOWN = "unknown_semantic_change"


class ObligationType(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    BOUNDARY = "boundary"
    ERROR = "error"
    STATE = "state"
    INTERACTION = "interaction"
    REGRESSION = "regression"
    REVIEW = "review"


class CoverageStatus(StrEnum):
    CANDIDATE_UNVERIFIED = "candidate_exists_unverified"
    NONE_FOUND = "no_candidate_found"
    INCOMPLETE = "mapping_incomplete"


class Origin(StrEnum):
    DETERMINISTIC = "deterministic"
    LLM_SUPPORTED = "llm_supported"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"


class Presentation(StrEnum):
    FINDING = "finding"
    REVIEW_QUESTION = "review_question"


class AnalyzeRequest(StrictModel):
    repo_path: str = Field(min_length=1, max_length=MAX_PATH_CHARS)
    base_ref: str = Field(min_length=1, max_length=MAX_REF_CHARS)
    head_ref: str = Field(default="HEAD", min_length=1, max_length=MAX_REF_CHARS)
    risk_profile: str | None = Field(default=None, max_length=MAX_PATH_CHARS)
    include: Annotated[list[RelativePattern], Field(max_length=MAX_PATH_PATTERNS)] | None = None
    exclude: Annotated[list[RelativePattern], Field(max_length=MAX_PATH_PATTERNS)] | None = None
    output_format: OutputFormat = OutputFormat.BOTH


class CriticalPath(StrictModel):
    pattern: RelativePattern
    weight: int = Field(ge=0, le=100)


class MappingRule(StrictModel):
    source: RelativePattern
    tests: Annotated[list[RelativePattern], Field(min_length=1, max_length=64)]


class LanguageConfig(StrictModel):
    primary: Literal["python"] = "python"


class PathsConfig(StrictModel):
    include: Annotated[list[RelativePattern], Field(min_length=1, max_length=MAX_PATH_PATTERNS)] = (
        Field(default_factory=lambda: ["**/*.py"])
    )
    exclude: Annotated[list[RelativePattern], Field(max_length=MAX_PATH_PATTERNS)] = Field(
        default_factory=lambda: ["**/migrations/**", "**/generated/**", "**/vendor/**"]
    )
    test_roots: Annotated[
        list[RelativePattern], Field(min_length=1, max_length=MAX_PATH_PATTERNS)
    ] = Field(default_factory=lambda: ["tests"])


class RulesConfig(StrictModel):
    max_changed_files: int = Field(default=40, ge=1, le=10000)
    max_diff_lines: int = Field(default=3000, ge=1, le=1_000_000)
    max_changed_symbols: int = Field(default=100, ge=1, le=10000)
    max_file_bytes: int = Field(default=1_000_000, ge=1024, le=100_000_000)
    max_readme_chars: int = Field(default=4000, ge=0, le=100_000)
    max_evidence_chars_per_symbol: int = Field(default=6000, ge=256, le=100_000)
    max_model_input_chars_per_call: int = Field(default=48000, ge=1024, le=1_000_000)
    max_llm_calls: int = Field(default=8, ge=0, le=8)
    max_obligations_per_behavior: int = Field(default=6, ge=1, le=50)
    max_test_obligations: int = Field(default=100, ge=1, le=1000)
    max_candidate_tests_per_obligation: int = Field(default=5, ge=0, le=25)
    minimum_report_confidence: float = Field(default=0.45, ge=0, le=1)
    review_question_confidence: float = Field(default=0.60, ge=0, le=1)
    refactor_materiality_threshold: float = Field(default=0.25, ge=0, le=1)
    emit_low_risk_refactors: bool = False
    deterministic_fallback: bool = True


class PrivacyConfig(StrictModel):
    redact_patterns: Literal[True] = True
    allow_network: Literal[False] = False


class WeaverConfig(StrictModel):
    version: Literal[1] = 1
    language: LanguageConfig = Field(default_factory=LanguageConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    critical_paths: Annotated[list[CriticalPath], Field(max_length=MAX_PATH_PATTERNS)] = Field(
        default_factory=list
    )
    rules: RulesConfig = Field(default_factory=RulesConfig)
    mapping: Annotated[list[MappingRule], Field(max_length=MAX_PATH_PATTERNS)] = Field(
        default_factory=list
    )
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)

    @model_validator(mode="after")
    def unique_mappings(self) -> WeaverConfig:
        sources = [item.source for item in self.mapping]
        if len(sources) != len(set(sources)):
            raise ValueError("duplicate semantic mapping source")
        return self


class LineRange(StrictModel):
    start: int = Field(ge=1)
    end: int = Field(ge=1)

    @model_validator(mode="after")
    def ordered(self) -> LineRange:
        if self.end < self.start:
            raise ValueError("line range end precedes start")
        return self


class Evidence(StrictModel):
    id: str = Field(pattern=r"^ev-\d{3,}$")
    path: str = Field(min_length=1)
    symbol: str | None = None
    old_lines: LineRange | None = None
    new_lines: LineRange | None = None
    hunk_id: str | None = None
    old: str | None = None
    new: str | None = None
    kind: str = Field(min_length=1)
    parser_complete: bool = True

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        return _repository_relative_posix_path(value)


class ScoreExplanation(StrictModel):
    behavioral_impact: float = Field(ge=0, le=100)
    critical_path_weight: float = Field(ge=0, le=100)
    test_gap_weight: float = Field(ge=0, le=100)
    change_surface_weight: float = Field(ge=0, le=100)


class BehaviorChange(StrictModel):
    id: str = Field(pattern=r"^bc-\d{3,}$")
    category: BehaviorCategory
    summary: str = Field(min_length=1, max_length=500)
    observable_impact: str = Field(min_length=1, max_length=1000)
    risk: RiskLabel
    risk_score: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    evidence: list[Evidence] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    presentation: Presentation = Presentation.FINDING
    origin: Origin
    score_explanation: ScoreExplanation


class CandidateTest(StrictModel):
    path: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    match_score: float = Field(ge=0, le=1)
    match_reasons: list[str] = Field(min_length=1)
    verified: Literal[False] = False

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        return _repository_relative_posix_path(value)


class TestObligation(StrictModel):
    id: str = Field(pattern=r"^to-\d{3,}$")
    behavior_change_ids: list[str] = Field(min_length=1)
    type: ObligationType
    priority: int = Field(ge=0, le=100)
    title: str = Field(min_length=1, max_length=300)
    given: str = Field(min_length=1, max_length=1000)
    when: str = Field(min_length=1, max_length=1000)
    then: str = Field(min_length=1, max_length=1000)
    candidate_existing_tests: list[CandidateTest] = Field(default_factory=list)
    coverage_status: CoverageStatus
    origin: Origin
    confidence: float = Field(ge=0, le=1)


class RepositoryIdentity(StrictModel):
    path: Literal["."] = "."
    base_ref: str
    head_ref: str
    base_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    head_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")


class OmittedScope(StrictModel):
    reason: str
    count: int = Field(ge=0)


class ScopeMetadata(StrictModel):
    changed_files_total: int = Field(ge=0)
    analyzed_files: list[str] = Field(default_factory=list)
    excluded_counts: dict[str, int] = Field(default_factory=dict)
    omitted: list[OmittedScope] = Field(default_factory=list)
    changed_lines: int = Field(ge=0)
    changed_symbols: int = Field(ge=0)
    truncated: bool = False

    @field_validator("analyzed_files")
    @classmethod
    def relative_paths(cls, values: list[str]) -> list[str]:
        return [_repository_relative_posix_path(value) for value in values]


class Summary(StrictModel):
    changed_files: int = Field(ge=0)
    changed_symbols: int = Field(ge=0)
    behavior_changes: int = Field(ge=0)
    test_obligations: int = Field(ge=0)
    overall_risk: RiskLabel
    risk_score: int = Field(ge=0, le=100)
    overall_confidence: float = Field(ge=0, le=1)
    risk_counts: dict[RiskLabel, int]


class LlmUsage(StrictModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0)


class LlmStatus(StrictModel):
    attempted: bool = False
    available: bool = False
    calls: int = Field(default=0, ge=0, le=8)
    failures: int = Field(default=0, ge=0)
    usage: LlmUsage | None = None


class AnalysisResult(StrictModel):
    success: Literal[True] = True
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    analysis_id: str = Field(pattern=r"^sdw_[A-Za-z0-9_-]+$")
    repository: RepositoryIdentity
    summary: Summary
    scope: ScopeMetadata
    behavior_changes: list[BehaviorChange]
    test_obligations: list[TestObligation]
    warnings: list[str]
    limitations: list[str]
    llm: LlmStatus
    deterministic_mode: bool

    @model_validator(mode="after")
    def references_resolve(self) -> AnalysisResult:
        if self.summary.changed_files != self.scope.changed_files_total:
            raise ValueError("summary and scope changed-file counts must agree")
        if self.summary.changed_symbols != self.scope.changed_symbols:
            raise ValueError("summary and scope changed-symbol counts must agree")
        if self.summary.behavior_changes != len(self.behavior_changes):
            raise ValueError("summary behavior count must match behavior_changes")
        if self.summary.test_obligations != len(self.test_obligations):
            raise ValueError("summary obligation count must match test_obligations")
        if len(set(self.scope.analyzed_files)) != len(self.scope.analyzed_files):
            raise ValueError("analyzed file paths must be unique")
        expected_risk_counts = {
            label: sum(item.risk is label for item in self.behavior_changes) for label in RiskLabel
        }
        if self.summary.risk_counts != expected_risk_counts:
            raise ValueError("summary risk counts must match behavior_changes")
        if self.behavior_changes:
            highest_score = max(item.risk_score for item in self.behavior_changes)
            highest_risks = {
                item.risk for item in self.behavior_changes if item.risk_score == highest_score
            }
            if self.summary.risk_score != highest_score:
                raise ValueError("summary risk score must equal the highest behavior score")
            if self.summary.overall_risk not in highest_risks:
                raise ValueError("summary risk label must come from a highest-scoring behavior")
        elif self.summary.risk_score or self.summary.overall_risk is not RiskLabel.LOW:
            raise ValueError("an empty analysis must have zero low risk")
        behavior_ids = {item.id for item in self.behavior_changes}
        if len(behavior_ids) != len(self.behavior_changes):
            raise ValueError("behavior change IDs must be unique")
        obligation_ids = {item.id for item in self.test_obligations}
        if len(obligation_ids) != len(self.test_obligations):
            raise ValueError("test obligation IDs must be unique")
        evidence_registry: dict[str, Evidence] = {}
        linked_behavior_ids: set[str] = set()
        for obligation in self.test_obligations:
            if not set(obligation.behavior_change_ids) <= behavior_ids:
                raise ValueError("obligation references unknown behavior change")
            if len(set(obligation.behavior_change_ids)) != len(obligation.behavior_change_ids):
                raise ValueError("obligation repeats a behavior change reference")
            linked_behavior_ids.update(obligation.behavior_change_ids)
        for behavior in self.behavior_changes:
            for evidence in behavior.evidence:
                previous = evidence_registry.get(evidence.id)
                if previous is not None and previous != evidence:
                    raise ValueError("an evidence ID resolves to conflicting records")
                evidence_registry[evidence.id] = evidence
        required = {
            item.id
            for item in self.behavior_changes
            if item.risk in {RiskLabel.HIGH, RiskLabel.CRITICAL}
        }
        if not required <= linked_behavior_ids:
            raise ValueError("every high or critical behavior must have a linked obligation")
        return self


class MarkdownEnvelope(StrictModel):
    success: Literal[True] = True
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    analysis_id: str = Field(pattern=r"^sdw_[A-Za-z0-9_-]+$")
    markdown: str


class BothEnvelope(StrictModel):
    success: Literal[True] = True
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    analysis: AnalysisResult
    markdown: str


class ErrorResponse(StrictModel):
    success: Literal[False] = False
    error: ErrorCode
    message: str
    remediation: str


class LlmBehavior(StrictModel):
    category: BehaviorCategory
    summary: str = Field(min_length=1, max_length=500)
    observable_impact: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    assumptions: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(ge=0, le=1)


class LlmObligation(StrictModel):
    behavior_index: int = Field(ge=0)
    type: ObligationType
    title: str = Field(min_length=1, max_length=300)
    given: str = Field(min_length=1, max_length=1000)
    when: str = Field(min_length=1, max_length=1000)
    then: str = Field(min_length=1, max_length=1000)


class LlmBatchResponse(StrictModel):
    behaviors: Annotated[list[LlmBehavior], Field(max_length=30)] = Field(default_factory=list)
    obligations: Annotated[list[LlmObligation], Field(max_length=100)] = Field(default_factory=list)
