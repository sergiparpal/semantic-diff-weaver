"""Static discovery and ranking of unverified candidate existing tests."""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache, lru_cache
from pathlib import PurePosixPath
from typing import ClassVar

from .errors import WeaverError
from .git_diff import GitRepository
from .models import BehaviorCategory, CandidateTest, WeaverConfig
from .path_policy import (
    exclusion_reason,
    glob_matches,
    has_test_module_name,
    is_test_support_path,
    redact_text,
)
from .semantic_candidates import SemanticCandidate
from .taxonomy import CATEGORY_PROFILES

TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
MAX_TEST_INDEX_FILES = 500
MAX_TEST_INDEX_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class IndexedTest:
    path: str
    symbol: str
    imports: frozenset[str]
    name_tokens: frozenset[str]
    body_tokens: frozenset[str]


@dataclass
class TestIndex:
    __test__: ClassVar[bool] = False
    tests: list[IndexedTest]
    incomplete: bool
    warnings: list[str]


class _ImportVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.imports: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.update(alias.name for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.imports.add(node.module)
            self.imports.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif node.level:
            self.imports.update(alias.name for alias in node.names)


def _tokens(text: str) -> frozenset[str]:
    tokens: set[str] = set()
    for item in TOKEN_RE.findall(text):
        lowered = item.casefold()
        tokens.add(lowered)
        tokens.update(part for part in lowered.split("_") if part)
    return frozenset(tokens)


def _test_functions(
    body: list[ast.stmt], prefix: str = ""
) -> Iterator[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Yield each test function's qualified name and node, as pytest would collect them."""
    for node in body:
        if isinstance(node, ast.ClassDef):
            yield from _test_functions(node.body, f"{prefix}{node.name}.")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test"):
                yield f"{prefix}{node.name}", node


def _index_source(path: str, source: str) -> list[IndexedTest]:
    # No type comment is read here, and ``type_comments=True`` only narrows the accepted
    # grammar, so requesting them would drop otherwise-indexable test files for a signal
    # this function never uses.
    tree = ast.parse(source)
    imports_visitor = _ImportVisitor()
    imports_visitor.visit(tree)
    imports = frozenset(item.casefold() for item in imports_visitor.imports)
    return [
        IndexedTest(
            path=path,
            symbol=symbol,
            imports=imports,
            name_tokens=_tokens(node.name),
            body_tokens=_tokens(redact_text(ast.unparse(node), max_chars=4000)),
        )
        for symbol, node in _test_functions(tree.body)
    ]


def defines_tests(source: str) -> bool:
    """Whether candidate-test discovery would find a test in this source.

    Source that does not parse defines none, so a file named like a test module but broken is
    analyzed, and the AST stage reports why it could not be read.
    """
    try:
        tree = ast.parse(source)
    except (MemoryError, RecursionError, SyntaxError, TypeError, ValueError):
        return False
    return next(_test_functions(tree.body), None) is not None


def build_test_index(repo: GitRepository, head_commit: str, config: WeaverConfig) -> TestIndex:
    tests: list[IndexedTest] = []
    incomplete = False
    warnings: list[str] = []
    try:
        repository_files = repo.list_files(head_commit)
    except WeaverError:
        return TestIndex(
            tests=[],
            incomplete=True,
            warnings=["Candidate test file discovery exceeded a safe Git boundary."],
        )
    test_paths = sorted(
        path
        for path in repository_files
        if not exclusion_reason(path)
        and not any(glob_matches(path, pattern) for pattern in config.paths.exclude)
        and (is_test_support_path(path, config.paths.test_roots) or has_test_module_name(path))
    )
    if len(test_paths) > MAX_TEST_INDEX_FILES:
        incomplete = True
        warnings.append(
            f"Candidate test indexing was capped at {MAX_TEST_INDEX_FILES} files; mapping is incomplete."
        )
        test_paths = test_paths[:MAX_TEST_INDEX_FILES]
    entries = repo.tree_entries(head_commit, test_paths)
    eligible_entries = {
        path: entry for path, entry in entries.items() if entry.mode not in {"120000", "160000"}
    }
    blob_text = repo.read_blob_objects(
        {entry.object_id for entry in eligible_entries.values()},
        config.rules.max_file_bytes,
        max_total_bytes=MAX_TEST_INDEX_BYTES,
    )
    indexed_bytes = 0
    for path in test_paths:
        entry = eligible_entries.get(path)
        source = blob_text.get(entry.object_id) if entry else None
        if source is None:
            incomplete = True
            warnings.append(
                "At least one candidate test file exceeded bounded source limits or was not "
                "regular UTF-8 text."
            )
            continue
        source_bytes = len(source.encode("utf-8"))
        if indexed_bytes + source_bytes > MAX_TEST_INDEX_BYTES:
            incomplete = True
            warnings.append(
                "Candidate test indexing reached its aggregate byte cap; mapping is incomplete."
            )
            break
        indexed_bytes += source_bytes
        try:
            tests.extend(_index_source(path, source))
        except (SyntaxError, ValueError, TypeError):
            incomplete = True
            warnings.append(f"Could not parse candidate test file {path!r}; mapping is incomplete.")
    tests.sort(key=lambda item: (item.path, item.symbol))
    return TestIndex(tests=tests, incomplete=incomplete, warnings=sorted(set(warnings)))


def _module_name(path: str) -> str:
    pure = PurePosixPath(path)
    parts = list(pure.with_suffix("").parts)
    if parts and parts[0] in {"src", "lib"}:
        parts = parts[1:]
    return ".".join(parts)


def _distinctive_stem(path: str) -> str | None:
    """A changed file's stem when it is specific enough to name its module in a test.

    Handler-per-module code exports one generic symbol from every file — `HANDLER`, `main`,
    `run` — so the changed symbol cannot find its tests, while the tests name the module:
    `test_register_cooked_meal_rollback` calls `register_cooked_meal(...)`. A multi-word stem
    is that specific; a single word such as `store` or `base` turns up in tests about anything,
    and private or dunder modules (`_common`, `__init__`) are not named by tests at all.
    """
    stem = PurePosixPath(path).stem.casefold()
    if stem.startswith("_") or "_" not in stem:
        return None
    return stem


@lru_cache(maxsize=16384)
def _mirrored(source_path: str, test_path: str) -> bool:
    source = PurePosixPath(source_path)
    test = PurePosixPath(test_path)
    source_stem = source.stem
    test_stem = test.stem.removeprefix("test_").removesuffix("_test")
    if source_stem != test_stem:
        return False
    source_parts = [item for item in source.parent.parts if item not in {"src", "lib"}]
    test_parts = [item for item in test.parent.parts if item not in {"tests", "test"}]
    return not source_parts or not test_parts or source_parts[-2:] == test_parts[-2:]


def map_candidate_tests(
    candidates: list[SemanticCandidate],
    index: TestIndex,
    config: WeaverConfig,
) -> dict[int, list[CandidateTest]]:
    """Return capped static candidates; terminology alone can never create a match."""
    result: dict[int, list[CandidateTest]] = {}
    tests = index.tests
    # Every signal below is resolved into a set of test positions, memoized on the only input
    # it actually depends on. A candidate then scores just the union of its structural hits
    # instead of the whole index, and repeated paths, modules, symbols, and categories — which
    # dominate a real diff — each cost one pass rather than one per (candidate, test) pair.
    tests_by_mapping: list[frozenset[int]] = [
        frozenset(
            position
            for position, test in enumerate(tests)
            if any(glob_matches(test.path, pattern) for pattern in mapping.tests)
        )
        for mapping in config.mapping
    ]
    name_token_positions: dict[str, set[int]] = defaultdict(set)
    body_token_positions: dict[str, set[int]] = defaultdict(set)
    # ``imported.endswith(f".{symbol}")`` is an exact match on a dotted import's last segment,
    # so it indexes directly. Keeping it out of the module scan below means the import signal
    # costs one pass per distinct module rather than one per distinct (module, symbol) pair.
    import_leaf_positions: dict[str, set[int]] = defaultdict(set)
    all_tokens_by_test: list[frozenset[str]] = []
    for position, test in enumerate(tests):
        all_tokens_by_test.append(test.name_tokens | test.body_tokens)
        for token in test.name_tokens:
            name_token_positions[token].add(position)
        for token in test.body_tokens:
            body_token_positions[token].add(position)
        for import_path in test.imports:
            _, dot, leaf = import_path.rpartition(".")
            if dot:
                import_leaf_positions[leaf].add(position)

    @cache
    def mapped_for(source_path: str) -> frozenset[int]:
        matched: set[int] = set()
        for mapping_index, mapping in enumerate(config.mapping):
            if glob_matches(source_path, mapping.source):
                matched |= tests_by_mapping[mapping_index]
        return frozenset(matched)

    @cache
    def mirrored_for(source_path: str) -> frozenset[int]:
        return frozenset(
            position for position, test in enumerate(tests) if _mirrored(source_path, test.path)
        )

    @cache
    def module_imported_for(module: str) -> frozenset[int]:
        module_prefix = f"{module}."
        module_suffix = f".{module}"
        return frozenset(
            position
            for position, test in enumerate(tests)
            if any(
                imported == module
                or imported.startswith(module_prefix)
                or module.startswith(f"{imported}.")
                or imported.endswith(module_suffix)
                for imported in test.imports
            )
        )

    @cache
    def module_named_for(stem: str) -> frozenset[int]:
        # Whole words only: `register_cooked_meal` names `test_register_cooked_meal_rollback`,
        # but `cooked_meal` must not match `test_uncooked_meals`.
        marker = f"_{stem}_"
        return frozenset(
            position
            for position, test in enumerate(tests)
            if marker in f"_{test.symbol.rsplit('.', 1)[-1].casefold()}_"
        )

    @cache
    def terminology_for(category: BehaviorCategory) -> frozenset[int]:
        terms = CATEGORY_PROFILES[category].test_terms
        return frozenset(
            position for position, tokens in enumerate(all_tokens_by_test) if terms & tokens
        )

    for candidate_index, candidate in enumerate(candidates):
        symbol_token = candidate.symbol.rsplit(".", 1)[-1].casefold()
        module = _module_name(candidate.path).casefold()
        mapped = mapped_for(candidate.path)
        mirrored = mirrored_for(candidate.path)
        empty: set[int] = set()
        imported = module_imported_for(module) | (
            import_leaf_positions.get(symbol_token, empty) if symbol_token else empty
        )
        named = name_token_positions.get(symbol_token, empty) if symbol_token else empty
        bodied = body_token_positions.get(symbol_token, empty) if symbol_token else empty
        stem = _distinctive_stem(candidate.path)
        module_named = module_named_for(stem) if stem else empty
        # The body only confirms a test already named after the module. On its own a module
        # mention is everywhere — this suite writes `mcp_server.X` in tests of anything — and
        # with the package-import rule it promoted unrelated tests over the real ones.
        module_bodied = module_named & body_token_positions.get(stem, empty) if stem else empty
        terminology = terminology_for(candidate.category)
        ranked: list[CandidateTest] = []
        # Only a structural hit can produce a candidate, so tests outside this union would
        # score identically to being skipped. Terminology alone never clears the threshold.
        structural = mapped | mirrored | imported | named | bodied | module_named
        for test_position in sorted(structural):
            test = tests[test_position]
            score = 0.0
            reasons: list[str] = []
            if test_position in mapped:
                score += 0.30
                reasons.append("explicit configured mapping")
            if test_position in mirrored:
                score += 0.25
                reasons.append("mirrored source/test path")
            if test_position in imported:
                score += 0.25
                reasons.append("direct module or symbol import")
            if test_position in named:
                score += 0.20
                reasons.append("changed symbol token in test name")
            if test_position in bodied:
                score += 0.10
                reasons.append("changed symbol token in bounded test body")
            # As strong as a mirrored file name: the test is named after the changed module.
            if test_position in module_named:
                score += 0.25
                reasons.append("changed module named in test name")
            if test_position in module_bodied:
                score += 0.10
                reasons.append("changed module named in bounded test body")
            if test_position in terminology:
                score += 0.10
                reasons.append("behavior-category terminology")
            score = min(1.0, score)
            if score >= 0.35:
                ranked.append(
                    CandidateTest(
                        path=test.path,
                        symbol=test.symbol,
                        match_score=round(score, 2),
                        match_reasons=reasons,
                    )
                )
        ranked.sort(key=lambda item: (-item.match_score, item.path, item.symbol))
        result[candidate_index] = ranked[: config.rules.max_candidate_tests_per_obligation]
    return result
