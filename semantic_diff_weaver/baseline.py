"""Compare an analysis with an earlier one: which findings are new, still there, or gone.

The baseline is untrusted input data, as a coverage report is: bounded, parsed with the standard
library, read for the few fields the comparison needs, and never executed. Only its validated
category, path, symbol, and risk ever reach the output; its prose does not, so a crafted file
cannot write a heading into the brief.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .errors import ErrorCode, WeaverError
from .models import (
    MAX_PATH_CHARS,
    BaselineComparison,
    BaselineFinding,
    BehaviorCategory,
    BehaviorChange,
    Evidence,
    RiskLabel,
    _repository_relative_posix_path,
)

# More findings than any analysis reports: `rules.max_changed_symbols` caps symbols at 10,000.
MAX_BASELINE_FINDINGS = 20_000
MAX_BASELINE_EVIDENCE = 200
MAX_EVIDENCE_TEXT_CHARS = 10_000
# The fields read here exist unchanged in every 1.x schema.
SUPPORTED_SCHEMA_MAJOR = "1"


def baseline_error(message: str) -> WeaverError:
    return WeaverError(
        ErrorCode.BASELINE_UNREADABLE,
        message,
        "Supply the JSON this tool wrote for an earlier analysis of the same repository, from "
        "`--format json` or `--format both`.",
    )


class _Read(BaseModel):
    # The baseline holds far more than the comparison reads; the rest is ignored, not trusted.
    model_config = ConfigDict(extra="ignore", frozen=True)


class _Evidence(_Read):
    path: str = Field(min_length=1, max_length=MAX_PATH_CHARS)
    symbol: str | None = Field(default=None, max_length=MAX_PATH_CHARS)
    kind: str = Field(min_length=1, max_length=200)
    old: str | None = Field(default=None, max_length=MAX_EVIDENCE_TEXT_CHARS)
    new: str | None = Field(default=None, max_length=MAX_EVIDENCE_TEXT_CHARS)

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        return _repository_relative_posix_path(value)


class _Behavior(_Read):
    category: BehaviorCategory
    risk: RiskLabel
    evidence: list[_Evidence] = Field(min_length=1, max_length=MAX_BASELINE_EVIDENCE)


class _Repository(_Read):
    head_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")


class BaselineAnalysis(_Read):
    schema_version: str = Field(max_length=16)
    analysis_id: str = Field(pattern=r"^sdw_[A-Za-z0-9_-]+$", max_length=200)
    repository: _Repository
    behavior_changes: list[_Behavior] = Field(max_length=MAX_BASELINE_FINDINGS)


def _read_bounded(path: Path, limit: int) -> str:
    try:
        resolved = path.resolve(strict=True)
        size = resolved.stat().st_size
    except OSError as exc:
        raise baseline_error("The baseline analysis is inaccessible.") from exc
    if not resolved.is_file():
        raise baseline_error("The baseline analysis is not a regular file.")
    if size > limit:
        raise baseline_error(
            f"The baseline analysis is {size} bytes, above the {limit}-byte limit."
        )
    try:
        # One character past the limit, so a file that grows between stat and read is caught.
        with resolved.open("r", encoding="utf-8", errors="strict") as handle:
            text = handle.read(limit + 1)
    except (OSError, UnicodeDecodeError) as exc:
        raise baseline_error("The baseline analysis is not readable UTF-8 text.") from exc
    if len(text.encode("utf-8", errors="ignore")) > limit:
        raise baseline_error("The baseline analysis exceeded the size limit while being read.")
    return text


def load_baseline(path: Path, limit: int) -> BaselineAnalysis:
    """Read an earlier analysis, as `--format json` or `--format both` wrote it."""
    try:
        document: Any = json.loads(_read_bounded(path, limit))
    except (json.JSONDecodeError, RecursionError) as exc:
        raise baseline_error("The baseline analysis is not valid JSON.") from exc
    # `both` wraps the analysis beside the brief; `json` is the analysis itself.
    if isinstance(document, dict) and isinstance(document.get("analysis"), dict):
        document = document["analysis"]
    if not isinstance(document, dict) or "behavior_changes" not in document:
        raise baseline_error(
            "The baseline holds no analysis; a `--format markdown` brief cannot be compared."
        )
    try:
        analysis = BaselineAnalysis.model_validate(document)
    except ValidationError as exc:
        raise baseline_error("The baseline analysis does not have the expected shape.") from exc
    if analysis.schema_version.split(".", 1)[0] != SUPPORTED_SCHEMA_MAJOR:
        raise baseline_error(
            f"The baseline analysis has schema version {analysis.schema_version!r}; only 1.x "
            "can be compared."
        )
    return analysis


Identity = tuple[BehaviorCategory, str, str]
Fingerprint = tuple[tuple[str, str, str, str | None, str | None], ...]


def _identity(category: BehaviorCategory, first: _Evidence | Evidence) -> Identity:
    # The key findings are grouped by within one analysis, so it names one finding in each.
    return category, first.path, first.symbol or "<module>"


def _fingerprint(evidence: Iterable[_Evidence | Evidence]) -> Fingerprint:
    """What changed, without where: line numbers move with unrelated edits above a symbol."""
    return tuple(
        sorted((item.kind, item.path, item.symbol or "", item.old, item.new) for item in evidence)
    )


def compare_with_baseline(
    baseline: BaselineAnalysis, behaviors: list[BehaviorChange]
) -> BaselineComparison:
    earlier: dict[Identity, _Behavior] = {}
    for item in baseline.behavior_changes:
        # A crafted baseline may repeat an identity; the first one stands for it.
        earlier.setdefault(_identity(item.category, item.evidence[0]), item)
    comparison = BaselineComparison(
        analysis_id=baseline.analysis_id, head_commit=baseline.repository.head_commit
    )
    seen: set[Identity] = set()
    for behavior in behaviors:
        identity = _identity(behavior.category, behavior.evidence[0])
        seen.add(identity)
        before = earlier.get(identity)
        if before is None:
            comparison.new.append(behavior.id)
        elif _fingerprint(before.evidence) == _fingerprint(behavior.evidence):
            comparison.unchanged.append(behavior.id)
        else:
            comparison.updated.append(behavior.id)
    comparison.resolved.extend(
        BaselineFinding(category=identity[0], path=identity[1], symbol=identity[2], risk=item.risk)
        for identity, item in earlier.items()
        if identity not in seen
    )
    return comparison
