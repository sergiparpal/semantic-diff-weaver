"""Measure the analyzer against the labeled public benchmark of real pull requests.

A developer tool, not part of the package and never run in CI, as `validate_real_prs.py` is: it
clones public repositories. `benchmark/cases.json` pins each case to a pull request of a public
repository by its two commits, and labels every Python symbol the pull request changes with the
categories a reviewer expects of it, or with none when its behavior does not change. The labels
were written from the diffs before the analyzer's output on them was read; this script compares
the two and never rewrites a label.

Read-only throughout, as `validate_real_prs.py` is: repositories are cloned without a checkout
into a scratch directory outside the working tree, nothing from them is installed or executed,
and the analyzer reads their Git objects.

    python scripts/run_benchmark.py --scratch /tmp/sdw-benchmark
    python scripts/run_benchmark.py propose --repo psf/black --count 6 --scratch /tmp/sdw-benchmark
    python scripts/run_benchmark.py llm --model MODEL --max-cost-usd 0.5 --scratch /tmp/sdw-bench

`propose` samples a repository's latest pull requests that change at most ten Python symbols,
and prints only how many symbols and files each changes, never a finding, so that labeling stays
blind to the analyzer.

`llm` measures the inference layer: it analyzes each development case twice, without a model and
with one behind an OpenAI-compatible endpoint (`openai_compatible.py`, OpenRouter's by default),
and compares the two. It sends repository code to that endpoint, so it refuses a repository GitHub
does not serve as public, never runs a held-out case, and needs a spending cap. A case counts only
if the model answered: it was called, no batch failed, and the analysis kept one behavior at least
of those the model named. The rest measure the deterministic fallback, and are listed apart.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
import tempfile
import time
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.openai_compatible import (  # noqa: E402
    DEFAULT_API_KEY_ENV,
    DEFAULT_BASE_URL,
    REASONING_EFFORTS,
    CallRecord,
    OpenAICompatibleClient,
)
from scripts.validate_real_prs import (  # noqa: E402
    PULL_REQUEST_SUBJECT,
    analyze,
    clone,
    run_git,
)
from semantic_diff_weaver import __version__  # noqa: E402
from semantic_diff_weaver.models import BehaviorCategory, LlmBatchResponse  # noqa: E402
from semantic_diff_weaver.path_policy import ALLOWED_ROOTS_ENV  # noqa: E402
from semantic_diff_weaver.semantic_interpreter import INPUT_PREFIX, INPUT_SUFFIX  # noqa: E402
from semantic_diff_weaver.service import analyze as analyze_service  # noqa: E402

CASES = REPOSITORY_ROOT / "benchmark" / "cases.json"
DEFAULT_MAX_SYMBOLS = 10
# How far back a repository's first-parent history is read for pull requests to sample.
MAX_HISTORY = 600
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
REPOSITORY_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
# The categories a label may expect: a reviewer names what a change does, so neither "no
# behavioral signal" nor "cannot be classified" is a label, and a change with none of the others
# is labeled with no category at all.
LABEL_CATEGORIES = frozenset(BehaviorCategory) - {
    BehaviorCategory.REFACTOR,
    BehaviorCategory.UNKNOWN,
}


class Label(BaseModel):
    """What a reviewer expects of one changed symbol."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    # As the analyzer names symbols: `Class.method`, `function.nested`, a class's own statements
    # as `Class`, and a module's top-level statements as `<module>`.
    symbol: str = Field(min_length=1)
    change: Literal["modified", "added", "removed"]
    # Any of these is the right category. None at all for a modified symbol means its behavior
    # does not change; for an added or removed one, that its category is not scored.
    expected: list[BehaviorCategory] = Field(default_factory=list)
    rationale: str = Field(min_length=1)

    @field_validator("expected")
    @classmethod
    def labeled_categories(cls, value: list[BehaviorCategory]) -> list[BehaviorCategory]:
        if len(set(value)) != len(value) or not set(value) <= LABEL_CATEGORIES:
            raise ValueError("expected categories must be distinct and name what a change does")
        return value


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    repository: str
    pull_request: int = Field(ge=1)
    base: str
    head: str
    subject: str = Field(min_length=1)
    # The analyzer is changed against the development cases; the held-out ones, labeled before
    # such a change, measure whether it holds on cases it was not made from.
    split: Literal["development", "held_out"]
    labels: list[Label] = Field(min_length=1)
    # The people who checked the labels after their first annotator, by name.
    reviewed_by: list[str] = Field(default_factory=list)

    @field_validator("repository")
    @classmethod
    def repository_slug(cls, value: str) -> str:
        if not REPOSITORY_RE.fullmatch(value):
            raise ValueError("repository must be an OWNER/NAME slug")
        return value

    @field_validator("base", "head")
    @classmethod
    def full_commit(cls, value: str) -> str:
        if not COMMIT_RE.fullmatch(value):
            raise ValueError("a commit must be pinned by its full 40-character SHA")
        return value

    @model_validator(mode="after")
    def distinct_symbols(self) -> Case:
        keys = [(label.path, label.symbol) for label in self.labels]
        if len(set(keys)) != len(keys):
            raise ValueError(f"{self.id} labels a symbol twice")
        if self.id != f"{self.repository}#{self.pull_request}":
            raise ValueError(f"{self.id} is not named OWNER/NAME#NUMBER")
        return self


class Benchmark(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    annotation: str = Field(min_length=1)
    # Per repository, the default branch's commit the sample was read back from.
    sampled_from: dict[str, str]
    cases: list[Case] = Field(min_length=1)

    @model_validator(mode="after")
    def distinct_cases(self) -> Benchmark:
        ids = [case.id for case in self.cases]
        if len(set(ids)) != len(ids):
            raise ValueError("a case is listed twice")
        if not {case.repository for case in self.cases} <= set(self.sampled_from):
            raise ValueError("every repository must say which commit it was sampled from")
        return self


def load(path: Path = CASES) -> Benchmark:
    return Benchmark.model_validate(json.loads(path.read_text(encoding="utf-8")))


Outcome = Literal[
    "correct", "wrong_category", "false_positive", "unscored", "unlabeled", "out_of_scope"
]
# The outcomes that make a finding right or wrong; the rest are counted apart.
SCORED: tuple[Outcome, ...] = ("correct", "wrong_category", "false_positive")


@dataclass(frozen=True)
class Finding:
    path: str
    symbol: str
    category: str
    # Read for the inference layer's measurement. A finding is matched by the three above.
    origin: str = field(default="", compare=False)
    confidence: float = field(default=0.0, compare=False)


@dataclass
class CaseScore:
    """How one case's findings stand against its labels."""

    case: str
    findings: list[tuple[Finding, Outcome]] = field(default_factory=list)
    # Per labeled symbol: whether any finding is on it, and whether one has an expected category.
    detected: dict[tuple[str, str], bool] = field(default_factory=dict)
    right: dict[tuple[str, str], bool] = field(default_factory=dict)


def findings_of(analysis: dict[str, Any]) -> list[Finding]:
    """Each finding of an analysis, by the path and symbol of its first evidence."""
    found = []
    for behavior in analysis.get("behavior_changes", []):
        evidence = behavior["evidence"][0]
        found.append(
            Finding(
                path=str(evidence["path"]),
                symbol=str(evidence.get("symbol") or "<module>"),
                category=str(behavior["category"]),
                origin=str(behavior.get("origin", "")),
                confidence=float(behavior.get("confidence", 0.0)),
            )
        )
    return found


def score(case: Case, findings: list[Finding]) -> CaseScore:
    """Mark each finding against the labels, and each label against the findings.

    A finding that says no behavior changed, `refactor_likely_no_behavior_change`, is no claim
    that one did, and is never a false positive. Only Python is labeled, so a finding on an API
    schema or a configuration file is out of scope.
    """
    labels = {(label.path, label.symbol): label for label in case.labels}
    result = CaseScore(case=case.id)
    for key in labels:
        result.detected[key] = False
        result.right[key] = False
    for finding in findings:
        key = (finding.path, finding.symbol)
        label = labels.get(key)
        claims = finding.category != BehaviorCategory.REFACTOR
        outcome: Outcome
        if not finding.path.endswith(".py"):
            outcome = "out_of_scope"
        elif label is None:
            outcome = "unlabeled"
        elif not label.expected:
            if label.change == "modified":
                outcome = "false_positive" if claims else "correct"
            else:
                outcome = "unscored"
        else:
            outcome = "correct" if finding.category in label.expected else "wrong_category"
        result.findings.append((finding, outcome))
        if label is not None and claims:
            result.detected[key] = True
            if finding.category in label.expected:
                result.right[key] = True
    return result


@dataclass
class Totals:
    cases: int = 0
    findings: Counter[str] = field(default_factory=Counter)
    # The scored findings that name a behavior category, by outcome: neither an unknown change
    # nor a refactor, which says no behavior changed.
    named: Counter[str] = field(default_factory=Counter)
    # Per category, the scored findings in it, by outcome.
    categories: dict[str, Counter[str]] = field(default_factory=dict)
    # Labeled symbols, by what their labels say.
    changes: int = 0
    unchanged: int = 0
    detected: int = 0
    right: int = 0
    flagged: int = 0
    # Unchanged symbols given a finding that names a behavior category.
    misnamed: int = 0


def total(benchmark: Benchmark, scores: list[CaseScore]) -> Totals:
    labels = {
        case.id: {(label.path, label.symbol): label for label in case.labels}
        for case in benchmark.cases
    }
    result = Totals(cases=len(scores))
    for case_score in scores:
        named_symbols = set()
        for finding, outcome in case_score.findings:
            result.findings[outcome] += 1
            if outcome not in SCORED:
                continue
            result.categories.setdefault(finding.category, Counter())[outcome] += 1
            if finding.category in LABEL_CATEGORIES:
                result.named[outcome] += 1
                named_symbols.add((finding.path, finding.symbol))
        for key, label in labels[case_score.case].items():
            if label.expected:
                result.changes += 1
                result.detected += case_score.detected[key]
                result.right += case_score.right[key]
            elif label.change == "modified":
                result.unchanged += 1
                result.flagged += case_score.detected[key]
                result.misnamed += key in named_symbols
    return result


def _rate(part: int, whole: int) -> str:
    return f"{part} of {whole} ({100 * part / whole:.1f}%)" if whole else "none"


def _measures(benchmark: Benchmark, totals: Totals) -> list[tuple[str, str]]:
    """The headline measures, by name, as `render` and `render_model` print them."""
    scored = sum(totals.findings[name] for name in SCORED)
    labeled = sum(len(case.labels) for case in benchmark.cases)
    return [
        ("Cases", str(totals.cases)),
        (
            "Labeled symbols",
            f"{labeled}: {totals.changes} behavior changes, {totals.unchanged} unchanged, and "
            f"{labeled - totals.changes - totals.unchanged} added or removed without a scored "
            "category",
        ),
        (
            "Findings on Python",
            f"{scored + totals.findings['unscored']} scored or on an added symbol, "
            f"{totals.findings['unlabeled']} on an unlabeled symbol",
        ),
        ("Precision, findings in an expected category", _rate(totals.findings["correct"], scored)),
        (
            "Precision, findings naming a behavior category",
            _rate(totals.named["correct"], sum(totals.named[name] for name in SCORED)),
        ),
        ("Recall, behavior changes given a finding", _rate(totals.detected, totals.changes)),
        (
            "Recall, behavior changes given one in an expected category",
            _rate(totals.right, totals.changes),
        ),
        ("Unchanged symbols given a finding", _rate(totals.flagged, totals.unchanged)),
        (
            "Unchanged symbols given one naming a behavior category",
            _rate(totals.misnamed, totals.unchanged),
        ),
        ("Findings outside Python, not scored", str(totals.findings["out_of_scope"])),
    ]


def render(benchmark: Benchmark, scores: list[CaseScore], totals: Totals) -> str:
    lines = ["| Measure | Value |", "| --- | --- |"]
    lines += [f"| {name} | {value} |" for name, value in _measures(benchmark, totals)]
    lines += [
        "",
        "| Category found | Findings | In an expected category | In another | On an unchanged symbol |",
        "| --- | --- | --- | --- | --- |",
    ]
    for category, outcomes in sorted(
        totals.categories.items(), key=lambda item: (-sum(item[1].values()), item[0])
    ):
        lines.append(
            f"| `{category}` | {sum(outcomes.values())} | {outcomes['correct']} "
            f"| {outcomes['wrong_category']} | {outcomes['false_positive']} |"
        )
    lines += [
        "",
        "| Case | Labels | Findings | Correct | Wrong category | False positive | Missed |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    labels = {case.id: case.labels for case in benchmark.cases}
    for case_score in scores:
        outcomes = Counter(outcome for _, outcome in case_score.findings)
        missed = sum(
            1
            for label in labels[case_score.case]
            if label.expected and not case_score.detected[(label.path, label.symbol)]
        )
        lines.append(
            f"| `{case_score.case}` | {len(labels[case_score.case])} "
            f"| {len(case_score.findings)} | {outcomes['correct']} "
            f"| {outcomes['wrong_category']} | {outcomes['false_positive']} | {missed} |"
        )
    return "\n".join(lines) + "\n"


def disagreements(benchmark: Benchmark, scores: list[CaseScore]) -> list[str]:
    """Every finding that is not correct and every behavior change not found, one per line."""
    lines = []
    cases = {case.id: case for case in benchmark.cases}
    for case_score in scores:
        labels = {(label.path, label.symbol): label for label in cases[case_score.case].labels}
        for finding, outcome in case_score.findings:
            if outcome in {"correct", "unscored", "out_of_scope"}:
                continue
            label = labels.get((finding.path, finding.symbol))
            expected = (
                ", ".join(label.expected) if label and label.expected else "no behavior change"
            )
            lines.append(
                f"{case_score.case} {finding.path} {finding.symbol}: {outcome}, "
                f"found {finding.category}, labeled {expected if label else 'nothing'}"
            )
        for key, label in labels.items():
            if label.expected and not case_score.detected[key]:
                lines.append(
                    f"{case_score.case} {label.path} {label.symbol}: missed, "
                    f"labeled {', '.join(label.expected)}"
                )
    return lines


def measure(benchmark: Benchmark, scratch: Path) -> list[CaseScore]:
    scores = []
    clones: dict[str, Path] = {}
    for case in benchmark.cases:
        repo = clones.get(case.repository) or clone(case.repository, scratch)
        if repo is None:
            raise RuntimeError(f"{case.repository} could not be cloned")
        clones[case.repository] = repo
        envelope = analyze(repo, case.base, case.head)
        if envelope is None:
            raise RuntimeError(f"{case.id} could not be analyzed")
        scores.append(score(case, findings_of(envelope["analysis"])))
        print(f"measured {case.id}", file=sys.stderr)
    return scores


# Measuring the inference layer with a model.

GITHUB_REPOSITORIES = "https://api.github.com/repos/"
UNKNOWN = BehaviorCategory.UNKNOWN.value
# Whether a case's analysis with the model counts. Only a valid one does: the model was called, no
# batch failed, and the analysis kept one behavior at least of those the model named, as a finding
# it supports or adds, reported or hidden. Without a call there was nothing to send the model; the
# rest measure the deterministic fallback, or a model that said nothing the analysis kept.
Validity = Literal["valid", "no_call", "failed", "nothing_kept", "not_run"]


def public_repository(slug: str) -> bool:
    """Whether GitHub serves `slug` as a public repository to a request without credentials."""
    request = urllib.request.Request(  # noqa: S310 - a fixed HTTPS URL
        GITHUB_REPOSITORIES + slug,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "semantic-diff-weaver-benchmark",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - as above
            document = json.loads(response.read(1_000_000))
    except (OSError, ValueError):
        return False
    return isinstance(document, dict) and document.get("private") is False


@dataclass
class Replies:
    """What a model said, read from its replies before the analysis judged them."""

    # Replies the response schema rejects, which the analysis discards whole.
    invalid_replies: int = 0
    behaviors: int = 0
    obligations: int = 0
    # Behaviors the analysis discards, by the first of its rules each one breaks.
    repeated_reference: int = 0
    fabricated_reference: int = 0
    other_batch_reference: int = 0
    # Behaviors it keeps, as the finding of theirs it supports or as a new one.
    kept: int = 0
    # Kept behaviors whose category no deterministic finding on their evidence has, so the analysis
    # makes each an unknown change.
    unsupported_category: int = 0
    # Evidence IDs cited that the model was never shown during the analysis.
    fabricated_ids: int = 0

    def add(self, other: Replies) -> None:
        for name, value in asdict(other).items():
            setattr(self, name, getattr(self, name) + value)


def _value(result: Any, name: str) -> Any:
    return result.get(name) if isinstance(result, dict) else getattr(result, name, None)


def _evidence_groups(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The evidence groups of a call's input, as `semantic_interpreter` frames them."""
    groups: list[dict[str, Any]] = []
    for item in items:
        text = str(item.get("text", ""))
        if not (text.startswith(INPUT_PREFIX) and text.endswith(INPUT_SUFFIX)):
            continue
        try:
            document = json.loads(text[len(INPUT_PREFIX) : len(text) - len(INPUT_SUFFIX)])
        except ValueError:
            continue
        found = document.get("evidence_groups") if isinstance(document, dict) else None
        groups += [group for group in found or () if isinstance(group, dict)]
    return groups


class RecordingClient:
    """Hands each call to a model, and keeps what the model was shown and what it answered."""

    def __init__(self, model: Any) -> None:
        self.model = model
        # Per evidence ID the model was shown, the fields that identify the record.
        self.shown: dict[str, dict[str, Any]] = {}
        # Per answered call, the category hint of each evidence ID it showed, and the reply.
        self._answers: list[tuple[dict[str, Any], Any]] = []

    def complete_structured(self, **request: Any) -> Any:
        hints: dict[str, Any] = {}
        for group in _evidence_groups(request.get("input", [])):
            for evidence in group.get("evidence", []):
                if isinstance(evidence, dict) and isinstance(evidence.get("id"), str):
                    hints[evidence["id"]] = group.get("category_hint")
                    self.shown[evidence["id"]] = {
                        key: evidence[key] for key in ("path", "symbol", "kind") if key in evidence
                    }
        result = self.model.complete_structured(**request)
        self._answers.append((hints, _value(result, "parsed")))
        return result

    def replies(self) -> Replies:
        """Every reply's behaviors, judged as the analysis judges them, against all it showed."""
        replies = Replies()
        for hints, parsed in self._answers:
            if isinstance(parsed, BaseModel):
                parsed = parsed.model_dump(mode="json")
            try:
                reply = LlmBatchResponse.model_validate(parsed)
            except ValidationError:
                replies.invalid_replies += 1
                continue
            replies.obligations += len(reply.obligations)
            for behavior in reply.behaviors:
                replies.behaviors += 1
                self._judge(behavior.category.value, list(behavior.evidence_ids), hints, replies)
        return replies

    def _judge(
        self, category: str, references: list[str], hints: dict[str, Any], replies: Replies
    ) -> None:
        unseen = {item for item in references if item not in self.shown}
        replies.fabricated_ids += len(unseen)
        if len(set(references)) != len(references):
            replies.repeated_reference += 1
        elif unseen:
            replies.fabricated_reference += 1
        elif not set(references) <= hints.keys():
            replies.other_batch_reference += 1
        else:
            replies.kept += 1
            if category != UNKNOWN and category not in {hints[item] for item in references}:
                replies.unsupported_category += 1


def unanchored(
    model: dict[str, Any], deterministic: dict[str, Any], shown: dict[str, dict[str, Any]]
) -> int:
    """Evidence in the findings of the run with the model that the analysis did not extract.

    An evidence record is anchored when it is the run without the model's record under its ID,
    or, for a finding that run did not report, has the fields the model was shown under it.
    """
    extracted = {
        item["id"]: item
        for behavior in deterministic["behavior_changes"]
        for item in behavior["evidence"]
    }
    count = 0
    for behavior in model["behavior_changes"]:
        for item in behavior["evidence"]:
            if item["id"] in extracted:
                count += item != extracted[item["id"]]
            elif item["id"] in shown:
                count += any(item.get(key) != value for key, value in shown[item["id"]].items())
            else:
                count += 1
    return count


@dataclass
class ModelCase:
    """One development case, analyzed without a model and with one."""

    case: Case
    validity: Validity
    deterministic: dict[str, Any] = field(default_factory=dict)
    model: dict[str, Any] = field(default_factory=dict)
    seconds: float = 0.0
    calls: list[CallRecord] = field(default_factory=list)
    replies: Replies = field(default_factory=Replies)
    unanchored: int = 0


@contextmanager
def _authorized(repo: Path) -> Iterator[None]:
    """Authorize the analysis of `repo` alone, as `validate_real_prs.analyze` does."""
    previous = os.environ.get(ALLOWED_ROOTS_ENV)
    os.environ[ALLOWED_ROOTS_ENV] = str(repo.resolve())
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(ALLOWED_ROOTS_ENV, None)
        else:
            os.environ[ALLOWED_ROOTS_ENV] = previous


def _analyze_case(repo: Path, case: Case, llm: Any) -> dict[str, Any]:
    with _authorized(repo):
        return analyze_service(
            {
                "repo_path": str(repo),
                "base_ref": case.base,
                "head_ref": case.head,
                "output_format": "json",
            },
            llm=llm,
        )


def validity_of(analysis: dict[str, Any], replies: Replies) -> Validity:
    status = analysis["llm"]
    if not status["attempted"]:
        return "no_call"
    if not status["available"] or status["failures"]:
        return "failed"
    if not replies.kept:
        return "nothing_kept"
    return "valid"


def _exhausted(model: Any) -> bool:
    cap = getattr(model, "max_cost_usd", None)
    return cap is not None and getattr(model, "spent", 0.0) >= cap


def measure_with_model(
    benchmark: Benchmark,
    scratch: Path,
    model: Any,
    *,
    public: Callable[[str], bool] | None = None,
) -> list[ModelCase]:
    """Analyze each case without the model and with it, until the model's spending cap stops it.

    No repository is read before each one is confirmed public, since its code goes to the model.
    """
    is_public = public or public_repository
    for slug in sorted({case.repository for case in benchmark.cases}):
        if not is_public(slug):
            raise RuntimeError(f"{slug} is not a public repository on GitHub; nothing was sent")
    runs: list[ModelCase] = []
    clones: dict[str, Path] = {}
    for case in benchmark.cases:
        if _exhausted(model) or any(
            call.outcome == "refused_budget" for run in runs for call in run.calls
        ):
            runs.append(ModelCase(case=case, validity="not_run"))
            continue
        repo = clones.get(case.repository) or clone(case.repository, scratch)
        if repo is None:
            raise RuntimeError(f"{case.repository} could not be cloned")
        clones[case.repository] = repo
        deterministic = _analyze_case(repo, case, None)
        recorder = RecordingClient(model)
        before = len(getattr(model, "calls", ()))
        started = time.perf_counter()
        analysis = _analyze_case(repo, case, recorder)
        seconds = time.perf_counter() - started
        replies = recorder.replies()
        calls = [
            call
            for call in list(getattr(model, "calls", ()))[before:]
            if isinstance(call, CallRecord)
        ]
        runs.append(
            ModelCase(
                case=case,
                validity=validity_of(analysis, replies),
                deterministic=deterministic,
                model=analysis,
                seconds=seconds,
                calls=calls,
                replies=replies,
                unanchored=unanchored(analysis, deterministic, recorder.shown),
            )
        )
        print(f"measured {case.id} with the model: {runs[-1].validity}", file=sys.stderr)
    return runs


def _median_max(values: list[float]) -> str:
    if not values:
        return "none"
    return f"{statistics.median(values):.1f} median, {max(values):.1f} at most"


def _mean_confidence(scores: list[CaseScore], outcome: Outcome) -> str:
    values = [
        finding.confidence
        for case_score in scores
        for finding, found in case_score.findings
        if found == outcome
    ]
    return f"{statistics.fmean(values):.2f} over {len(values)}" if values else "none"


def _supported(analysis: dict[str, Any]) -> int:
    return sum(item["origin"] == "llm_supported" for item in analysis.get("behavior_changes", ()))


def render_model(
    benchmark: Benchmark, runs: list[ModelCase], *, model: str, cap: float, spent: float
) -> str:
    """The valid cases' measures without the model and with it, then what the model did."""
    valid = [run for run in runs if run.validity == "valid"]
    measured = [run for run in runs if run.validity != "not_run"]
    subset = benchmark.model_copy(update={"cases": [run.case for run in valid]})
    without = [score(run.case, findings_of(run.deterministic)) for run in valid]
    with_model = [score(run.case, findings_of(run.model)) for run in valid]
    lines = [
        f"| Measure, on the {len(valid)} valid cases | Without a model | With {model} |",
        "| --- | --- | --- |",
    ]
    for (name, before), (_, after) in zip(
        _measures(subset, total(subset, without)),
        _measures(subset, total(subset, with_model)),
        strict=True,
    ):
        lines.append(f"| {name} | {before} | {after} |")
    for label, outcome in (
        ("in an expected category", "correct"),
        ("in another category", "wrong_category"),
        ("on an unchanged symbol", "false_positive"),
    ):
        lines.append(
            f"| Mean confidence, findings {label} | {_mean_confidence(without, outcome)} "
            f"| {_mean_confidence(with_model, outcome)} |"
        )
    lines.append(
        f"| Test obligations | {sum(len(run.deterministic['test_obligations']) for run in valid)} "
        f"| {sum(len(run.model['test_obligations']) for run in valid)} |"
    )
    added = removed = 0
    for run in valid:
        before_found = Counter(findings_of(run.deterministic))
        after_found = Counter(findings_of(run.model))
        added += sum((after_found - before_found).values())
        removed += sum((before_found - after_found).values())
    replies = Replies()
    for run in measured:
        replies.add(run.replies)
    calls = [call for run in measured for call in run.calls]
    outcomes = Counter(call.outcome for call in calls)
    counts = Counter(run.validity for run in runs)
    estimated = " (partly estimated from tokens)" if any(call.estimated for call in calls) else ""
    supported = sum(_supported(run.model) for run in valid)
    reported = sum(len(run.model["behavior_changes"]) for run in valid)
    lines += [
        "",
        "| What the model did | Value |",
        "| --- | --- |",
        f"| Cases | {len(runs)}: {counts['valid']} valid, {counts['no_call']} with nothing to send "
        f"the model, {counts['failed']} with a failed batch, {counts['nothing_kept']} where the "
        f"analysis kept nothing the model said, {counts['not_run']} not run for the cap |",
        f"| Findings the model supported, valid cases | {_rate(supported, reported)} |",
        f"| Findings only the run with the model has, valid cases | {added} |",
        f"| Findings only the run without it has, valid cases | {removed} |",
        f"| Replies the response schema rejects, every case | {replies.invalid_replies} |",
        f"| Behaviors in the other replies | {replies.behaviors}, and {replies.obligations} "
        "obligations |",
        f"| Behaviors the analysis kept | {replies.kept} |",
        f"| Behaviors discarded for citing evidence never shown | {replies.fabricated_reference} |",
        f"| Evidence IDs cited that were never shown | {replies.fabricated_ids} |",
        "| Behaviors discarded for citing another batch's evidence | "
        f"{replies.other_batch_reference} |",
        f"| Behaviors discarded for citing evidence twice | {replies.repeated_reference} |",
        "| Behaviors kept in a category no evidence supports, made unknown | "
        f"{replies.unsupported_category} |",
        "| Evidence in the output the analysis did not extract | "
        f"{sum(run.unanchored for run in measured)} |",
        f"| Calls | {len(calls)}"
        + (
            ": " + ", ".join(f"{name} {count}" for name, count in sorted(outcomes.items()))
            if calls
            else ""
        )
        + " |",
        f"| Tokens | {sum(call.input_tokens or 0 for call in calls)} in, "
        f"{sum(call.output_tokens or 0 for call in calls)} out, of which "
        f"{sum(call.reasoning_tokens or 0 for call in calls)} reasoning |",
        f"| Cost | {spent:.4f} USD of a {cap:g} USD cap{estimated} |",
        f"| Seconds per call | {_median_max([call.seconds for call in calls])} |",
        "| Seconds per analysis with the model | "
        f"{_median_max([run.seconds for run in measured])} |",
        "",
        "| Case | Counted | Calls | Findings without and with | Supported | Kept | Discarded "
        "| USD | Seconds |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for run in runs:
        if run.validity == "not_run":
            lines.append(f"| `{run.case.id}` | not_run | | | | | | | |")
            continue
        discarded = (
            run.replies.repeated_reference
            + run.replies.fabricated_reference
            + run.replies.other_batch_reference
        )
        lines.append(
            f"| `{run.case.id}` | {run.validity} | {run.model['llm']['calls']} "
            f"| {len(run.deterministic['behavior_changes'])}, {len(run.model['behavior_changes'])} "
            f"| {_supported(run.model)} | {run.replies.kept} | {discarded} "
            f"| {sum(call.cost or 0.0 for call in run.calls):.4f} | {run.seconds:.1f} |"
        )
    return "\n".join(lines) + "\n"


def model_report(
    runs: list[ModelCase],
    *,
    model: str,
    endpoint: str,
    options: dict[str, Any],
    cap: float,
    spent: float,
) -> dict[str, Any]:
    """Everything a run measured, per case and per call, but no repository content."""
    cases = []
    for run in runs:
        findings = {
            name: [
                {
                    "path": finding.path,
                    "symbol": finding.symbol,
                    "category": finding.category,
                    "origin": finding.origin,
                    "confidence": finding.confidence,
                    "outcome": outcome,
                }
                for finding, outcome in score(run.case, findings_of(analysis)).findings
            ]
            for name, analysis in (("without_model", run.deterministic), ("with_model", run.model))
            if analysis
        }
        cases.append(
            {
                "id": run.case.id,
                "validity": run.validity,
                "seconds": round(run.seconds, 3),
                "llm": run.model.get("llm"),
                "calls": [asdict(call) for call in run.calls if is_dataclass(call)],
                "replies": asdict(run.replies),
                "unanchored": run.unanchored,
                "findings": findings,
            }
        )
    return {
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "commit": run_git(REPOSITORY_ROOT, "rev-parse", "HEAD").strip(),
        "version": __version__,
        "model": model,
        "endpoint": endpoint,
        "options": options,
        "cap_usd": cap,
        "spent_usd": spent,
        "cases": cases,
    }


def write_model_run(directory: Path, runs: list[ModelCase], report: dict[str, Any]) -> Path:
    """Save the report and each case's two analyses, for reading the model's wording later."""
    directory.mkdir(parents=True, exist_ok=True)
    for run in runs:
        stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", run.case.id)
        for name, analysis in (("without-model", run.deterministic), ("with-model", run.model)):
            if analysis:
                (directory / f"{stem}.{name}.json").write_text(
                    json.dumps(analysis, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
                )
    path = directory / "report.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def candidates(repo: Path, count: int, max_symbols: int) -> list[dict[str, Any]]:
    """The latest pull requests of the default branch that change at most `max_symbols` Python
    symbols outside the tests, by the analyzer's own count; a finding is never shown."""
    raw = run_git(
        repo,
        "log",
        "--first-parent",
        f"--max-count={MAX_HISTORY}",
        "--pretty=format:%H%x1f%P%x1f%s",
    )
    found: list[dict[str, Any]] = []
    for line in raw.splitlines():
        commit, parents, subject = line.split("\x1f")
        named = PULL_REQUEST_SUBJECT.search(subject)
        if named is None or len(parents.split()) not in (1, 2):
            continue
        first, *rest = parents.split()
        base, head = (
            (first, commit)
            if not rest
            else (run_git(repo, "merge-base", first, rest[0]).strip(), rest[0])
        )
        envelope = analyze(repo, base, head)
        if envelope is None:
            continue
        scope = envelope["analysis"]["scope"]
        python = [path for path in scope["analyzed_files"] if path.endswith(".py")]
        if not python or scope["truncated"] or not 1 <= scope["changed_symbols"] <= max_symbols:
            continue
        found.append(
            {
                "pull_request": int(next(group for group in named.groups() if group)),
                "base": base,
                "head": head,
                "subject": subject,
                "changed_symbols": scope["changed_symbols"],
                "python_files": python,
            }
        )
        if len(found) >= count:
            break
    return found


def _scratch(value: str) -> Path:
    scratch = Path(value).expanduser().resolve()
    if REPOSITORY_ROOT in scratch.parents or scratch == REPOSITORY_ROOT:
        raise SystemExit("run_benchmark: --scratch must be outside this working tree")
    scratch.mkdir(parents=True, exist_ok=True)
    return scratch


def _number(minimum: float, *, inclusive: bool) -> Callable[[str], float]:
    def parse(value: str) -> float:
        try:
            number = float(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{value!r} is not a number") from None
        if not math.isfinite(number) or number < minimum or (number == minimum and not inclusive):
            bound = "at least" if inclusive else "more than"
            raise argparse.ArgumentTypeError(f"{value!r} must be {bound} {minimum:g}")
        return number

    return parse


def _count(value: str) -> int:
    if not value.isdigit() or int(value) < 1:
        raise argparse.ArgumentTypeError(f"{value!r} must be a positive whole number")
    return int(value)


def _main_model(arguments: list[str], default_scratch: str) -> int:
    parser = argparse.ArgumentParser(
        prog="run_benchmark.py llm",
        description="Measure the development cases without a model and with one.",
    )
    parser.add_argument("--model", required=True, help="the model's name at the endpoint")
    parser.add_argument(
        "--max-cost-usd",
        type=_number(0, inclusive=False),
        required=True,
        metavar="USD",
        help="make no call once the calls so far cost this much",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, metavar="URL")
    parser.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV, metavar="NAME")
    parser.add_argument(
        "--no-temperature",
        action="store_true",
        help="send no temperature, which some models reject",
    )
    parser.add_argument("--reasoning-effort", choices=REASONING_EFFORTS)
    parser.add_argument(
        "--usd-per-mtok",
        nargs=2,
        type=_number(0, inclusive=True),
        metavar=("INPUT", "OUTPUT"),
        help="estimate a call's cost from its tokens where the endpoint reports none",
    )
    parser.add_argument(
        "--case", action="append", default=[], metavar="OWNER/NAME#NUMBER", help="measure it alone"
    )
    parser.add_argument("--limit", type=_count, metavar="N", help="measure the first N cases")
    parser.add_argument("--cases", default=str(CASES), metavar="PATH")
    parser.add_argument("--scratch", default=default_scratch, metavar="DIR")
    namespace = parser.parse_args(arguments)
    benchmark = load(Path(namespace.cases))
    unknown = sorted(set(namespace.case) - {case.id for case in benchmark.cases})
    if unknown:
        parser.error(f"no such case: {', '.join(unknown)}")
    chosen = [case for case in benchmark.cases if not namespace.case or case.id in namespace.case]
    held_out = [case.id for case in chosen if case.split != "development" and namespace.case]
    if held_out:
        parser.error(f"a held-out case is never measured with a model: {', '.join(held_out)}")
    chosen = [case for case in chosen if case.split == "development"][: namespace.limit]
    scratch = _scratch(namespace.scratch)
    options = {
        "temperature": not namespace.no_temperature,
        "reasoning_effort": namespace.reasoning_effort,
        "usd_per_million_tokens": namespace.usd_per_mtok,
    }
    try:
        model = OpenAICompatibleClient.from_environment(
            api_key_env=namespace.api_key_env,
            model=namespace.model,
            base_url=namespace.base_url,
            send_temperature=not namespace.no_temperature,
            reasoning_effort=namespace.reasoning_effort,
            max_cost_usd=namespace.max_cost_usd,
            usd_per_million_tokens=(
                tuple(namespace.usd_per_mtok) if namespace.usd_per_mtok else None
            ),
        )
    except ValueError as exc:
        print(f"run_benchmark: {exc}", file=sys.stderr)
        return 2
    subset = benchmark.model_copy(update={"cases": chosen})
    try:
        runs = measure_with_model(subset, scratch, model)
    except RuntimeError as exc:
        print(f"run_benchmark: {exc}", file=sys.stderr)
        return 1
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    directory = (
        scratch / "model-runs" / f"{stamp}-{re.sub(r'[^A-Za-z0-9_.-]+', '-', namespace.model)}"
    )
    report = model_report(
        runs,
        model=namespace.model,
        endpoint=urlsplit(model.url).netloc,
        options=options,
        cap=namespace.max_cost_usd,
        spent=model.spent,
    )
    path = write_model_run(directory, runs, report)
    print(
        render_model(
            subset, runs, model=namespace.model, cap=namespace.max_cost_usd, spent=model.spent
        )
    )
    print(f"report: {path}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    default_scratch = os.environ.get(
        "SDW_SCRATCH", str(Path(tempfile.gettempdir()) / "semantic-diff-weaver-benchmark")
    )
    if arguments[:1] == ["llm"]:
        return _main_model(arguments[1:], default_scratch)
    if arguments[:1] == ["propose"]:
        parser = argparse.ArgumentParser(prog="run_benchmark.py propose")
        parser.add_argument("--repo", action="append", required=True, metavar="OWNER/NAME")
        parser.add_argument("--count", type=int, default=6)
        parser.add_argument("--max-symbols", type=int, default=DEFAULT_MAX_SYMBOLS)
        parser.add_argument("--scratch", default=default_scratch, metavar="DIR")
        namespace = parser.parse_args(arguments[1:])
        scratch = _scratch(namespace.scratch)
        proposed = {}
        for slug in namespace.repo:
            repo = clone(slug, scratch)
            if repo is None:
                return 1
            proposed[slug] = {
                "sampled_from": run_git(repo, "rev-parse", "HEAD").strip(),
                "candidates": candidates(repo, namespace.count, namespace.max_symbols),
            }
        print(json.dumps(proposed, indent=2))
        return 0
    parser = argparse.ArgumentParser(prog="run_benchmark.py")
    parser.add_argument("--cases", default=str(CASES), metavar="PATH")
    parser.add_argument("--scratch", default=default_scratch, metavar="DIR")
    parser.add_argument("--disagreements", action="store_true", help="also list each one")
    parser.add_argument(
        "--split", choices=("development", "held_out"), help="measure one split only"
    )
    namespace = parser.parse_args(arguments)
    benchmark = load(Path(namespace.cases))
    if namespace.split is not None:
        benchmark = benchmark.model_copy(
            update={"cases": [case for case in benchmark.cases if case.split == namespace.split]}
        )
    scores = measure(benchmark, _scratch(namespace.scratch))
    print(render(benchmark, scores, total(benchmark, scores)))
    if namespace.disagreements:
        print("\n".join(disagreements(benchmark, scores)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
