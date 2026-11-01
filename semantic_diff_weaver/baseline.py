"""Compare an analysis with an earlier one: which findings are new, still there, or gone.

The baseline is untrusted input data, as a coverage report is: bounded, parsed with the standard
library, read for the few fields the comparison needs, and never executed. Only its validated
category, path, symbol, and risk ever reach the output; its prose does not, so a crafted file
cannot write a heading into the brief.

It is either an earlier analysis, as `--format json` or `--format both` wrote it, or the digest
of one that `digest_of` builds — each finding's category, path, symbol, risk, and a fingerprint
of its evidence, small enough for the GitHub Action to keep in its pull-request comment between
pushes. Both reduce to the same fingerprints, so they compare alike.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

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
from .textutil import read_bounded_text

# More findings than any analysis reports: `rules.max_changed_symbols` caps symbols at 10,000.
MAX_BASELINE_FINDINGS = 20_000
MAX_BASELINE_EVIDENCE = 200
MAX_EVIDENCE_TEXT_CHARS = 10_000
# The fields read here exist unchanged in every 1.x schema.
SUPPORTED_SCHEMA_MAJOR = "1"
# The key a digest is told apart by, and its format version.
DIGEST_KEY = "semantic_diff_weaver_baseline"
DIGEST_VERSION = 1
FINGERPRINT_CHARS = 32


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


class _Located(_Read):
    path: str = Field(min_length=1, max_length=MAX_PATH_CHARS)

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        return _repository_relative_posix_path(value)


class _Evidence(_Located):
    symbol: str | None = Field(default=None, max_length=MAX_PATH_CHARS)
    kind: str = Field(min_length=1, max_length=200)
    old: str | None = Field(default=None, max_length=MAX_EVIDENCE_TEXT_CHARS)
    new: str | None = Field(default=None, max_length=MAX_EVIDENCE_TEXT_CHARS)


class _Behavior(_Read):
    category: BehaviorCategory
    risk: RiskLabel
    evidence: list[_Evidence] = Field(min_length=1, max_length=MAX_BASELINE_EVIDENCE)


class _Repository(_Read):
    head_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")


class _Analysis(_Read):
    schema_version: str = Field(max_length=16)
    analysis_id: str = Field(pattern=r"^sdw_[A-Za-z0-9_-]+$", max_length=200)
    repository: _Repository
    behavior_changes: list[_Behavior] = Field(max_length=MAX_BASELINE_FINDINGS)


class _DigestFinding(_Located):
    category: BehaviorCategory
    symbol: str = Field(min_length=1, max_length=MAX_PATH_CHARS)
    risk: RiskLabel
    fingerprint: str = Field(pattern=rf"^[0-9a-f]{{{FINGERPRINT_CHARS}}}$")


class _Digest(_Read):
    # A literal, as the pydantic mypy plugin requires of an alias; it is `DIGEST_KEY`.
    version: Literal[1] = Field(alias="semantic_diff_weaver_baseline")
    analysis_id: str = Field(pattern=r"^sdw_[A-Za-z0-9_-]+$", max_length=200)
    head_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    findings: list[_DigestFinding] = Field(max_length=MAX_BASELINE_FINDINGS)


Identity = tuple[BehaviorCategory, str, str]


@dataclass(frozen=True)
class Baseline:
    """An earlier analysis reduced to what the comparison reads: each finding's risk and
    fingerprint, by identity."""

    analysis_id: str
    head_commit: str
    findings: dict[Identity, tuple[RiskLabel, str]]


def _identity(category: BehaviorCategory, path: str, symbol: str | None) -> Identity:
    # The key findings are grouped by within one analysis, so it names one finding in each.
    return category, path, symbol or "<module>"


def fingerprint(evidence: Iterable[_Evidence | Evidence]) -> str:
    """What changed, without where: line numbers move with unrelated edits above a symbol."""
    canonical = sorted(
        (item.kind, item.path, item.symbol or "", item.old, item.new) for item in evidence
    )
    encoded = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:FINGERPRINT_CHARS]


def _from_analysis(analysis: _Analysis) -> Baseline:
    findings: dict[Identity, tuple[RiskLabel, str]] = {}
    for item in analysis.behavior_changes:
        first = item.evidence[0]
        # A crafted baseline may repeat an identity; the first one stands for it.
        findings.setdefault(
            _identity(item.category, first.path, first.symbol),
            (item.risk, fingerprint(item.evidence)),
        )
    return Baseline(analysis.analysis_id, analysis.repository.head_commit, findings)


def _from_digest(digest: _Digest) -> Baseline:
    findings: dict[Identity, tuple[RiskLabel, str]] = {}
    for item in digest.findings:
        findings.setdefault(
            _identity(item.category, item.path, item.symbol), (item.risk, item.fingerprint)
        )
    return Baseline(digest.analysis_id, digest.head_commit, findings)


def _analysis(document: Any) -> _Analysis:
    # `both` wraps the analysis beside the brief; `json` is the analysis itself.
    if isinstance(document, dict) and isinstance(document.get("analysis"), dict):
        document = document["analysis"]
    if not isinstance(document, dict) or "behavior_changes" not in document:
        raise baseline_error(
            "The baseline holds no analysis; a `--format markdown` brief cannot be compared."
        )
    try:
        analysis = _Analysis.model_validate(document)
    except ValidationError as exc:
        raise baseline_error("The baseline analysis does not have the expected shape.") from exc
    if analysis.schema_version.split(".", 1)[0] != SUPPORTED_SCHEMA_MAJOR:
        raise baseline_error(
            f"The baseline analysis has schema version {analysis.schema_version!r}; only 1.x "
            "can be compared."
        )
    return analysis


def load_baseline(path: Path, limit: int) -> Baseline:
    """Read an earlier analysis, or the digest of one."""
    try:
        document: Any = json.loads(
            read_bounded_text(path, limit, "baseline analysis", baseline_error)
        )
    except (json.JSONDecodeError, RecursionError) as exc:
        raise baseline_error("The baseline analysis is not valid JSON.") from exc
    if isinstance(document, dict) and DIGEST_KEY in document:
        try:
            return _from_digest(_Digest.model_validate(document))
        except ValidationError as exc:
            raise baseline_error("The baseline digest does not have the expected shape.") from exc
    return _from_analysis(_analysis(document))


def digest_of(document: Any) -> dict[str, Any]:
    """The digest of an analysis, as `--format json` or `--format both` wrote it.

    It keeps no evidence text, only a fingerprint of it, so it is small enough for a
    pull-request comment and says no more than the brief beside it.
    """
    baseline = _from_analysis(_analysis(document))
    return {
        DIGEST_KEY: DIGEST_VERSION,
        "analysis_id": baseline.analysis_id,
        "head_commit": baseline.head_commit,
        "findings": [
            {
                "category": category.value,
                "path": path,
                "symbol": symbol,
                "risk": risk.value,
                "fingerprint": print_,
            }
            for (category, path, symbol), (risk, print_) in baseline.findings.items()
        ],
    }


def compare_with_baseline(
    baseline: Baseline, behaviors: list[BehaviorChange]
) -> BaselineComparison:
    comparison = BaselineComparison(
        analysis_id=baseline.analysis_id, head_commit=baseline.head_commit
    )
    seen: set[Identity] = set()
    for behavior in behaviors:
        first = behavior.evidence[0]
        identity = _identity(behavior.category, first.path, first.symbol)
        seen.add(identity)
        before = baseline.findings.get(identity)
        if before is None:
            comparison.new.append(behavior.id)
        elif before[1] == fingerprint(behavior.evidence):
            comparison.unchanged.append(behavior.id)
        else:
            comparison.updated.append(behavior.id)
    comparison.resolved.extend(
        BaselineFinding(category=identity[0], path=identity[1], symbol=identity[2], risk=risk)
        for identity, (risk, _) in baseline.findings.items()
        if identity not in seen
    )
    return comparison
