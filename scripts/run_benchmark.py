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

`propose` samples a repository's latest pull requests that change at most ten Python symbols,
and prints only how many symbols and files each changes, never a finding, so that labeling stays
blind to the analyzer.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.validate_real_prs import (  # noqa: E402
    PULL_REQUEST_SUBJECT,
    analyze,
    clone,
    run_git,
)
from semantic_diff_weaver.models import BehaviorCategory  # noqa: E402

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


def render(benchmark: Benchmark, scores: list[CaseScore], totals: Totals) -> str:
    scored = sum(totals.findings[name] for name in SCORED)
    lines = [
        "| Measure | Value |",
        "| --- | --- |",
        f"| Cases | {totals.cases} |",
        f"| Labeled symbols | {sum(len(case.labels) for case in benchmark.cases)}: "
        f"{totals.changes} behavior changes, {totals.unchanged} unchanged, and "
        f"{sum(len(case.labels) for case in benchmark.cases) - totals.changes - totals.unchanged}"
        " added or removed without a scored category |",
        f"| Findings on Python | {scored + totals.findings['unscored']} scored or on an added "
        f"symbol, {totals.findings['unlabeled']} on an unlabeled symbol |",
        f"| Precision, findings in an expected category | {_rate(totals.findings['correct'], scored)} |",
        f"| Precision, findings naming a behavior category | "
        f"{_rate(totals.named['correct'], sum(totals.named[name] for name in SCORED))} |",
        f"| Recall, behavior changes given a finding | {_rate(totals.detected, totals.changes)} |",
        f"| Recall, behavior changes given one in an expected category | "
        f"{_rate(totals.right, totals.changes)} |",
        f"| Unchanged symbols given a finding | {_rate(totals.flagged, totals.unchanged)} |",
        f"| Unchanged symbols given one naming a behavior category | "
        f"{_rate(totals.misnamed, totals.unchanged)} |",
        f"| Findings outside Python, not scored | {totals.findings['out_of_scope']} |",
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


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    default_scratch = os.environ.get(
        "SDW_SCRATCH", str(Path(tempfile.gettempdir()) / "semantic-diff-weaver-benchmark")
    )
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
    namespace = parser.parse_args(arguments)
    benchmark = load(Path(namespace.cases))
    scores = measure(benchmark, _scratch(namespace.scratch))
    print(render(benchmark, scores, total(benchmark, scores)))
    if namespace.disagreements:
        print("\n".join(disagreements(benchmark, scores)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
