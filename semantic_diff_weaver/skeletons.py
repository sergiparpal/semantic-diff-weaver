"""Failing test skeletons for approved obligations, as a patch to review and apply.

The input is the analysis the obligations come from — one saved as `--format json` or
`--format both` wrote it, or the deterministic analysis of the same arguments run again — and the
IDs of the obligations a person approved from it. The output is a unified diff adding one test per
obligation to the end of the test file named for the changed module — one among the obligation's
candidate tests first — or creating that file beside the obligation's first candidate test, or in
the first test root without one. Nothing is written here: the patch is what the person reviews
before `git apply`. Each test fails until it is written, as an IDE's generated test does, so an
approved obligation cannot pass unwritten.

A saved analysis is untrusted input data, as a baseline is: bounded, parsed with the standard
library, and validated field by field, as one run again is too. Its text reaches the generated code
only as comments, each on a line of its own, with every character that could end the line or
reorder it written as its escape. Test names are built from ASCII letters, digits, and
underscores, and the one string literal names a pattern-validated obligation ID. Test files are
read from the analysis's head commit through the Git boundary, never from the working tree, so the
patch applies to that revision.
"""

from __future__ import annotations

import ast
import json
import re
import textwrap
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .baseline import MAX_BASELINE_EVIDENCE, MAX_BASELINE_FINDINGS, SUPPORTED_SCHEMA_MAJOR
from .config import load_config
from .errors import ErrorCode, WeaverError
from .git_diff import GitRepository, GitTreeEntry
from .models import (
    MAX_PATH_CHARS,
    MAX_PATH_PATTERNS,
    MAX_REF_CHARS,
    AnalyzeRequest,
    BehaviorCategory,
    ObligationType,
    RelativePattern,
    RiskLabel,
    StrictModel,
    WeaverConfig,
    _repository_relative_posix_path,
)
from .path_policy import (
    ensure_authorized_path,
    exclusion_reason,
    glob_matches,
    is_test_support_path,
)
from .test_mapper import CAMEL_BOUNDARY_RE, WORD_SPLIT_RE, _as_module, _mirrored, defines_tests
from .textutil import read_bounded_text

# The obligations one request may approve: as many as an analysis reports by default.
MAX_SKELETONS = 100
# More obligations than any analysis reports: `rules.max_test_obligations` caps them at 1,000.
MAX_ANALYSIS_OBLIGATIONS = 1_000
# `rules.max_candidate_tests_per_obligation` caps an obligation's candidates at 25.
MAX_CANDIDATES = 25
# How many of an obligation's behaviors and candidate tests its skeleton's comments list.
MAX_LISTED_BEHAVIORS = 10
MAX_LISTED_CANDIDATES = 5
MAX_TEST_NAME_CHARS = 64
COMMENT_WIDTH = 88
REGULAR_FILE_MODES = frozenset({"100644", "100755"})
# The paths a patch names as they are. Git quotes any other in its headers, a format with no
# reason to be reproduced for the name of a test file.
SAFE_PATH_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.+-]*)*\.py")
SAFE_STEM_RE = re.compile(r"[A-Za-z0-9_]+")
OBLIGATION_ID_PATTERN = r"^to-\d{3,}$"
NO_NEWLINE_MARKER = "\\ No newline at end of file\n"


class Framework(StrEnum):
    PYTEST = "pytest"
    UNITTEST = "unittest"


ObligationId = Annotated[str, Field(pattern=OBLIGATION_ID_PATTERN, max_length=32)]
BehaviorId = Annotated[str, Field(pattern=r"^bc-\d{3,}$", max_length=32)]


class SkeletonRequest(StrictModel):
    """Which obligations to scaffold, in which framework, and the analysis that lists them.

    The analysis is either one saved as JSON, `analysis_path`, or the analysis of `base_ref` run
    again with the other analysis arguments: exactly one of the two.
    """

    repo_path: str = Field(min_length=1, max_length=MAX_PATH_CHARS)
    obligation_ids: Annotated[list[ObligationId], Field(min_length=1, max_length=MAX_SKELETONS)]
    framework: Framework = Framework.PYTEST
    analysis_path: str | None = Field(default=None, min_length=1, max_length=MAX_PATH_CHARS)
    base_ref: str | None = Field(default=None, min_length=1, max_length=MAX_REF_CHARS)
    head_ref: str | None = Field(default=None, min_length=1, max_length=MAX_REF_CHARS)
    risk_profile: str | None = Field(default=None, min_length=1, max_length=MAX_PATH_CHARS)
    coverage_report: str | None = Field(default=None, min_length=1, max_length=MAX_PATH_CHARS)
    coverage_data: str | None = Field(default=None, min_length=1, max_length=MAX_PATH_CHARS)
    include: Annotated[list[RelativePattern], Field(max_length=MAX_PATH_PATTERNS)] | None = None
    exclude: Annotated[list[RelativePattern], Field(max_length=MAX_PATH_PATTERNS)] | None = None


# The analysis arguments an analysis run again takes: those that decide its obligations and their
# IDs. A baseline only marks findings new or resolved, and a format only shapes the output.
ANALYSIS_ARGUMENTS = (
    "base_ref",
    "head_ref",
    "include",
    "exclude",
    "risk_profile",
    "coverage_report",
    "coverage_data",
)


class Skeleton(StrictModel):
    """One test the patch adds: where, under which name, and for which obligation."""

    obligation_id: str = Field(pattern=OBLIGATION_ID_PATTERN)
    path: str = Field(min_length=1)
    test: str = Field(min_length=1)
    new_file: bool

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        return _repository_relative_posix_path(value)


class SkippedObligation(StrictModel):
    """An approved obligation the patch has no test for, and why."""

    obligation_id: str = Field(pattern=OBLIGATION_ID_PATTERN)
    reason: str = Field(min_length=1)


class SkeletonResult(StrictModel):
    success: Literal[True] = True
    analysis_id: str = Field(pattern=r"^sdw_[A-Za-z0-9_-]+$")
    head_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    framework: Framework
    skeletons: list[Skeleton]
    skipped: list[SkippedObligation]
    warnings: list[str]
    patch: str


class _Read(BaseModel):
    # The analysis holds far more than a skeleton reads; the rest is ignored, not trusted.
    model_config = ConfigDict(extra="ignore", frozen=True)


class _Located(_Read):
    path: str = Field(min_length=1, max_length=MAX_PATH_CHARS)

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        return _repository_relative_posix_path(value)


class _Evidence(_Located):
    symbol: str | None = Field(default=None, max_length=MAX_PATH_CHARS)


class _Behavior(_Read):
    id: BehaviorId
    category: BehaviorCategory
    risk: RiskLabel
    evidence: list[_Evidence] = Field(min_length=1, max_length=MAX_BASELINE_EVIDENCE)


class _Candidate(_Located):
    symbol: str = Field(min_length=1, max_length=MAX_PATH_CHARS)


class _Obligation(_Read):
    id: ObligationId
    behavior_change_ids: list[BehaviorId] = Field(min_length=1, max_length=MAX_BASELINE_FINDINGS)
    type: ObligationType
    priority: int = Field(ge=0, le=100)
    title: str = Field(min_length=1, max_length=300)
    given: str = Field(min_length=1, max_length=1000)
    when: str = Field(min_length=1, max_length=1000)
    then: str = Field(min_length=1, max_length=1000)
    candidate_existing_tests: list[_Candidate] = Field(
        default_factory=list, max_length=MAX_CANDIDATES
    )


class _Repository(_Read):
    head_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")


class _Analysis(_Read):
    schema_version: str = Field(max_length=16)
    analysis_id: str = Field(pattern=r"^sdw_[A-Za-z0-9_-]+$", max_length=200)
    repository: _Repository
    behavior_changes: list[_Behavior] = Field(max_length=MAX_BASELINE_FINDINGS)
    test_obligations: list[_Obligation] = Field(max_length=MAX_ANALYSIS_OBLIGATIONS)

    @model_validator(mode="after")
    def references_resolve(self) -> _Analysis:
        behavior_ids = {item.id for item in self.behavior_changes}
        if len(behavior_ids) != len(self.behavior_changes):
            raise ValueError("behavior change IDs must be unique")
        if len({item.id for item in self.test_obligations}) != len(self.test_obligations):
            raise ValueError("test obligation IDs must be unique")
        if any(not set(item.behavior_change_ids) <= behavior_ids for item in self.test_obligations):
            raise ValueError("an obligation references an unknown behavior change")
        return self


def analysis_error(message: str) -> WeaverError:
    return WeaverError(
        ErrorCode.ANALYSIS_UNREADABLE,
        message,
        "Supply the JSON this tool wrote for an analysis of this repository, from `--format json` "
        "or `--format both`.",
    )


def _load_analysis(path: Path, limit: int) -> _Analysis:
    """Read an earlier analysis for the obligations it lists and the behaviors they test."""
    try:
        document: Any = json.loads(read_bounded_text(path, limit, "analysis", analysis_error))
    except (json.JSONDecodeError, RecursionError) as exc:
        raise analysis_error("The analysis is not valid JSON.") from exc
    # `both` wraps the analysis beside the brief; `json` is the analysis itself.
    if isinstance(document, dict) and isinstance(document.get("analysis"), dict):
        document = document["analysis"]
    if not isinstance(document, dict) or "test_obligations" not in document:
        raise analysis_error(
            "The file holds no analysis; a `--format markdown` brief lists no obligations to read."
        )
    try:
        analysis = _Analysis.model_validate(document)
    except ValidationError as exc:
        raise analysis_error("The analysis does not have the expected shape.") from exc
    if analysis.schema_version.split(".", 1)[0] != SUPPORTED_SCHEMA_MAJOR:
        raise analysis_error(
            f"The analysis has schema version {analysis.schema_version!r}; only 1.x can be read."
        )
    return analysis


SOURCE_REMEDIATION = (
    "Pass analysis_path for an analysis saved as JSON, or base_ref with the head_ref, include, "
    "exclude, risk_profile, coverage_report, and coverage_data of the analysis whose obligation "
    "IDs you read."
)


def _request(arguments: dict[str, Any]) -> SkeletonRequest:
    try:
        request = SkeletonRequest.model_validate(arguments)
    except ValidationError as exc:
        location = ".".join(str(item) for item in exc.errors()[0].get("loc", ())) or "request"
        raise WeaverError(
            ErrorCode.CONFIGURATION_ERROR,
            f"Invalid skeleton argument at {location}.",
            "Pass repo_path, obligation IDs such as to-001, a framework of pytest or unittest, "
            "and the analysis: analysis_path, or base_ref and the other analysis arguments.",
        ) from exc
    given = [name for name in ANALYSIS_ARGUMENTS if getattr(request, name) is not None]
    if request.analysis_path is not None and given:
        raise WeaverError(
            ErrorCode.CONFIGURATION_ERROR,
            f"A saved analysis takes no {given[0]}: pass analysis_path or the analysis arguments.",
            SOURCE_REMEDIATION,
        )
    if request.analysis_path is None and request.base_ref is None:
        raise WeaverError(
            ErrorCode.CONFIGURATION_ERROR,
            "No analysis was named: pass analysis_path, or base_ref to analyze again.",
            SOURCE_REMEDIATION,
        )
    return request


def _analysis_arguments(request: SkeletonRequest) -> dict[str, Any]:
    """The arguments of the analysis to run again, as the analysis tool takes them."""
    arguments: dict[str, Any] = {"repo_path": request.repo_path, "output_format": "json"}
    for name in ANALYSIS_ARGUMENTS:
        if (value := getattr(request, name)) is not None:
            arguments[name] = value
    return arguments


def _analyzed(arguments: dict[str, Any]) -> _Analysis:
    """The analysis run again without inference, as the MCP server always runs it.

    Its obligations and their IDs are then those of every deterministic analysis of the same
    arguments, whatever analysis ID each run is given. With inference they could be numbered
    otherwise from one run to the next, which is why a saved analysis is the other way in.
    """
    # Imported here, as the transports import it: the pipeline loads only when it runs.
    from .service import analyze

    return _Analysis.model_validate(analyze(arguments, llm=None))


def _head_commit(repo: GitRepository, analysis: _Analysis) -> str:
    error = WeaverError(
        ErrorCode.INVALID_REF,
        "The analysis's head commit is not in this repository.",
        "Generate skeletons in the repository the analysis is of, with its head commit present "
        "locally.",
    )
    try:
        resolved = repo.resolve_ref(analysis.repository.head_commit)
    except WeaverError as exc:
        raise error from exc
    # Sixty-four digits cannot name an object in a SHA-1 repository, so Git reads them as a ref:
    # a branch of that name would stand in for the commit the analysis names.
    if resolved != analysis.repository.head_commit:
        raise error
    return resolved


def _head_warnings(repo: GitRepository, head_commit: str) -> list[str]:
    try:
        checked_out = repo.resolve_ref("HEAD")
    except WeaverError:
        return []
    if checked_out == head_commit:
        return []
    return [
        f"The analysis is of commit {head_commit[:12]}, not of HEAD; the patch adds to that "
        "commit's test files."
    ]


def _selected(analysis: _Analysis, requested: Iterable[str]) -> list[_Obligation]:
    wanted = set(requested)
    missing = sorted(wanted - {item.id for item in analysis.test_obligations})
    if missing:
        shown = ", ".join(missing[:10]) + (
            f" and {len(missing) - 10} more" if len(missing) > 10 else ""
        )
        raise WeaverError(
            ErrorCode.CONFIGURATION_ERROR,
            f"The analysis lists no obligation {shown}.",
            "Pass the IDs of obligations the analysis lists, as its brief or JSON shows them.",
        )
    # In the analysis's order, highest priority first, whatever order they were passed in.
    return [item for item in analysis.test_obligations if item.id in wanted]


def _module_stem(source_path: str) -> str:
    """The name a test file gives the module, as candidate-test mirroring reads it: without the
    underscores that make it private or special, and a package by its directory."""
    return PurePosixPath(_as_module(source_path)).stem.strip("_") or "init"


def _words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return [
        word for word in WORD_SPLIT_RE.split(CAMEL_BOUNDARY_RE.sub(" ", folded).lower()) if word
    ]


@dataclass(frozen=True)
class _File:
    """A path the head commit holds, and what keeps it from taking a skeleton, if anything."""

    path: str
    text: str | None
    is_test: bool
    unusable: str | None


@dataclass
class _Target:
    path: str
    existing: str | None
    obligations: list[tuple[_Obligation, tuple[_Behavior, ...]]] = field(default_factory=list)


class _Placement:
    """Where each approved obligation's skeleton goes, read from the head commit in few batches."""

    def __init__(
        self,
        repo: GitRepository,
        head_commit: str,
        config: WeaverConfig,
        placements: list[tuple[_Obligation, str]],
    ) -> None:
        self._repo = repo
        self._head_commit = head_commit
        self._config = config
        self._roots = config.paths.test_roots
        self._names = {
            _module_stem(source): _test_file_names(_module_stem(source)) for _, source in placements
        }
        names = {name for item in self._names.values() for name in item}
        tracked = [
            path for path in repo.list_files(head_commit) if PurePosixPath(path).name in names
        ]
        # A path the mandatory exclusions refuse, such as one under `.git`, is not even looked up.
        candidates = {
            item.path
            for obligation, _ in placements
            for item in obligation.candidate_existing_tests
            if not exclusion_reason(item.path)
        }
        self._files = self._read([*tracked, *sorted(candidates)])
        # One new file per module, where the first of its obligations would put it: two files of
        # one basename in two directories would make pytest refuse the second outside packages.
        self._new_paths: dict[str, str] = {}
        for obligation, source in placements:
            stem = _module_stem(source)
            if stem not in self._new_paths:
                self._new_paths[stem] = _new_path(self._beside(obligation) or self._roots[0], stem)
        # Listing files names no directory, so one standing where a new file would go is asked for.
        self._files.update(
            self._read([path for path in self._new_paths.values() if path not in self._files])
        )

    def _read(self, paths: list[str]) -> dict[str, _File]:
        if not paths:
            return {}
        entries = self._repo.tree_entries(self._head_commit, paths)
        texts = self._repo.read_blob_objects(
            {entry.object_id for entry in entries.values() if entry.mode in REGULAR_FILE_MODES},
            self._config.rules.max_file_bytes,
        )
        return {path: self._file(path, entry, texts) for path, entry in entries.items()}

    def _beside(self, obligation: _Obligation) -> str | None:
        """The directory of the obligation's first candidate test that is a test file here: one
        under a test root or defining a test, readable, and neither excluded nor a link."""
        for candidate in obligation.candidate_existing_tests:
            item = self._files.get(candidate.path)
            if item is not None and item.unusable is None and item.is_test:
                return PurePosixPath(item.path).parent.as_posix()
        return None

    def _file(self, path: str, entry: GitTreeEntry, texts: dict[str, str | None]) -> _File:
        text = texts.get(entry.object_id) if entry.mode in REGULAR_FILE_MODES else None
        # A file named like a test module is one only once it defines a test, as for discovery:
        # this repository's `test_mapper.py` is production code. One that cannot be read may be
        # a test, and is counted as one, so no second file of its name is created beside it.
        is_test = text is None or is_test_support_path(path, self._roots) or defines_tests(text)
        # A reason reaches a terminal, so the path in it is escaped as a comment's text is.
        shown = _escaped(path)
        unusable = None
        if not SAFE_PATH_RE.fullmatch(path):
            unusable = f"{shown} is not a path a patch names as it is"
        elif exclusion_reason(path) or any(
            glob_matches(path, pattern) for pattern in self._config.paths.exclude
        ):
            unusable = f"{shown} is excluded from analysis"
        elif entry.mode not in REGULAR_FILE_MODES:
            unusable = f"{shown} is not a regular file at the analyzed commit"
        elif text is None:
            unusable = f"{shown} is not UTF-8 text within the file size limit"
        return _File(path, text, is_test, unusable)

    def target(self, source_path: str, obligation: _Obligation) -> tuple[str, str | None] | str:
        """The test file and its head text (`None` for a new one), or why there is none."""
        stem = _module_stem(source_path)
        names = self._names[stem]
        existing = [
            item
            for item in self._files.values()
            if item.is_test and PurePosixPath(item.path).name in names
        ]
        candidates = [item.path for item in obligation.candidate_existing_tests]
        # The obligation's own candidates first, in their order; then a file mirroring the
        # module's directory; then path order.
        existing.sort(
            key=lambda item: (
                candidates.index(item.path) if item.path in candidates else len(candidates),
                not _mirrored(source_path, item.path),
                item.path,
            )
        )
        for item in existing:
            if item.unusable is None:
                return item.path, item.text
        if existing:
            # Another file of the same name beside it would make pytest import two modules of one
            # basename, which it refuses outside packages.
            return f"its test file {existing[0].unusable}"
        path = self._new_paths[stem]
        if not SAFE_PATH_RE.fullmatch(path):
            return f"its new test file {_escaped(path)} is not a path a patch names as it is"
        if path in self._files:
            # Beside a candidate outside the test roots, a production module can bear the name.
            return f"{path} exists and is not a test file a skeleton can be added to"
        return path, None


def _safe_stem(stem: str) -> str:
    return stem if SAFE_STEM_RE.fullmatch(stem) else "_".join(_words(stem)) or "init"


def _new_path(directory: str, stem: str) -> str:
    return (PurePosixPath(directory) / f"test_{_safe_stem(stem)}.py").as_posix()


def _test_file_names(stem: str) -> tuple[str, ...]:
    """The names a test file of the module has: as pytest finds it, or as a new one is named."""
    return tuple(
        dict.fromkeys((f"test_{stem}.py", f"{stem}_test.py", f"test_{_safe_stem(stem)}.py"))
    )


def _escaped(text: str) -> str:
    """The text with every character that is not printable written as its escape.

    A comment ends at a line break, and a bidirectional control reorders what a reader sees of
    the code around it. Neither is printable, so `ascii` spells both out, and a crafted title
    cannot end its comment to continue as code or disguise what the line does.
    """
    return "".join(char if char.isprintable() else ascii(char)[1:-1] for char in text)


def _comment(indent: str, text: str, *, lead: str = "", hang: str = "    ") -> list[str]:
    """The text as comment lines, escaped and wrapped, each line nothing but a comment."""
    lines = textwrap.wrap(
        _escaped(text),
        width=COMMENT_WIDTH,
        initial_indent=f"{indent}# {lead}",
        subsequent_indent=f"{indent}# {hang}",
        break_long_words=False,
        break_on_hyphens=False,
    )
    return [f"{line.rstrip()}\n" for line in lines] or [f"{indent}#\n"]


def _symbol(behavior: _Behavior) -> str:
    return behavior.evidence[0].symbol or "<module>"


def _listed(indent: str, singular: str, plural: str, items: list[str]) -> list[str]:
    if len(items) == 1:
        return _comment(indent, f"{singular}: {items[0]}")
    lines = _comment(indent, f"{plural}:")
    for item in items:
        lines += _comment(indent, item, lead="  ")
    return lines


def _comments(indent: str, obligation: _Obligation, behaviors: tuple[_Behavior, ...]) -> list[str]:
    """What the test is to establish, as comments: the obligation, the behaviors it stands for,
    the candidate tests the analysis matched to it, and its Given/When/Then."""
    lines = _comment(
        indent,
        f"Obligation {obligation.id} ({obligation.type.value}, priority {obligation.priority}): "
        f"{obligation.title}",
    )
    described = [
        f"{item.id}, {item.category.value} at {item.risk.value} risk, {_symbol(item)} in "
        f"{item.evidence[0].path}"
        for item in behaviors[:MAX_LISTED_BEHAVIORS]
    ]
    if len(behaviors) > MAX_LISTED_BEHAVIORS:
        described.append(f"and {len(behaviors) - MAX_LISTED_BEHAVIORS} more")
    lines += _listed(indent, "Behavior", "Behaviors", described)
    candidates = [
        f"{item.path}::{item.symbol}"
        for item in obligation.candidate_existing_tests[:MAX_LISTED_CANDIDATES]
    ]
    if candidates:
        lines += _listed(
            indent,
            "Candidate existing test, unverified",
            "Candidate existing tests, unverified",
            candidates,
        )
    else:
        lines += _comment(indent, "No candidate existing test was found.")
    for label, text in (
        ("Given", obligation.given),
        ("When", obligation.when),
        ("Then", obligation.then),
    ):
        lines += _comment(indent, f"{label}: {text}")
    return lines


def _test_name(obligation: _Obligation, behavior: _Behavior, taken: set[str]) -> str:
    symbol = _symbol(behavior)
    words = [] if symbol == "<module>" else _words(".".join(symbol.split(".")[-2:]))
    name = "test"
    for word in [*words, *_words(obligation.title)]:
        if len(name) + 1 + len(word) > MAX_TEST_NAME_CHARS:
            break
        name = f"{name}_{word}"
    if name == "test":
        name = f"test_obligation_{obligation.id.removeprefix('to-')}"
    return _unique(name, taken)


def _unique(name: str, taken: set[str]) -> str:
    candidate, number = name, 1
    while candidate in taken:
        number += 1
        candidate = f"{name}_{number}"
    taken.add(candidate)
    return candidate


def _failure(obligation: _Obligation) -> str:
    # `json.dumps` writes a double-quoted literal Python reads alike, whatever the text holds.
    return json.dumps(f"Write the test for obligation {obligation.id}.")


def _module_bindings(tree: ast.Module) -> tuple[set[str], str | None]:
    """The names a module binds at its top level, and how it names `unittest.TestCase`."""
    names: set[str] = set()
    test_case: str | None = None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.partition(".")[0]
                names.add(bound)
                if alias.name == "unittest" or (
                    alias.asname is None and alias.name.startswith("unittest.")
                ):
                    test_case = f"{bound}.TestCase"
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound = alias.asname or alias.name
                names.add(bound)
                if node.level == 0 and node.module == "unittest" and alias.name == "TestCase":
                    test_case = bound
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(
                item.id
                for target in targets
                for item in ast.walk(target)
                if isinstance(item, ast.Name)
            )
    return names, test_case


def _parsed(text: str) -> ast.Module | None:
    try:
        return ast.parse(text)
    except (MemoryError, RecursionError, SyntaxError, TypeError, ValueError):
        return None


@dataclass(frozen=True)
class _Rendered:
    addition: str
    skeletons: list[Skeleton]


def _render(target: _Target, framework: Framework, analysis_id: str) -> _Rendered | str:
    """The code to add to one test file, every name in it new to the file; or why there is none.

    Appended code must not rebind a name the file already has: a second `def` of an existing
    test's name would replace that test, which would then silently stop running.
    """
    taken: set[str] = set()
    test_case: str | None = None
    if target.existing is not None:
        tree = _parsed(target.existing)
        if tree is None:
            return f"its test file {target.path} does not parse as Python"
        taken, test_case = _module_bindings(tree)
    new_file = target.existing is None
    provenance = "".join(
        _comment(
            "",
            f"Test skeletons for obligations of semantic-diff-weaver analysis {analysis_id}. Each "
            "fails until it is written.",
            hang="",
        )
    )
    skeletons: list[Skeleton] = []
    if framework is Framework.PYTEST:
        blocks = []
        for obligation, behaviors in target.obligations:
            name = _test_name(obligation, behaviors[0], taken)
            blocks.append(
                "".join(
                    [
                        f"def {name}():\n",
                        *_comments("    ", obligation, behaviors),
                        f"    raise NotImplementedError({_failure(obligation)})\n",
                    ]
                )
            )
            skeletons.append(
                Skeleton(
                    obligation_id=obligation.id, path=target.path, test=name, new_file=new_file
                )
            )
        return _Rendered(f"{provenance}\n\n" + "\n\n".join(blocks), skeletons)
    header = f"{provenance}\n\n"
    if test_case is None:
        header += "import unittest\n\n\n"
        test_case = "unittest.TestCase"
    stem = _module_stem(target.obligations[0][1][0].evidence[0].path)
    class_name = _unique(
        "Test" + "".join(word.capitalize() for word in _words(stem)) + "Obligations", taken
    )
    methods = []
    method_names: set[str] = set()
    for obligation, behaviors in target.obligations:
        name = _test_name(obligation, behaviors[0], method_names)
        methods.append(
            "".join(
                [
                    f"    def {name}(self):\n",
                    *_comments("        ", obligation, behaviors),
                    f"        self.fail({_failure(obligation)})\n",
                ]
            )
        )
        skeletons.append(
            Skeleton(
                obligation_id=obligation.id,
                path=target.path,
                test=f"{class_name}.{name}",
                new_file=new_file,
            )
        )
    return _Rendered(f"{header}class {class_name}({test_case}):\n" + "\n".join(methods), skeletons)


def _lines(text: str) -> list[str]:
    """The text's lines as Git counts them, split at line feeds only, each keeping its own."""
    parts = text.split("\n")
    lines = [f"{part}\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _file_diff(path: str, existing: str | None, addition: str) -> str:
    """A `git apply` diff adding `addition` to the end of `existing`, or creating the file."""
    if existing is None:
        added = _lines(addition)
        return "".join(
            [
                f"diff --git a/{path} b/{path}\n",
                "new file mode 100644\n",
                "--- /dev/null\n",
                f"+++ b/{path}\n",
                f"@@ -0,0 +1,{len(added)} @@\n",
                *(f"+{line}" for line in added),
            ]
        )
    lines = _lines(existing)
    terminated = [line for line in lines if line.endswith("\n")]
    newline = "\r\n" if terminated and terminated[-1].endswith("\r\n") else "\n"
    blank = 0
    for line in reversed(lines):
        if line.strip():
            break
        blank += 1
    # Two blank lines before the first new definition, counting those the file ends with.
    leading = "" if not lines else "\n" * max(0, 2 - blank)
    added = [
        line.replace("\n", newline) if line.endswith("\n") else line
        for line in _lines(leading + addition)
    ]
    context = lines[-3:]
    body = [f" {line}" for line in context]
    removed = 0
    if context and not context[-1].endswith("\n"):
        # The last line gains the line feed it lacks: removed as it is and added back with one.
        last = context[-1]
        body[-1] = f"-{last}\n{NO_NEWLINE_MARKER}"
        added.insert(0, f"{last}{newline}")
        removed = 1
    start = len(lines) - len(context) + 1 if lines else 0
    old_count = len(context)
    new_count = len(context) - removed + len(added)
    return "".join(
        [
            f"diff --git a/{path} b/{path}\n",
            f"--- a/{path}\n",
            f"+++ b/{path}\n",
            f"@@ -{start},{old_count} +{max(start, 1)},{new_count} @@\n",
            *body,
            *(f"+{line}" for line in added),
        ]
    )


def generate_skeletons(arguments: dict[str, Any]) -> dict[str, Any]:
    """Plan and render a failing test for each approved obligation; never writes a file."""
    request = _request(arguments)
    repo = GitRepository.open(str(ensure_authorized_path(Path(request.repo_path))))
    ensure_authorized_path(repo.root)
    if request.analysis_path is not None:
        config, warnings = load_config(repo.root)
        analysis = _load_analysis(
            ensure_authorized_path(Path(request.analysis_path)), config.rules.max_baseline_bytes
        )
    else:
        rerun = _analysis_arguments(request)
        # The configuration the analysis read, so its test roots and exclusions are the same.
        config, warnings = load_config(repo.root, AnalyzeRequest.model_validate(rerun))
        analysis = _analyzed(rerun)
    head_commit = _head_commit(repo, analysis)
    warnings.extend(_head_warnings(repo, head_commit))
    obligations = _selected(analysis, request.obligation_ids)
    behaviors = {item.id: item for item in analysis.behavior_changes}
    linked = {
        item.id: tuple(behaviors[behavior_id] for behavior_id in item.behavior_change_ids)
        for item in obligations
    }
    sources = {item.id: linked[item.id][0].evidence[0].path for item in obligations}
    placement = _Placement(
        repo, head_commit, config, [(item, sources[item.id]) for item in obligations]
    )
    targets: dict[str, _Target] = {}
    skipped: list[SkippedObligation] = []
    for obligation in obligations:
        chosen = placement.target(sources[obligation.id], obligation)
        if isinstance(chosen, str):
            skipped.append(SkippedObligation(obligation_id=obligation.id, reason=chosen))
            continue
        path, existing = chosen
        target = targets.setdefault(path, _Target(path, existing))
        target.obligations.append((obligation, linked[obligation.id]))
    diffs: list[str] = []
    skeletons: list[Skeleton] = []
    for path in sorted(targets):
        target = targets[path]
        rendered = _render(target, request.framework, analysis.analysis_id)
        if isinstance(rendered, str):
            skipped.extend(
                SkippedObligation(obligation_id=obligation.id, reason=rendered)
                for obligation, _ in target.obligations
            )
            continue
        diffs.append(_file_diff(path, target.existing, rendered.addition))
        skeletons.extend(rendered.skeletons)
    order = {item.id: index for index, item in enumerate(obligations)}
    return SkeletonResult(
        analysis_id=analysis.analysis_id,
        head_commit=head_commit,
        framework=request.framework,
        skeletons=sorted(skeletons, key=lambda item: order[item.obligation_id]),
        skipped=sorted(skipped, key=lambda item: order[item.obligation_id]),
        warnings=warnings,
        patch="".join(diffs),
    ).model_dump(mode="json")
