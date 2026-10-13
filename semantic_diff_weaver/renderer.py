"""Deterministic canonical JSON transport and concise Markdown rendering."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .models import (
    WHOLE_TEST_FILE,
    AnalysisResult,
    BothEnvelope,
    CandidateTest,
    MarkdownEnvelope,
    OutputFormat,
    Presentation,
)

BIDI_CONTROLS = frozenset(
    {
        "\u061c",
        "\u200e",
        "\u200f",
        "\u202a",
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
        "\u2066",
        "\u2067",
        "\u2068",
        "\u2069",
    }
)


# What the brief says each scope reason means, read after its count. Keys are the reason codes
# in `ScopeMetadata.omitted` and `ScopeMetadata.excluded_counts`; a code without a phrase is
# shown as itself, so a new reason is never hidden — `tests/unit/test_scope_reasons.py` asks for a
# phrase for every code the pipeline can emit.
OMISSION_PHRASES = {
    "resource_prioritization": "lower-priority file(s) past the resource budget",
    "aggregate_source_limit": "file(s) past the aggregate source budget",
    "parse_incomplete_files": "file(s) that did not parse completely",
    "ast_resource_limit": "file(s) past the AST safety budget",
    "changed_symbol_limit": "changed symbol(s) past `max_changed_symbols`",
    "minimum_confidence": "finding(s) below `minimum_report_confidence`",
    "low_risk_refactor_policy": "low-risk refactor(s) hidden by `emit_low_risk_refactors: false`",
    "llm_batch_limit": "model evidence batch(es) past the call budget",
    "model_input_symbol_limit": "symbol(s) too large for one model call",
    "model_evidence_limit": "symbol(s) with truncated model evidence",
    "global_obligation_limit": "test obligation(s) past `max_test_obligations`",
}
EXCLUSION_PHRASES = {
    "path_filter": "outside the configured include and exclude paths",
    "unsupported_extension": "not Python",
    "test_file": "test file(s) left out by `analyze_test_files: false`",
    "secret_filename": "with secret-like names",
    "control_directory": "in version-control directories",
    "cache_or_environment": "in caches or environments",
    "symlink_or_gitlink": "symlink(s) or submodule(s)",
    "binary": "binary",
    "aggregate_source_limit": "past the aggregate source budget",
    "oversized_or_non_utf8": "oversized or not UTF-8",
}


def _visible_controls(text: str, *, preserve_newlines: bool = False) -> str:
    output: list[str] = []
    for character in text:
        if character == "\n" and preserve_newlines:
            output.append(character)
        elif character == "\r":
            output.append("\\r")
        elif character == "\n":
            output.append("\\n")
        elif character == "\t":
            output.append("\\t")
        elif unicodedata.category(character) == "Cc" or character in BIDI_CONTROLS:
            width = 4 if ord(character) <= 0xFFFF else 8
            output.append(f"\\u{ord(character):0{width}x}")
        else:
            output.append(character)
    return "".join(output)


def _escape(text: str) -> str:
    controlled = _visible_controls(text)
    return re.sub(r"([\\`*_{}\[\]()<>#+.!|~-])", r"\\\1", controlled)


def _reason_items(counts: list[tuple[str, int]], phrases: dict[str, str]) -> str:
    return " · ".join(
        f"{count} {phrases.get(reason) or _escape(reason)}" for reason, count in counts if count
    )


def _scope_reason_lines(result: AnalysisResult) -> list[str]:
    """Say why scope was left out, not only how much: the counts alone could not be acted on."""
    lines = []
    omitted = _reason_items(
        [(item.reason, item.count) for item in result.scope.omitted], OMISSION_PHRASES
    )
    if omitted:
        lines.append(f"**Omitted:** {omitted}.")
    excluded = _reason_items(list(result.scope.excluded_counts.items()), EXCLUSION_PHRASES)
    if excluded:
        lines.append(f"**Excluded files:** {excluded}.")
    return lines


def _candidate(item: CandidateTest) -> str:
    """One candidate test, or a test file standing for its tests related only through it."""
    details = f"{item.match_score:.2f}; {_escape(', '.join(item.match_reasons))}"
    if item.symbol == WHOLE_TEST_FILE:
        return f"`{_escape(item.path)}` (whole file; {details})"
    return f"`{_escape(item.path)}::{_escape(item.symbol)}` ({details})"


def _excerpt(text: str, limit: int | None) -> str:
    """`text`, cut to `limit` characters when one is set, with an ellipsis marking the cut."""
    if limit is None or len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()} …"


def _excerpt_lines(limit: int | None) -> list[str]:
    if limit is None:
        return []
    if limit == 0:
        return ["**Evidence excerpts:** omitted to fit a size limit; `git diff` shows the changes."]
    return [
        f"**Evidence excerpts:** cut to {limit} characters to fit a size limit; `git diff` shows "
        "them in full."
    ]


def _fenced_summary(label: str, text: str) -> list[str]:
    safe = _visible_controls(text.replace("`", "'"), preserve_newlines=True)
    return [f"    {label}: {line}" for line in safe.splitlines() or [""]]


def _coverage_lines(result: AnalysisResult) -> list[str]:
    """One line describing an ingested coverage report, and nothing at all without one.

    States plainly that the report was consumed, not produced, and that unmatched files are
    unknown rather than uncovered — so a reader can tell a coverage gap from a path-prefix
    mismatch.
    """
    coverage = result.coverage
    if coverage is None:
        return []
    unmatched = (
        f", {coverage.unmatched_files} changed file(s) absent from the report (reported as "
        "unknown, not uncovered)"
        if coverage.unmatched_files
        else ""
    )
    return [
        (
            f"**Coverage (ingested {coverage.source} report, not produced by this tool):** "
            f"{coverage.covered_lines} covered and {coverage.uncovered_lines} uncovered of "
            f"{coverage.changed_lines} changed line(s) across {coverage.matched_files} matched "
            f"file(s){unmatched}."
        )
    ]


def render_markdown(result: AnalysisResult, *, excerpt_chars: int | None = None) -> str:
    """The brief. `excerpt_chars` cuts each evidence code excerpt, and 0 drops them.

    Only a transport with a hard result limit sets it — findings, obligations, and every ID
    are kept either way, and the brief says when excerpts were shortened.
    """
    lines = [
        "## Semantic Diff Test Brief",
        "",
        (
            f"**Overall risk:** {result.summary.overall_risk.value} "
            f"({result.summary.risk_score}/100) · **Confidence:** "
            f"{result.summary.overall_confidence:.0%}"
        ),
        (
            f"**Scope:** {len(result.scope.analyzed_files)}/{result.scope.changed_files_total} "
            "changed file(s) analyzed, "
            f"{result.summary.changed_symbols} changed symbol(s), "
            f"{sum(item.count for item in result.scope.omitted)} omitted item(s), "
            f"{sum(result.scope.excluded_counts.values())} excluded file(s)"
            f"{' (truncated)' if result.scope.truncated else ''}."
        ),
        *_scope_reason_lines(result),
        *_coverage_lines(result),
        *_excerpt_lines(excerpt_chars),
        "",
        "### Inferred behavior changes",
        "",
    ]
    if not result.behavior_changes:
        lines.append(
            "No reportable Python behavior change was inferred from the bounded static evidence."
        )
    for behavior in result.behavior_changes:
        prefix = (
            "Review question"
            if behavior.presentation is Presentation.REVIEW_QUESTION
            else "Finding"
        )
        lines.extend(
            [
                f"- **{prefix} `{behavior.id}` · {_escape(behavior.category.value)} · "
                f"{behavior.risk.value} risk · {behavior.confidence:.0%} confidence:** "
                f"{_escape(behavior.summary)}",
                f"  - Observable impact: {_escape(behavior.observable_impact)}",
            ]
        )
        for evidence in behavior.evidence:
            location = _escape(evidence.path)
            if evidence.symbol:
                location += f" — `{_escape(evidence.symbol)}`"
            ranges = []
            if evidence.old_lines:
                ranges.append(f"old {evidence.old_lines.start}-{evidence.old_lines.end}")
            if evidence.new_lines:
                ranges.append(f"new {evidence.new_lines.start}-{evidence.new_lines.end}")
            suffix = f" ({', '.join(ranges)})" if ranges else ""
            lines.append(f"  - Evidence `{evidence.id}`: {location}{suffix}")
            if (evidence.old or evidence.new) and excerpt_chars != 0:
                old = _excerpt(evidence.old or "∅", excerpt_chars)
                new = _excerpt(evidence.new or "∅", excerpt_chars)
                lines.extend(["    ````text", *_fenced_summary("old", old)])
                lines.extend([*_fenced_summary("new", new), "    ````"])
    lines.extend(["", "### Prioritized test obligations", ""])
    if not result.test_obligations:
        lines.append("No test obligations were generated.")
    for obligation in result.test_obligations:
        # No escaping needed: obligation IDs are pattern-validated, and an obligation may only
        # reference behavior IDs of this result, which are too. They number positions within this
        # one result, the only place they can be quoted from.
        covers = ", ".join(f"`{item}`" for item in obligation.behavior_change_ids)
        lines.extend(
            [
                f"- [ ] **P{obligation.priority} — {_escape(obligation.title)}** "
                f"(`{obligation.id}`; covers {covers})",
                f"  - Given: {_escape(obligation.given)}",
                f"  - When: {_escape(obligation.when)}",
                f"  - Then: {_escape(obligation.then)}",
            ]
        )
        if obligation.candidate_existing_tests:
            candidates = ", ".join(_candidate(item) for item in obligation.candidate_existing_tests)
            lines.append(f"  - Candidate existing tests (unverified): {candidates}")
        else:
            lines.append(f"  - Candidate existing tests: none ({obligation.coverage_status.value})")
    review = [
        item
        for item in result.behavior_changes
        if item.presentation is Presentation.REVIEW_QUESTION
    ]
    if review:
        lines.extend(["", "### Review questions", ""])
        lines.extend(f"- {_escape(item.observable_impact)}" for item in review)
    if result.warnings:
        lines.extend(["", "### Warnings", ""])
        lines.extend(f"- {_escape(item)}" for item in result.warnings)
    if result.limitations:
        lines.extend(["", "### Limitations", ""])
        lines.extend(f"- {_escape(item)}" for item in result.limitations)
    lines.extend(
        [
            "",
            "_Advisory static analysis only: no tests or repository code were executed, and candidate "
            "test mappings do not verify runtime coverage._",
        ]
    )
    return "\n".join(lines)


def render_transport(result: AnalysisResult, output_format: OutputFormat) -> dict[str, Any]:
    if output_format is OutputFormat.JSON:
        return result.model_dump(mode="json")
    markdown = render_markdown(result)
    if output_format is OutputFormat.MARKDOWN:
        return MarkdownEnvelope(analysis_id=result.analysis_id, markdown=markdown).model_dump(
            mode="json"
        )
    return BothEnvelope(analysis=result, markdown=markdown).model_dump(mode="json")
