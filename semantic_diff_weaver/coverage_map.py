"""Ingest a coverage report the user's own CI already produced.

This is what turns "these tests mention the symbol" into "this changed line has no test
touching it". The tool reads the artifact as **untrusted input data** and runs nothing: the
no-execute invariant is unchanged, and it consumes a coverage report without ever producing
one.

Two formats are supported, both parseable with the standard library:

- **coverage.py's native JSON**, which this repository's own CI already emits;
- **lcov `.info`**, line-oriented plain text.

A run measured with per-test contexts, as `pytest --cov-context=test` records them, also says
which test executed each line, so a test that ran a changed line is known by its run rather than
by its name or imports. The run's coverage.py data file, `.coverage`, holds them once each and is
read beside the report; a small report holds them too when `show_contexts` writes them.

Cobertura and JaCoCo XML are excluded deliberately. Parsing untrusted XML would either add a
`defusedxml` dependency or accept an entity-expansion surface in a tool whose selling point is
safety against a hostile repository. See `docs/decisions.md`.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections import defaultdict
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from .errors import ErrorCode, WeaverError
from .models import LineRange, WeaverConfig
from .textutil import read_bounded_bytes, read_bounded_text

CoverageState = Literal["covered", "uncovered", "unknown"]

SOURCE_COVERAGE_JSON = "coverage.py-json"
SOURCE_LCOV = "lcov"
SOURCE_COVERAGE_DATA = "coverage.py-data"

# lcov is line-oriented; a hostile file could otherwise be one enormous line.
MAX_LCOV_RECORDS = 200_000
MAX_LINES_PER_FILE = 1_000_000
# Test contexts are kept compactly, each test once, but a report may still name more of them,
# or more pairs of a line and a test, than an analysis needs to hold; past either bound the
# rest of the contexts are left unread and the report is said to be incomplete.
MAX_CONTEXT_TESTS = 100_000
MAX_CONTEXT_PAIRS = 2_000_000
# A pytest node ID long past any real one names no test worth matching.
MAX_CONTEXT_CHARS = 1_000
# The phases pytest-cov's `--cov-context=test` labels each test's contexts with.
CONTEXT_PHASES = ("|setup", "|run", "|teardown")
# What a SQLite database, as coverage.py's data file is, starts with.
SQLITE_HEADER = b"SQLite format 3\x00"
# A data file is read within this long, whatever the queries its schema makes do.
COVERAGE_DATA_SECONDS = 10.0
# No value coverage.py writes comes near this; reading a longer one fails.
MAX_DATA_VALUE_BYTES = 1_000_000
# A file's line numbers as coverage.py's numbits hold them, a bit each, up to the line bound.
MAX_NUMBITS_BYTES = MAX_LINES_PER_FILE // 8 + 1
# The connection settings Python 3.12 can make, by name, each with the value it is given.
DEFENSIVE_SETTINGS = (
    ("SQLITE_DBCONFIG_DEFENSIVE", True),
    ("SQLITE_DBCONFIG_TRUSTED_SCHEMA", False),
    ("SQLITE_DBCONFIG_ENABLE_VIEW", False),
    ("SQLITE_DBCONFIG_ENABLE_TRIGGER", False),
)


def coverage_error(message: str) -> WeaverError:
    return WeaverError(
        ErrorCode.COVERAGE_UNREADABLE,
        message,
        "Supply a coverage.py JSON report or an lcov .info file produced by your own test run.",
    )


def data_error(message: str) -> WeaverError:
    return WeaverError(
        ErrorCode.COVERAGE_UNREADABLE,
        message,
        "Supply the .coverage data file your own test run wrote with per-test contexts, as "
        "pytest --cov-context=test records them.",
    )


@dataclass(frozen=True)
class FileCoverage:
    """Executed and unexecuted line numbers for one file in the report."""

    covered: frozenset[int]
    uncovered: frozenset[int]
    # Per executed line, the tests its contexts say executed it, by position in the report's
    # `tests`; empty for a report without contexts.
    contexts: Mapping[int, frozenset[int]] = field(default_factory=dict, hash=False)

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
    # The tests the report's contexts name, each once: a pytest node ID without its phase or
    # parameters, or coverage.py's own dotted test name.
    tests: tuple[str, ...] = ()
    # Whether a bound left some of the contexts unread.
    contexts_incomplete: bool = False
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
        """Report coverage for a line range; see `status_for_lines`."""
        return self.status_for_lines(path, range(lines.start, lines.end + 1))

    def status_for_lines(self, path: str, lines: Iterable[int]) -> CoverageState:
        """Report coverage for a set of changed lines.

        A file absent from the report is `unknown`, never `uncovered` — a misconfigured path
        prefix must be visible as a gap in the *report*, not reported as a gap in the tests.
        Lines whose known states disagree are also `unknown`; only a unanimous set is claimed
        either way.
        """
        entry = self.resolve(path)
        if entry is None:
            self._unmatched.add(_posix(path))
            return "unknown"
        self._matched.add(_posix(path))
        covered, uncovered = _known_counts(entry, lines)
        if covered and not uncovered:
            return "covered"
        if uncovered and not covered:
            return "uncovered"
        return "unknown"

    def counts_for(self, path: str, lines: LineRange) -> tuple[int, int]:
        """Return (covered, uncovered) known line counts inside a range."""
        return self.counts_for_lines(path, range(lines.start, lines.end + 1))

    def counts_for_lines(self, path: str, lines: Iterable[int]) -> tuple[int, int]:
        """Return (covered, uncovered) known line counts among `lines`."""
        entry = self.resolve(path)
        if entry is None:
            return 0, 0
        return _known_counts(entry, lines)

    def measured(self, lines_by_path: Mapping[str, Collection[int]]) -> int:
        """How many of `lines_by_path` the report measures, executed or not."""
        measured = 0
        for path, lines in lines_by_path.items():
            entry = self.resolve(path)
            if entry is not None:
                measured += sum(1 for line in lines if line in entry.known)
        return measured

    def executed(
        self, lines_by_path: Mapping[str, Collection[int]]
    ) -> dict[str, frozenset[tuple[str, int]]]:
        """Per test the contexts name, which of `lines_by_path` it executed, each with its path;
        nothing for a file the contexts do not cover."""
        executed: dict[str, set[tuple[str, int]]] = defaultdict(set)
        for path, lines in lines_by_path.items():
            entry = self.resolve(path)
            if entry is None:
                continue
            for line in lines:
                for test in entry.contexts.get(line, ()):
                    executed[self.tests[test]].add((path, line))
        return {test: frozenset(found) for test, found in executed.items()}


class ContextTests:
    """Which of an analysis's tests the context labels of a report name.

    A pytest node ID names a test by its file and its names, the file matched as the report's
    own paths are, by the longest suffix it shares with a test's file, since pytest names files
    from its root directory rather than the repository's. coverage.py's dotted name names it by
    its module and names, matched by the longest dotted suffix, since a module's name depends on
    how the tests were imported. An exact match wins, and a label that the tests of two files
    match equally well names neither: claiming either would be a guess.
    """

    def __init__(self, tests: Sequence[tuple[str, str]]) -> None:
        # Each test as its file and its names within the file, `TestRounding.test_round_total`.
        self._tests = list(tests)
        self._by_name: dict[str, list[int]] = defaultdict(list)
        for position, (_, symbol) in enumerate(self._tests):
            self._by_name[symbol.rpartition(".")[2]].append(position)
        self._found: dict[str, frozenset[int]] = {}

    def positions(self, label: str) -> frozenset[int]:
        """The positions in `tests` of the tests a label names, as `_test_label` writes it."""
        if label not in self._found:
            self._found[label] = self._resolve(label)
        return self._found[label]

    def _resolve(self, label: str) -> frozenset[int]:
        scored: list[tuple[int, int]] = []
        if "::" in label:
            location, *names = label.split("::")
            query = PurePosixPath(location).parts
            for position in self._by_name.get(names[-1], ()):
                path, symbol = self._tests[position]
                if symbol.split(".") == names:
                    exact = int(path == location)
                    scored.append(
                        (_suffix_score(PurePosixPath(path).parts, query) + exact, position)
                    )
        else:
            names = label.split(".")
            for position in self._by_name.get(names[-1], ()):
                path, symbol = self._tests[position]
                dotted = [*PurePosixPath(path).with_suffix("").parts, *symbol.split(".")]
                if dotted[-len(names) :] == names:
                    scored.append((len(names) + int(len(dotted) == len(names)), position))
        best = max((score for score, _ in scored), default=0)
        chosen = [position for score, position in scored if best and score == best]
        if len({self._tests[position][0] for position in chosen}) != 1:
            return frozenset()
        return frozenset(chosen)


def _known_counts(entry: FileCoverage, lines: Iterable[int]) -> tuple[int, int]:
    covered = uncovered = 0
    for line in lines:
        if line in entry.covered:
            covered += 1
        if line in entry.uncovered:
            uncovered += 1
    return covered, uncovered


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


def _test_label(raw: Any) -> str | None:
    """The test a context label names: a pytest node ID without its phase or parameters,
    `tests/test_money.py::TestRounding::test_round_total`, or coverage.py's own dotted test name,
    `test_money.TestRounding.test_round_total`.

    None for the empty context, of code that ran outside any test, such as an import, and for
    anything else, a static context such as `unit` among them.
    """
    if not isinstance(raw, str) or not raw or len(raw) > MAX_CONTEXT_CHARS:
        return None
    for phase in CONTEXT_PHASES:
        if raw.endswith(phase):
            raw = raw[: -len(phase)]
            break
    if "::" in raw:
        location, _, rest = raw.partition("::")
        # A parameter ID is any text, separators included, but a name holds no bracket.
        names = rest.split("[", 1)[0].split("::")
        path = _posix(location)
        if not path or not all(name.isidentifier() for name in names):
            return None
        return "::".join([path, *names])
    names = raw.split(".")
    if len(names) < 2 or not all(name.isidentifier() for name in names):
        return None
    return raw


class _Contexts:
    """Test contexts as they are read, under bounds: each test once, by position."""

    def __init__(self) -> None:
        self.tests: dict[str, int] = {}
        self.pairs = 0
        self.incomplete = False

    def position(self, test: str) -> int | None:
        """A test's position, given on first sight; none once the tests reach their bound."""
        position = self.tests.get(test)
        if position is None:
            if len(self.tests) >= MAX_CONTEXT_TESTS:
                self.incomplete = True
                return None
            position = self.tests[test] = len(self.tests)
        return position

    def counted(self, pairs: int) -> bool:
        """Count pairs of a line and a test; false once they pass their bound."""
        self.pairs += pairs
        if self.pairs > MAX_CONTEXT_PAIRS:
            self.incomplete = True
        return not self.incomplete

    def lines(self, value: Any, executed: frozenset[int]) -> dict[int, frozenset[int]]:
        """Per executed line of a JSON report's file entry, the tests its `contexts` say
        executed it."""
        found: dict[int, frozenset[int]] = {}
        if not isinstance(value, dict):
            return found
        for raw_line, labels in value.items():
            line = _context_line(raw_line)
            if line is None or line not in executed or not isinstance(labels, list):
                continue
            tests: set[int] = set()
            for label in labels:
                test = _test_label(label)
                if test is None:
                    continue
                position = self.position(test)
                if position is None:
                    return found
                tests.add(position)
            if not tests:
                continue
            if not self.counted(len(tests)):
                return found
            found[line] = frozenset(tests)
        return found


def _context_line(value: Any) -> int | None:
    """A line number as a `contexts` object keys it, `"12"`, within bounds."""
    if not isinstance(value, str) or not value.isdecimal() or len(value) > 7:
        return None
    number = int(value)
    return number if 1 <= number <= MAX_LINES_PER_FILE else None


def _parse_coverage_json(text: str) -> tuple[dict[str, FileCoverage], _Contexts]:
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
    contexts = _Contexts()
    for raw_path, entry in files.items():
        if not isinstance(raw_path, str) or not isinstance(entry, dict):
            continue
        key = _posix(raw_path)
        if not key:
            continue
        covered = _line_numbers(entry.get("executed_lines"))
        uncovered = _line_numbers(entry.get("missing_lines"))
        tests = {} if contexts.incomplete else contexts.lines(entry.get("contexts"), covered)
        parsed[key] = _merge_entry(parsed.get(key), covered, uncovered, tests)
    if not parsed:
        raise coverage_error("The coverage report contained no usable file entries.")
    return parsed, contexts


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
    existing: FileCoverage | None,
    covered: frozenset[int],
    uncovered: frozenset[int],
    contexts: Mapping[int, frozenset[int]] | None = None,
) -> FileCoverage:
    """Union duplicate entries for one path; an executed line stays executed, and keeps every
    test either entry says executed it."""
    merged = dict(contexts or {})
    if existing is not None:
        covered = covered | existing.covered
        uncovered = uncovered | existing.uncovered
        for line, tests in existing.contexts.items():
            merged[line] = merged.get(line, frozenset()) | tests
    return FileCoverage(covered=covered, uncovered=uncovered - covered, contexts=merged)


def load_coverage(path: Path, config: WeaverConfig) -> CoverageMap:
    """Read and parse a coverage report, sniffing the format by content, not by extension."""
    text = read_bounded_text(
        path, config.rules.max_coverage_bytes, "coverage report", coverage_error
    )
    stripped = text.lstrip()
    if stripped.startswith("{"):
        files, contexts = _parse_coverage_json(text)
        return CoverageMap(
            source=SOURCE_COVERAGE_JSON,
            files=files,
            tests=tuple(contexts.tests),
            contexts_incomplete=contexts.incomplete,
        )
    if "SF:" in text:
        return CoverageMap(source=SOURCE_LCOV, files=_parse_lcov(text))
    raise coverage_error("The coverage report is neither coverage.py JSON nor lcov text.")


def summarize(
    coverage: CoverageMap, lines_by_path: Mapping[str, Collection[int]]
) -> tuple[int, int, int]:
    """Return (changed_lines, covered_lines, uncovered_lines), each line counted once.

    Summing the ranges of the findings instead counted a function's lines again inside its
    module's, and reported more changed lines than the file had.
    """
    changed = covered = uncovered = 0
    for path, lines in lines_by_path.items():
        changed += len(lines)
        hit, miss = coverage.counts_for_lines(path, lines)
        covered += hit
        uncovered += miss
    return changed, covered, uncovered


def _harden(connection: sqlite3.Connection, deadline: float) -> None:
    """Set a connection up to read a hostile database: no view, trigger, attached database, or
    function a schema names, a bound on every value, and each query interrupted at `deadline`.

    Before Python 3.12 no view or trigger can be turned off, so the tables read are checked to be
    ordinary ones, and the deadline still stops whatever a schema makes a query do.
    """
    setconfig = getattr(connection, "setconfig", None)
    if setconfig is not None:
        for name, value in DEFENSIVE_SETTINGS:
            setconfig(getattr(sqlite3, name), value)
    connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
    connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_DATA_VALUE_BYTES)
    connection.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)
    connection.execute("PRAGMA trusted_schema = OFF")
    connection.execute("PRAGMA cell_size_check = ON")
    connection.execute("PRAGMA query_only = ON")


def _numbits(value: Any) -> Iterator[int]:
    """The line numbers coverage.py's numbits hold: bit `n % 8` of byte `n // 8` is line `n`."""
    if not isinstance(value, bytes) or len(value) > MAX_NUMBITS_BYTES:
        return
    for index, byte in enumerate(value):
        for bit in range(8) if byte else ():
            if byte >> bit & 1 and (line := index * 8 + bit) >= 1:
                yield line


def _read_data(connection: sqlite3.Connection) -> CoverageMap:
    """The test contexts of a coverage.py data file, from the tables coverage.py writes.

    Each of `file` and `context`, and `line_bits` or `arc`, must be an ordinary table: a view or
    a virtual table by that name could run anything a query reaches. Lines come from `line_bits`
    when lines were measured and from `arc`, whose ends are lines, when branches were.
    """
    tables = {
        name: (kind, sql)
        for name, kind, sql in connection.execute("SELECT name, type, sql FROM sqlite_master")
    }

    def ordinary(name: str) -> bool:
        kind, sql = tables.get(name, (None, None))
        return kind == "table" and isinstance(sql, str) and sql.upper().startswith("CREATE TABLE")

    if not (ordinary("file") and ordinary("context")):
        raise data_error("The coverage data file holds no files and contexts as coverage.py's do.")
    paths = {
        file_id: key
        for file_id, raw_path in connection.execute("SELECT id, path FROM file")
        if isinstance(file_id, int) and isinstance(raw_path, str) and (key := _posix(raw_path))
    }
    contexts = _Contexts()
    tests: dict[int, int] = {}
    for context_id, label in connection.execute("SELECT id, context FROM context"):
        test = _test_label(label)
        if not isinstance(context_id, int) or test is None:
            continue
        position = contexts.position(test)
        if position is None:
            break
        tests[context_id] = position
    lines: dict[str, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))

    def record(file_id: Any, context_id: Any, numbers: Iterable[int]) -> bool:
        path = paths.get(file_id)
        test = tests.get(context_id)
        if path is None or test is None:
            return True
        found = lines[path]
        added = 0
        for line in numbers:
            if 1 <= line <= MAX_LINES_PER_FILE and test not in found[line]:
                found[line].add(test)
                added += 1
        return contexts.counted(added)

    if ordinary("line_bits"):
        query = "SELECT file_id, context_id, numbits FROM line_bits"
        for file_id, context_id, numbits in connection.execute(query):
            if not record(file_id, context_id, _numbits(numbits)):
                break
    if ordinary("arc") and not contexts.incomplete:
        query = "SELECT file_id, context_id, fromno, tono FROM arc"
        for file_id, context_id, start, end in connection.execute(query):
            ends = [line for line in (start, end) if isinstance(line, int)]
            if not record(file_id, context_id, ends):
                break
    return CoverageMap(
        source=SOURCE_COVERAGE_DATA,
        files={
            path: FileCoverage(
                covered=frozenset(found),
                uncovered=frozenset(),
                contexts={line: frozenset(ran) for line, ran in found.items()},
            )
            for path, found in lines.items()
        },
        tests=tuple(contexts.tests),
        contexts_incomplete=contexts.incomplete,
    )


def load_coverage_data(path: Path, config: WeaverConfig) -> CoverageMap:
    """Read which test executed each line from a coverage.py data file, `.coverage`.

    The file is a SQLite database and untrusted input: its bytes are read under their own bound
    and handed to SQLite as an in-memory database, so SQLite opens no path and leaves no
    journal, and the connection is hardened before the schema is parsed. The map it gives says
    which test ran a line and nothing else: which lines did not run takes the source, which only
    the report was computed from.
    """
    data = read_bounded_bytes(
        path, config.rules.max_coverage_data_bytes, "coverage data file", data_error
    )
    if not data.startswith(SQLITE_HEADER):
        raise data_error("The coverage data file is not a SQLite database.")
    connection = sqlite3.connect(":memory:")
    try:
        deserialize = getattr(connection, "deserialize", None)
        if deserialize is None:
            raise data_error("This Python's SQLite cannot read a database from memory.")
        _harden(connection, time.monotonic() + COVERAGE_DATA_SECONDS)
        deserialize(data)
        return _read_data(connection)
    except sqlite3.Error as exc:
        raise data_error("The coverage data file is not a readable coverage.py data file.") from exc
    finally:
        connection.close()
