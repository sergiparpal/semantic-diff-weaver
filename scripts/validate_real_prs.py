"""Run the analyzer over real merged pull requests and report what it says.

A developer tool, not part of the package and never run in CI. The evaluation corpus is 17
synthetic cases and `docs/evaluation.md` says outright that it is not a claim about arbitrary
repositories. Before anyone enables a PR bot, someone should read its output on real diffs —
findings-per-PR is what predicts whether the bot stays enabled or gets muted.

**This measures nothing.** Real PRs here are unlabeled, so the output is observations, not
precision. Do not add these cases to the golden corpus.

Read-only throughout: repositories are shallow-cloned into a scratch directory outside the
working tree, nothing from them is checked out into this repository, nothing is installed, and
nothing is executed. Analysis reads Git objects, which is all `git_diff/repository.py` does.

    python scripts/validate_real_prs.py --repo psf/requests --repo pallets/click --count 5
    python scripts/validate_real_prs.py --local /path/to/repo --count 5
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from semantic_diff_weaver.cli import main as cli_main  # noqa: E402
from semantic_diff_weaver.path_policy import ALLOWED_ROOTS_ENV  # noqa: E402

GIT_TIMEOUT = 600
DEFAULT_COUNT = 5


@dataclass
class PullRequestResult:
    """One analyzed merge, or the reason it was skipped."""

    repository: str
    identifier: str
    subject: str = ""
    findings: int = 0
    obligations: int = 0
    categories: dict[str, int] = field(default_factory=dict)
    risks: dict[str, int] = field(default_factory=dict)
    overall_risk: str = ""
    mean_confidence: float = 0.0
    changed_files: int = 0
    seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    skipped: str = ""


def run_git(repo: Path, *arguments: str) -> str:
    """Argument lists only, `shell=False`, bounded — the same rule as `git_diff/process.py`."""
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("git is not available on PATH")
    completed = subprocess.run(
        [executable, *arguments],
        cwd=repo,
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=GIT_TIMEOUT,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git {arguments[0]} failed with status {completed.returncode}")
    return completed.stdout


def clone(slug: str, destination: Path) -> Path | None:
    """Shallow-clone a public repository into the scratch directory. None on any failure."""
    target = destination / slug.replace("/", "__")
    if target.exists():
        return target
    executable = shutil.which("git")
    if executable is None:
        return None
    # Full history with every object present, and `--no-checkout` so no analyzed file is
    # ever written to a working tree. A shallow or blobless clone cannot be used: a merge's
    # parents fall past the graft boundary, and `git_diff/repository.py` disables lazy object
    # fetching by design, so a missing blob is a hard failure rather than a silent refetch.
    completed = subprocess.run(
        [
            executable,
            "clone",
            "--no-checkout",
            "--single-branch",
            f"https://github.com/{slug}.git",
            str(target),
        ],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=GIT_TIMEOUT,
    )
    if completed.returncode != 0:
        print(f"  unreachable: {slug} (git clone status {completed.returncode})", file=sys.stderr)
        return None
    return target


def merge_commits(repo: Path, count: int) -> list[tuple[str, str, str]]:
    """Return (base, head, subject) for recent two-parent merges — a merged PR's own range."""
    raw = run_git(
        repo,
        "log",
        "--merges",
        f"--max-count={count * 4}",
        "--pretty=format:%H%x1f%P%x1f%s",
    )
    found: list[tuple[str, str, str]] = []
    for line in raw.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 3:
            continue
        parents = parts[1].split()
        if len(parents) != 2:
            continue
        found.append((parents[0], parents[1], parts[2]))
        if len(found) >= count:
            break
    return found


def analyze(repo: Path, base: str, head: str) -> dict[str, Any] | None:
    """Run the analyzer through its public CLI and capture the JSON envelope."""
    output = repo.parent / f"{repo.name}-{head[:12]}.json"
    previous = os.environ.get(ALLOWED_ROOTS_ENV)
    os.environ[ALLOWED_ROOTS_ENV] = str(repo.resolve())
    try:
        with output.open("w", encoding="utf-8") as stream:
            saved, sys.stdout = sys.stdout, stream
            try:
                code = cli_main(
                    [
                        "--repo",
                        str(repo),
                        "--base",
                        base,
                        "--head",
                        head,
                        "--format",
                        "both",
                        "--no-llm",
                    ]
                )
            finally:
                sys.stdout = saved
    finally:
        if previous is None:
            os.environ.pop(ALLOWED_ROOTS_ENV, None)
        else:
            os.environ[ALLOWED_ROOTS_ENV] = previous
    if code not in (0, 3):
        return None
    try:
        return json.loads(output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def measure(repository: str, repo: Path, base: str, head: str, subject: str) -> PullRequestResult:
    result = PullRequestResult(repository=repository, identifier=head[:12], subject=subject[:120])
    started = time.perf_counter()
    envelope = analyze(repo, base, head)
    result.seconds = round(time.perf_counter() - started, 3)
    if envelope is None:
        result.skipped = "analysis error"
        return result
    analysis = envelope.get("analysis", {})
    behaviors = analysis.get("behavior_changes", [])
    result.findings = len(behaviors)
    result.obligations = len(analysis.get("test_obligations", []))
    result.overall_risk = str(analysis.get("summary", {}).get("overall_risk", ""))
    result.changed_files = int(analysis.get("summary", {}).get("changed_files", 0))
    for behavior in behaviors:
        category = str(behavior.get("category", "?"))
        risk = str(behavior.get("risk", "?"))
        result.categories[category] = result.categories.get(category, 0) + 1
        result.risks[risk] = result.risks.get(risk, 0) + 1
    confidences = [float(item.get("confidence", 0.0)) for item in behaviors]
    result.mean_confidence = round(statistics.fmean(confidences), 3) if confidences else 0.0
    result.warnings = [str(item) for item in analysis.get("warnings", [])]
    result.limitations = [str(item) for item in analysis.get("limitations", [])]
    return result


def collect(
    slugs: list[str], locals_: list[str], count: int, scratch: Path
) -> tuple[list[PullRequestResult], list[str]]:
    results: list[PullRequestResult] = []
    unreachable: list[str] = []
    sources: list[tuple[str, Path]] = []
    for slug in slugs:
        cloned = clone(slug, scratch)
        if cloned is None:
            unreachable.append(slug)
            continue
        sources.append((slug, cloned))
    for path in locals_:
        resolved = Path(path).expanduser().resolve()
        if not (resolved / ".git").exists():
            unreachable.append(f"{path} (not a Git repository)")
            continue
        sources.append((resolved.name, resolved))

    for name, repo in sources:
        print(f"analyzing {name} ...", file=sys.stderr)
        try:
            merges = merge_commits(repo, count)
        except (RuntimeError, subprocess.SubprocessError) as exc:
            unreachable.append(f"{name} ({type(exc).__name__})")
            continue
        if not merges:
            unreachable.append(f"{name} (no two-parent merges found)")
            continue
        for base, head, subject in merges:
            results.append(measure(name, repo, base, head, subject))
    return results, unreachable


def render(results: list[PullRequestResult], unreachable: list[str], count: int) -> str:
    analyzed = [item for item in results if not item.skipped]
    findings = [item.findings for item in analyzed]
    categories: dict[str, int] = {}
    risks: dict[str, int] = {}
    for item in analyzed:
        for key, value in item.categories.items():
            categories[key] = categories.get(key, 0) + value
        for key, value in item.risks.items():
            risks[key] = risks.get(key, 0) + value
    confidences = [item.mean_confidence for item in analyzed if item.findings]
    seconds = [item.seconds for item in analyzed]

    lines = [
        "# Real pull-request validation",
        "",
        f"Generated {time.strftime('%Y-%m-%d')} by `scripts/validate_real_prs.py`, "
        f"deterministic mode (`--no-llm`), up to {count} recent merges per repository.",
        "",
        "**These are observations on unlabeled data, not precision measurements.** No one has",
        "reviewed these diffs to the standard the golden corpus requires, so nothing here is a",
        "true-positive or false-positive rate. Real-PR cases are deliberately *not* added to the",
        "golden corpus. The measured numbers live in `docs/evaluation.md`.",
        "",
        "## Aggregate",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Repositories analyzed | {len({item.repository for item in analyzed})} |",
        f"| Merges analyzed | {len(analyzed)} |",
        f"| Merges skipped | {len(results) - len(analyzed)} |",
        f"| Findings per PR (mean) | {statistics.fmean(findings):.2f} |"
        if findings
        else "| Findings per PR (mean) | n/a |",
        f"| Findings per PR (median) | {statistics.median(findings):.1f} |"
        if findings
        else "| Findings per PR (median) | n/a |",
        f"| Findings per PR (max) | {max(findings)} |"
        if findings
        else "| Findings per PR (max) | n/a |",
        f"| PRs with zero findings | {sum(1 for value in findings if value == 0)} |",
        f"| Mean confidence | {statistics.fmean(confidences):.3f} |"
        if confidences
        else "| Mean confidence | n/a |",
        f"| Wall clock per PR (mean) | {statistics.fmean(seconds):.2f}s |"
        if seconds
        else "| Wall clock per PR (mean) | n/a |",
        f"| Wall clock per PR (max) | {max(seconds):.2f}s |"
        if seconds
        else "| Wall clock per PR (max) | n/a |",
        "",
        "## Category distribution",
        "",
        "| Category | Count |",
        "| --- | --- |",
    ]
    for key, value in sorted(categories.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| `{key}` | {value} |")
    lines += ["", "## Risk distribution", "", "| Risk | Count |", "| --- | --- |"]
    for key in ("critical", "high", "medium", "low"):
        if key in risks:
            lines.append(f"| {key} | {risks[key]} |")

    lines += [
        "",
        "## Per-merge detail",
        "",
        "| Repository | Merge | Files | Findings | Obligations | Overall risk | Seconds |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in results:
        if item.skipped:
            lines.append(
                f"| {item.repository} | `{item.identifier}` | — | — | — | skipped: {item.skipped} | {item.seconds} |"
            )
            continue
        lines.append(
            f"| {item.repository} | `{item.identifier}` | {item.changed_files} | {item.findings} "
            f"| {item.obligations} | {item.overall_risk} | {item.seconds} |"
        )

    if unreachable:
        lines += ["", "## Unreachable", ""]
        lines += [f"- {item}" for item in unreachable]

    lines += [
        "",
        "## Hand-reviewed sample",
        "",
        "Ten findings read against their diffs and marked plausible or implausible, one line of",
        "reasoning each. **Fill this in by hand** — an automatically generated verdict here would",
        "be exactly the self-grading this section exists to avoid.",
        "",
        "| # | Repository | Category | Verdict | Reasoning |",
        "| --- | --- | --- | --- | --- |",
    ]
    for index in range(1, 11):
        lines.append(f"| {index} | | | | |")
    lines += [
        "",
        "## Where a category fires more often than a reviewer would expect",
        "",
        "_Fill in after reading the sample above._",
        "",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="validate_real_prs.py",
        description="Analyze recent merged pull requests and report findings-per-PR.",
    )
    parser.add_argument("--repo", action="append", default=[], metavar="OWNER/NAME")
    parser.add_argument("--local", action="append", default=[], metavar="PATH")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument(
        "--scratch",
        default=os.environ.get(
            "SDW_SCRATCH", str(Path(tempfile.gettempdir()) / "semantic-diff-weaver-validation")
        ),
        metavar="DIR",
        help="clone destination; must be outside the working tree",
    )
    parser.add_argument("--output", default="docs/real-pr-validation.md", metavar="PATH")
    namespace = parser.parse_args(argv)

    if not namespace.repo and not namespace.local:
        print("validate_real_prs: pass at least one --repo or --local", file=sys.stderr)
        return 2

    scratch = Path(namespace.scratch).expanduser().resolve()
    if REPOSITORY_ROOT in scratch.parents or scratch == REPOSITORY_ROOT:
        print(
            "validate_real_prs: --scratch must be outside this working tree; analyzed "
            "repository content is never placed inside it.",
            file=sys.stderr,
        )
        return 2
    scratch.mkdir(parents=True, exist_ok=True)

    results, unreachable = collect(namespace.repo, namespace.local, namespace.count, scratch)
    if not results and unreachable:
        print("validate_real_prs: every source was unreachable; skipping cleanly.", file=sys.stderr)
        for item in unreachable:
            print(f"  - {item}", file=sys.stderr)
        return 0

    report = render(results, unreachable, namespace.count)
    Path(namespace.output).write_text(report, encoding="utf-8")
    print(f"validate_real_prs: wrote {namespace.output} ({len(results)} merge(s))", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
