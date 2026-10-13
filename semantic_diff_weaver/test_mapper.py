"""Static discovery and ranking of unverified candidate existing tests."""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache, lru_cache
from itertools import pairwise
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
CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
MODULE_NAME_RE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
# `-m pkg` inside one string: a shell command line, or what `shlex.split` is given.
RUN_FLAG_RE = re.compile(r"(?<!\S)-m\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)")
MAX_TEST_INDEX_FILES = 500
MAX_TEST_INDEX_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class IndexedTest:
    path: str
    symbol: str
    imports: frozenset[str]
    # Words as well as whole identifiers: category terminology reads these.
    name_tokens: frozenset[str]
    body_tokens: frozenset[str]
    # Whole identifiers, for whether the body uses a changed symbol or module by name.
    body_identifiers: frozenset[str] = frozenset()
    # Modules the test's file runs as a program, as `python -m` or `runpy` does.
    modules_run: frozenset[str] = frozenset()


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


class _ModuleRunVisitor(ast.NodeVisitor):
    """Collect the modules a test file runs as a program rather than imports.

    `[sys.executable, "-m", "pkg"]`, `"python -m pkg"`, and `runpy.run_module("pkg")` all run
    `pkg/__main__.py`, which no import reaches.
    """

    def __init__(self) -> None:
        self.modules: set[str] = set()

    def _add(self, value: object) -> None:
        if isinstance(value, str) and MODULE_NAME_RE.fullmatch(value):
            self.modules.add(value)

    def _visit_sequence(self, node: ast.List | ast.Tuple) -> None:
        values = [item.value if isinstance(item, ast.Constant) else None for item in node.elts]
        for flag, value in pairwise(values):
            if flag == "-m":
                self._add(value)
        self.generic_visit(node)

    visit_List = _visit_sequence
    visit_Tuple = _visit_sequence

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            self.modules.update(RUN_FLAG_RE.findall(node.value))

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        runs_module = (isinstance(function, ast.Attribute) and function.attr == "run_module") or (
            isinstance(function, ast.Name) and function.id == "run_module"
        )
        if runs_module and node.args and isinstance(node.args[0], ast.Constant):
            self._add(node.args[0].value)
        self.generic_visit(node)


def _tokens(text: str) -> frozenset[str]:
    tokens: set[str] = set()
    for item in TOKEN_RE.findall(text):
        lowered = item.casefold()
        tokens.add(lowered)
        tokens.update(part for part in lowered.split("_") if part)
    return frozenset(tokens)


def _bare(identifier: str) -> str:
    """An identifier casefolded and without the underscores that make it private.

    A test's `_serve(...)` helper wraps `serve`, so the two read alike. A dunder such as
    `__init__` is a protocol name rather than a word, and stays whole.
    """
    folded = identifier.casefold()
    if len(folded) > 4 and folded.startswith("__") and folded.endswith("__"):
        return folded
    return folded.strip("_")


def _identifiers(text: str) -> frozenset[str]:
    """Whole identifiers, so `run_args_from` does not read as `run`."""
    return frozenset(name for item in TOKEN_RE.findall(text) if (name := _bare(item)))


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
    runs_visitor = _ModuleRunVisitor()
    runs_visitor.visit(tree)
    modules_run = frozenset(item.casefold() for item in runs_visitor.modules)
    indexed: list[IndexedTest] = []
    for symbol, node in _test_functions(tree.body):
        body_text = redact_text(ast.unparse(node), max_chars=4000)
        indexed.append(
            IndexedTest(
                path=path,
                symbol=symbol,
                imports=imports,
                name_tokens=_tokens(node.name),
                body_tokens=_tokens(body_text),
                body_identifiers=_identifiers(body_text),
                modules_run=modules_run,
            )
        )
    return indexed


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


def _name_words(name: str) -> str | None:
    """A changed symbol's name as the snake_case words a test name spells it in.

    `RunArgs` and `_run_args` both read `run_args`. A dunder such as `__init__` reads as
    nothing, since `init` in a test name is about something else, and so does a synthetic name
    such as `<module>`.
    """
    words = _bare(CAMEL_BOUNDARY_RE.sub("_", name))
    return words if words.isidentifier() and not words.startswith("_") else None


@lru_cache(maxsize=16384)
def _mirrored(source_path: str, test_path: str) -> bool:
    source = PurePosixPath(source_path)
    test = PurePosixPath(test_path)
    # A test file names a module without the underscores that make it private or special:
    # `test_common.py` for `_common.py`, `test_main.py` for `__main__.py`.
    source_stem = source.stem.strip("_")
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
    body_identifier_positions: dict[str, set[int]] = defaultdict(set)
    run_positions: dict[str, set[int]] = defaultdict(set)
    # ``imported.endswith(f".{symbol}")`` is an exact match on a dotted import's last segment,
    # so it indexes directly. Keeping it out of the module scan below means the import signal
    # costs one pass per distinct module rather than one per distinct (module, symbol) pair.
    import_leaf_positions: dict[str, set[int]] = defaultdict(set)
    all_tokens_by_test: list[frozenset[str]] = []
    for position, test in enumerate(tests):
        all_tokens_by_test.append(test.name_tokens | test.body_tokens)
        for identifier in test.body_identifiers:
            body_identifier_positions[identifier].add(position)
        for module_run in test.modules_run:
            run_positions[module_run].add(position)
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
    def named_for(words: str) -> frozenset[int]:
        # Whole words only: `register_cooked_meal` names `test_register_cooked_meal_rollback`,
        # but `cooked_meal` must not match `test_uncooked_meals`.
        marker = f"_{words}_"
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
        name = candidate.symbol.rsplit(".", 1)[-1]
        symbol_token = name.casefold()
        module = _module_name(candidate.path).casefold()
        mapped = mapped_for(candidate.path)
        mirrored = mirrored_for(candidate.path)
        empty: set[int] = set()
        imported = module_imported_for(module) | (
            import_leaf_positions.get(symbol_token, empty) if symbol_token else empty
        )
        # `python -m pkg` runs `pkg/__main__.py`, which no import reaches. Running any other
        # module relates a test to all of its symbols, as coarsely as an import already does.
        ran = (
            run_positions.get(module.removesuffix(".__main__"), empty)
            if module.endswith(".__main__")
            else empty
        )
        # Whole identifiers: by its words, `run` was in the body of every test that called
        # `run_args_from(...)`, and every test name was in its own body through the `def` line.
        body_key = _bare(name)
        bodied = body_identifier_positions.get(body_key, empty) if body_key else empty
        # Whole words in sequence, as for a module: by single words, a compound name such as
        # `pr_number` could never match `test_pr_number_rejects_zero`.
        words = _name_words(name)
        named = named_for(words) if words else empty
        if words and "_" not in words:
            # A single word turns up in test names about something else — `run` in
            # `test_evaluate_gets_its_options_as_run_args` — so it names the symbol only in a
            # test whose body uses the symbol too.
            named = named & bodied
        stem = _distinctive_stem(candidate.path)
        module_named = named_for(stem) if stem else empty
        # The body only confirms a test already named after the module. On its own a module
        # mention is everywhere — this suite writes `mcp_server.X` in tests of anything — and
        # with the package-import rule it promoted unrelated tests over the real ones.
        module_bodied = module_named & body_identifier_positions.get(stem, empty) if stem else empty
        terminology = terminology_for(candidate.category)
        ranked: list[CandidateTest] = []
        # Only a structural hit can produce a candidate, so tests outside this union would
        # score identically to being skipped. Terminology alone never clears the threshold.
        structural = mapped | mirrored | imported | ran | named | bodied | module_named
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
            # As strong as an import: running the module is how a test reaches `__main__`.
            if test_position in ran:
                score += 0.25
                reasons.append("changed module run with python -m")
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
