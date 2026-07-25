"""Ingest a coverage report the user's own CI already produced.

This is what turns "these tests mention the symbol" into "this changed line has no test
touching it". The tool reads the artifact as **untrusted input data** and runs nothing: the
no-execute invariant is unchanged, and it consumes a coverage report without ever producing
one.

Two formats are supported, both parseable with the standard library:

- **coverage.py's native JSON**, which this repository's own CI already emits;
- **lcov `.info`**, line-oriented plain text.

Cobertura and JaCoCo XML are excluded deliberately. Parsing untrusted XML would either add a
`defusedxml` dependency or accept an entity-expansion surface in a tool whose selling point is
safety against a hostile repository. See `docs/decisions.md`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from .errors import ErrorCode, WeaverError
from .models import LineRange, WeaverConfig

CoverageState = Literal["covered", "uncovered", "unknown"]

SOURCE_COVERAGE_JSON = "coverage.py-json"
SOURCE_LCOV = "lcov"

# lcov is line-oriented; a hostile file could otherwise be one enormous line.
MAX_LCOV_RECORDS = 200_000
MAX_LINES_PER_FILE = 1_000_000


def coverage_error(message: str) -> WeaverError:
    return WeaverError(
        ErrorCode.COVERAGE_UNREADABLE,
        message,
        "Supply a coverage.py JSON report or an lcov .info file produced by your own test run.",
    )


@dataclass(frozen=True)
class FileCoverage:
    """Executed and unexecuted line numbers for one file in the report."""

    covered: frozenset[int]
    uncovered: frozenset[int]

    @property
    def known(self) -> frozenset[int]:
        return self.covered | self.uncovered


@dataclass
class CoverageMap:
    """A resolved report, queried by repository-relative path and line range.

    Coverage reports store paths relative to the CI working directory, which need not match
    the analyzer's repository-relative paths. Both sides are normalized to POSIX and resolved
    by longest matching suffix, with an exact match always winning.

    ``files`` is treated as immutable once constructed — the parsers below build it in full
    before the map exists, and the two caches are only sound because nothing mutates it after.
    """

    source: str
    files: Mapping[str, FileCoverage]
    _unmatched: set[str] = field(default_factory=set)
    _matched: set[str] = field(default_factory=set)
    # Suffix resolution is pure, and the same few changed paths are asked about repeatedly:
    # once by `status_for` per finding and again by `counts_for` in the summary. Splitting
    # every candidate path on every call made a lookup O(report files) in `PurePosixPath`
    # construction; splitting once per report and memoizing the answer makes it O(1) after
    # the first ask for a path.
    _candidate_parts: list[tuple[tuple[str, ...], FileCoverage]] = field(
        default_factory=list, init=False, repr=False
    )
    _resolved: dict[str, FileCoverage | None] = field(default_factory=dict, init=False, repr=False)

    @property
    def matched_files(self) -> int:
        return len(self._matched)

    @property
    def unmatched_files(self) -> int:
        return len(self._unmatched)

    @property
    def unmatched_paths(self) -> tuple[str, ...]:
        return tuple(sorted(self._unmatched))

    def _candidates(self) -> list[tuple[tuple[str, ...], FileCoverage]]:
        """The report's paths pre-split into POSIX components, built once per map."""
        if not self._candidate_parts and self.files:
            self._candidate_parts = [
                (PurePosixPath(candidate).parts, entry) for candidate, entry in self.files.items()
            ]
        return self._candidate_parts

    def resolve(self, path: str) -> FileCoverage | None:
        """Find the report entry for a repository-relative path, or None."""
        query = _posix(path)
        if not query:
            return None
        if query in self._resolved:
            return self._resolved[query]
        resolved = self._resolve_uncached(query)
        self._resolved[query] = resolved
        return resolved

    def _resolve_uncached(self, query: str) -> FileCoverage | None:
        exact = self.files.get(query)
        if exact is not None:
            return exact
        query_parts = PurePosixPath(query).parts
        best: FileCoverage | None = None
        best_score = 0
        ambiguous = False
        for candidate_parts, entry in self._candidates():
            score = _suffix_score(candidate_parts, query_parts)
            if score == 0:
                continue
            if score > best_score:
                best, best_score, ambiguous = entry, score, False
            elif score == best_score and entry != best:
                # Two unrelated files match equally well. Claiming either would be a guess,
                # and an unsupported coverage claim is worse than no claim.
                ambiguous = True
        return None if ambiguous else best

    def status_for(self, path: str, lines: LineRange) -> CoverageState:
        """Report coverage for a changed line range.

        A file absent from the report is `unknown`, never `uncovered` — a misconfigured path
        prefix must be visible as a gap in the *report*, not reported as a gap in the tests.
        A range whose known lines disagree is also `unknown`; only a unanimous range is
        claimed either way.
        """
        entry = self.resolve(path)
        if entry is None:
            self._unmatched.add(_posix(path))
            return "unknown"
        self._matched.add(_posix(path))
        span = range(lines.start, lines.end + 1)
        covered = sum(1 for line in span if line in entry.covered)
        uncovered = sum(1 for line in span if line in entry.uncovered)
        if covered and not uncovered:
            return "covered"
        if uncovered and not covered:
            return "uncovered"
        return "unknown"

    def counts_for(self, path: str, lines: LineRange) -> tuple[int, int]:
        """Return (covered, uncovered) known line counts inside a range."""
        entry = self.resolve(path)
        if entry is None:
            return 0, 0
        span = range(lines.start, lines.end + 1)
        return (
            sum(1 for line in span if line in entry.covered),
            sum(1 for line in span if line in entry.uncovered),
        )


def _posix(value: str) -> str:
    """Normalize an untrusted path to POSIX form without resolving it against the filesystem.

    Traversal and absolute prefixes are stripped rather than rejected: a coverage report
    legitimately stores absolute CI paths, and the result is only ever used as a lookup key,
    never opened. Nothing here reaches the filesystem.
    """
    normalized = value.replace("\\", "/").strip()
    if not normalized or "\x00" in normalized:
        return ""
    parts = [part for part in PurePosixPath(normalized).parts if part not in {"", ".", "..", "/"}]
    if parts and parts[0].endswith(":"):  # a Windows drive letter
        parts = parts[1:]
    return PurePosixPath(*parts).as_posix() if parts else ""


def _suffix_score(candidate: tuple[str, ...], query: tuple[str, ...]) -> int:
    """Count shared trailing components; 0 unless one path fully contains the other's tail."""
    shared = 0
    for left, right in zip(reversed(candidate), reversed(query), strict=False):
        if left != right:
            break
        shared += 1
    if shared == 0:
        return 0
    if shared != len(query) and shared != len(candidate):
        return 0
    return shared


def _read_bounded(path: Path, limit: int) -> str:
    try:
        resolved = path.resolve(strict=True)
        size = resolved.stat().st_size
    except OSError as exc:
        raise coverage_error("The coverage report is inaccessible.") from exc
    if not resolved.is_file():
        raise coverage_error("The coverage report is not a regular file.")
    if size > limit:
        raise coverage_error(f"The coverage report is {size} bytes, above the {limit}-byte limit.")
    try:
        # Read one byte past the limit so a file that grows between stat and read is caught.
        with resolved.open("r", encoding="utf-8", errors="strict") as handle:
            text = handle.read(limit + 1)
    except (OSError, UnicodeDecodeError) as exc:
        raise coverage_error("The coverage report is not readable UTF-8 text.") from exc
    if len(text.encode("utf-8", errors="ignore")) > limit:
        raise coverage_error("The coverage report exceeded the size limit while being read.")
    return text


def _parse_coverage_json(text: str) -> dict[str, FileCoverage]:
    try:
        document: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise coverage_error("The coverage report is not valid JSON.") from exc
    if not isinstance(document, dict):
        raise coverage_error("The coverage report is not a JSON object.")
    files = document.get("files")
    if not isinstance(files, dict):
        raise coverage_error("The coverage report has no 'files' object.")
    parsed: dict[str, FileCoverage] = {}
    for raw_path, entry in files.items():
        if not isinstance(raw_path, str) or not isinstance(entry, dict):
            continue
        key = _posix(raw_path)
        if not key:
            continue
        covered = _line_numbers(entry.get("executed_lines"))
        uncovered = _line_numbers(entry.get("missing_lines"))
        parsed[key] = _merge_entry(parsed.get(key), covered, uncovered)
    if not parsed:
        raise coverage_error("The coverage report contained no usable file entries.")
    return parsed


def _parse_lcov(text: str) -> dict[str, FileCoverage]:
    parsed: dict[str, FileCoverage] = {}
    current: str | None = None
    covered: set[int] = set()
    uncovered: set[int] = set()
    records = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("SF:"):
            current = _posix(line[3:])
            covered, uncovered = set(), set()
        elif line.startswith("DA:") and current:
            records += 1
            if records > MAX_LCOV_RECORDS:
                raise coverage_error("The coverage report exceeded the line-record limit.")
            number, hits = _lcov_datum(line[3:])
            if number is None:
                continue
            (covered if hits else uncovered).add(number)
        elif line == "end_of_record" and current:
            parsed[current] = _merge_entry(
                parsed.get(current), frozenset(covered), frozenset(uncovered)
            )
            current, covered, uncovered = None, set(), set()
    if current and (covered or uncovered):  # a final record with no end_of_record marker
        parsed[current] = _merge_entry(
            parsed.get(current), frozenset(covered), frozenset(uncovered)
        )
    if not parsed:
        raise coverage_error("The coverage report contained no usable file entries.")
    return parsed


def _lcov_datum(payload: str) -> tuple[int | None, bool]:
    parts = payload.split(",")
    if len(parts) < 2:
        return None, False
    try:
        number = int(parts[0])
        hits = int(parts[1])
    except ValueError:
        return None, False
    if number < 1 or number > MAX_LINES_PER_FILE:
        return None, False
    return number, hits > 0


def _line_numbers(value: Any) -> frozenset[int]:
    if not isinstance(value, list):
        return frozenset()
    numbers = {
        item
        for item in value
        if isinstance(item, int) and not isinstance(item, bool) and 1 <= item <= MAX_LINES_PER_FILE
    }
    return frozenset(numbers)


def _merge_entry(
    existing: FileCoverage | None, covered: frozenset[int], uncovered: frozenset[int]
) -> FileCoverage:
    """Union duplicate entries for one path; an executed line stays executed."""
    if existing is not None:
        covered = covered | existing.covered
        uncovered = uncovered | existing.uncovered
    return FileCoverage(covered=covered, uncovered=uncovered - covered)


def load_coverage(path: Path, config: WeaverConfig) -> CoverageMap:
    """Read and parse a coverage report, sniffing the format by content, not by extension."""
    text = _read_bounded(path, config.rules.max_coverage_bytes)
    stripped = text.lstrip()
    if stripped.startswith("{"):
        return CoverageMap(source=SOURCE_COVERAGE_JSON, files=_parse_coverage_json(text))
    if "SF:" in text:
        return CoverageMap(source=SOURCE_LCOV, files=_parse_lcov(text))
    raise coverage_error("The coverage report is neither coverage.py JSON nor lcov text.")


def summarize(
    coverage: CoverageMap, ranges: Iterable[tuple[str, LineRange]]
) -> tuple[int, int, int]:
    """Return (changed_lines, covered_lines, uncovered_lines) over the supplied ranges."""
    changed = covered = uncovered = 0
    for path, span in ranges:
        changed += span.end - span.start + 1
        hit, miss = coverage.counts_for(path, span)
        covered += hit
        uncovered += miss
    return changed, covered, uncovered
