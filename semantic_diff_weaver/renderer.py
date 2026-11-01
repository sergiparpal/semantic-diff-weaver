"""Deterministic canonical JSON transport and concise Markdown rendering."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .models import (
    WHOLE_TEST_FILE,
    AnalysisResult,
    BehaviorChange,
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
    "document_limit": "YAML or JSON file(s) past the document budget",
    "parse_incomplete_documents": "API schema(s) that did not parse completely",
    "schema_delta_limit": "API schema(s) with more changes than one is compared for",
    "parse_incomplete_config_files": "configuration file(s) that did not parse completely",
    "config_delta_limit": "configuration file(s) with more changes than one is compared for",
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
    "not_api_schema": "YAML or JSON that is not an API schema",
    "unreadable_document": "YAML or JSON not readable as bounded text",
    "unreadable_config_file": "named configuration file(s) not readable as bounded text",
    "unsupported_config_format": "named configuration file(s) in a format not compared",
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


def _code(text: str) -> str:
    """`text` as a code span, shown as it is but for its control characters, made visible.

    Markdown reads no escape inside a code span, so `parse\\_amount` would show its backslash.
    A span ends only at a run of backticks as long as the one it opened with, so it opens with
    one longer than any it holds; and with a space inside each end when its text starts or ends
    with a backtick, or with a space at both, since the span drops one space at each end.
    """
    shown = _visible_controls(text)
    fence = "`" * (max((len(run) for run in re.findall(r"`+", shown)), default=0) + 1)
    if (
        shown[:1] == "`"
        or shown[-1:] == "`"
        or (shown[:1] == " " and shown[-1:] == " " and shown.strip())
    ):
        shown = f" {shown} "
    return f"{fence}{shown}{fence}"


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
        return f"{_code(item.path)} (whole file; {details})"
    return f"{_code(f'{item.path}::{item.symbol}')} ({details})"


_COUNTS = {3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}


def _listed(items: list[str]) -> str:
    """`a`, `a and b`, or `a, b, and c`."""
    if len(items) <= 2:
        return " and ".join(items)
    return f"{', '.join(items[:-1])}, and {items[-1]}"


def _symbol_mentions(ids: list[str], behaviors: dict[str, BehaviorChange]) -> list[tuple[str, str]]:
    """Each distinct symbol of the findings `ids`, in their order: its name, and its mention with
    its path and findings."""
    found: dict[tuple[str, str], list[str]] = {}
    for behavior_id in ids:
        evidence = behaviors[behavior_id].evidence[0]
        found.setdefault((evidence.path, evidence.symbol or "<module>"), []).append(behavior_id)
    mentions = []
    for (path, symbol), items in found.items():
        name = _code(symbol)
        # No escaping is needed for the IDs: they are pattern-validated and of this result.
        listed = ", ".join(f"`{item}`" for item in items)
        mentions.append((name, f"{name} ({_escape(path)}, {listed})"))
    return mentions


def _connected_lines(result: AnalysisResult) -> list[str]:
    """Each group of findings calls connect, said as its entry points reaching the rest."""
    if not result.change_groups:
        return []
    behaviors = {item.id: item for item in result.behavior_changes}
    lines = ["", "### Connected changes", ""]
    for group in result.change_groups:
        entries = _symbol_mentions(group.entry_points, behaviors)
        reached = _symbol_mentions(
            [item for item in group.behavior_change_ids if item not in group.entry_points],
            behaviors,
        )
        count = len(entries) + len(reached)
        everything = "both" if count == 2 else f"all {_COUNTS.get(count, str(count))}"
        leading = _listed([mention for _, mention in entries])
        through = entries[0][0] if len(entries) == 1 else "any of them"
        if not reached:
            said = f"{leading} reach each other"
        elif len(entries) == 1:
            said = f"{leading} reaches {_listed([mention for _, mention in reached])}"
        else:
            said = f"{leading}, which reach each other, reach {_listed([m for _, m in reached])}"
        lines.append(f"- {said}: a test through {through} can exercise {everything}.")
    return lines


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


def _baseline_lines(result: AnalysisResult) -> list[str]:
    """How the findings stand against an earlier analysis, and nothing at all without one.

    A resolved finding is shown by its category, path, symbol, and risk only: the baseline is an
    untrusted file, and those are the fields of it that were validated. Its IDs are the earlier
    analysis's own and are not quoted, since IDs number positions within one result.
    """
    baseline = result.baseline
    if baseline is None:
        return []
    lines = [
        (
            f"**Since the baseline** (`{baseline.analysis_id}` at `{baseline.head_commit[:12]}`): "
            f"{len(baseline.new)} new, {len(baseline.updated)} updated, "
            f"{len(baseline.unchanged)} unchanged, {len(baseline.resolved)} resolved."
        )
    ]
    if baseline.resolved:
        resolved = "; ".join(
            f"{item.category.value} in {_code(f'{item.path}::{item.symbol}')} "
            f"({item.risk.value} risk)"
            for item in baseline.resolved
        )
        lines.append(f"**Resolved since the baseline:** {resolved}.")
    return lines


def _baseline_marks(result: AnalysisResult) -> dict[str, str]:
    """The mark each finding new or updated since the baseline carries next to its ID."""
    if result.baseline is None:
        return {}
    return {
        **{item: " · new" for item in result.baseline.new},
        **{item: " · updated" for item in result.baseline.updated},
    }


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
            f"**Verdict:** {result.summary.verdict.value} · "
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
        *_baseline_lines(result),
        *_excerpt_lines(excerpt_chars),
        "",
        "### Inferred behavior changes",
        "",
    ]
    if not result.behavior_changes:
        lines.append("No reportable behavior change was inferred from the bounded static evidence.")
    marks = _baseline_marks(result)
    for behavior in result.behavior_changes:
        prefix = (
            "Review question"
            if behavior.presentation is Presentation.REVIEW_QUESTION
            else "Finding"
        )
        lines.extend(
            [
                f"- **{prefix} `{behavior.id}`{marks.get(behavior.id, '')} · "
                f"{_escape(behavior.category.value)} · "
                f"{behavior.risk.value} risk · {behavior.confidence:.0%} confidence:** "
                f"{_escape(behavior.summary)}",
                f"  - Observable impact: {_escape(behavior.observable_impact)}",
            ]
        )
        for evidence in behavior.evidence:
            location = _escape(evidence.path)
            if evidence.symbol:
                location += f" — {_code(evidence.symbol)}"
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
    lines.extend(_connected_lines(result))
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
