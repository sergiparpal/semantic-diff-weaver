"""Static discovery and ranking of unverified candidate existing tests."""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import cache, lru_cache
from itertools import pairwise
from pathlib import PurePosixPath
from typing import ClassVar

from .call_graph import CallGraph, Node, bare_name
from .errors import WeaverError
from .git_diff import GitRepository
from .models import WHOLE_TEST_FILE, BehaviorCategory, CandidateTest, WeaverConfig
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
    # Identifiers the test uses together with those of the same file's helpers and fixtures it
    # names: `run_args_from(...)` reaches `cli.main` although the test never spells `main`.
    reach_names: frozenset[str] = frozenset()


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


def _identifiers(text: str) -> frozenset[str]:
    """Whole identifiers, so `run_args_from` does not read as `run`."""
    return frozenset(name for item in TOKEN_RE.findall(text) if (name := bare_name(item)))


def _code_names(node: ast.AST) -> frozenset[str]:
    """The identifiers code uses — names, attributes, and parameters — read as bodies are."""
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            raw = child.id
        elif isinstance(child, ast.Attribute):
            raw = child.attr
        elif isinstance(child, ast.arg):
            raw = child.arg
        else:
            continue
        if name := bare_name(raw):
            names.add(name)
    return frozenset(names)


def _reach_names(node: ast.AST, helpers: Mapping[str, frozenset[str]]) -> frozenset[str]:
    """What a test uses, with what each same-file helper or fixture it names uses in turn."""
    names = set(_code_names(node))
    pending = [name for name in names if name in helpers]
    visited: set[str] = set()
    while pending:
        helper = pending.pop()
        if helper in visited:
            continue
        visited.add(helper)
        names |= helpers[helper]
        pending.extend(name for name in helpers[helper] if name in helpers)
    return frozenset(names)


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
    helpers = {
        bare_name(node.name): _code_names(node)
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("test")
    }
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
                reach_names=_reach_names(node, helpers),
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
    words = bare_name(CAMEL_BOUNDARY_RE.sub("_", name))
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


def _is_private(name: str) -> bool:
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def map_candidate_tests(
    candidates: list[SemanticCandidate],
    index: TestIndex,
    config: WeaverConfig,
    graph: CallGraph | None = None,
) -> dict[int, list[CandidateTest]]:
    """Return capped static candidates; terminology alone can never create a match.

    With ``graph``, a test is followed from the modules it imports or runs through every call
    their symbols make, into other modules through their imports, to the changed symbols it
    reaches, which no name or import in the test shows.
    """
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
                or imported.endswith(module_suffix)
                for imported in test.imports
            )
        )

    @cache
    def package_imported_for(module: str) -> frozenset[int]:
        return frozenset(
            position
            for position, test in enumerate(tests)
            if any(module.startswith(f"{imported}.") for imported in test.imports)
        )

    @cache
    def ran_for(module: str) -> frozenset[int]:
        # `python -m pkg` runs `pkg/__main__.py`, which no import reaches. Running any other
        # module relates a test to all of its symbols, as coarsely as an import already does.
        if not module.endswith(".__main__"):
            return frozenset()
        return frozenset(run_positions.get(module.removesuffix(".__main__"), ()))

    @cache
    def entries_of(position: int) -> frozenset[Node]:
        """The symbols a test starts from: what it names in each module it imports, and the
        top-level code of every module it imports or runs, which loading executes."""
        if graph is None:
            return frozenset()
        test = tests[position]
        entries: set[Node] = set()
        for imported_name in test.imports:
            found = graph.locate(imported_name)
            if found is not None:
                entries.add((found[0], "<module>"))
                entries.update(
                    (found[0], symbol)
                    for name in test.reach_names
                    for symbol in graph.named(found[0], name)
                )
        for program in test.modules_run:
            found = graph.locate(f"{program}.__main__")
            if found is not None and not found[1]:
                entries.add((found[0], "<module>"))
        return frozenset(entries)

    def reaches(position: int, path: str, ancestors: frozenset[Node], local: bool) -> bool:
        if entries_of(position) & ancestors:
            return True
        # A test related to the module by its file, not its imports, starts from what it names.
        return local and any(
            (path, symbol) in ancestors
            for name in tests[position].reach_names
            for symbol in (graph.named(path, name) if graph else ())
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
        # Importing a package reaches a module in it only if something along the way imports
        # that module. Counted as a direct import, `from distiller import cli` imported every
        # module of `distiller`, and with category terminology alone a test crossed the
        # threshold for modules it never touches. It now confirms a test name, never alone.
        package = package_imported_for(module) - imported
        ran = ran_for(module)
        # Whole identifiers: by its words, `run` was in the body of every test that called
        # `run_args_from(...)`, and every test name was in its own body through the `def` line.
        body_key = bare_name(name)
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
        in_graph = (
            graph is not None
            and candidate.path in graph.modules
            and candidate.symbol in graph.modules[candidate.path].symbols
        )
        reaching: set[int] = set()
        if graph is not None and in_graph:
            ancestors = graph.reaching((candidate.path, candidate.symbol))
            local = mapped | mirrored
            reaching = {
                position
                for position in range(len(tests))
                if reaches(position, candidate.path, ancestors, position in local)
            }
        related = mapped | mirrored | imported | ran
        if candidate.symbol == "<module>":
            # Loading a module runs its top-level code, which the import or run already counted,
            # and loading it through other modules is true of nearly every test.
            reached = reached_elsewhere = empty
        else:
            reached = reaching & related
            reached_elsewhere = reaching - related
        # A mention in the body and a reach are one piece of evidence, that the test exercises
        # the symbol, and the reach is the stronger, so it replaces the mention. A test calling
        # the symbol directly used to keep its mention, 0.10, below one reaching the symbol
        # through other calls, 0.15, while a reach from afar added to the mention.
        mentioned = bodied - reached - reached_elsewhere
        # Reaching the module through another is what importing its package could only suggest,
        # so the package import adds nothing more to such a test.
        package -= reached_elsewhere
        terminology = terminology_for(candidate.category)
        # Category words confirm a test tied to the change itself. In one that reaches it only
        # from afar they describe whatever that test is about, and they had end-to-end tests
        # outrank a unit test that imports the module and reaches the symbol.
        terminology -= reached_elsewhere - (
            mapped | mirrored | imported | ran | named | bodied | module_named
        )
        ranked: list[CandidateTest] = []
        # A mapping, a mirrored name, an import, a run, or a package is evidence about a test
        # file, shared by every test in it, so it cannot single out the tests that exercise the
        # changed symbol. Tests matched by it alone stand as their file: one `cli.py` helper got
        # the first five of eight tests in `test_cli.py`, alphabetically, as five candidates.
        specific = named | bodied | module_named | reached | reached_elsewhere
        by_file: dict[str, list[CandidateTest]] = defaultdict(list)
        # Only a structural hit can produce a candidate, so tests outside this union would
        # score identically to being skipped. Terminology alone never clears the threshold.
        structural = (
            mapped
            | mirrored
            | imported
            | package
            | ran
            | named
            | bodied
            | module_named
            | reached_elsewhere
        )
        if (
            graph is not None
            and in_graph
            and not graph.incomplete
            and _is_private(name)
            and not graph.modules[candidate.path].symbols[candidate.symbol].decorated
            and not (reaching or bodied or named)
            and (
                related
                or any(
                    entries_of(position) & graph.reaching((candidate.path, "<module>"))
                    for position in range(len(tests))
                )
            )
        ):
            # A private helper is called only through its own module. When tests load the
            # module and none reaches or names the helper, neither their file nor a test named
            # after the module is evidence that it is tested; only a configured mapping counts.
            # A graph cut short by its bounds rules nothing out.
            structural = mapped
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
            if test_position in package:
                score += 0.15
                reasons.append("changed module's package imported")
            if test_position in named:
                score += 0.20
                reasons.append("changed symbol token in test name")
            if test_position in mentioned:
                score += 0.10
                reasons.append("changed symbol token in bounded test body")
            # More than a body mention: the test exercises the symbol through what it calls.
            if test_position in reached:
                score += 0.15
                reasons.append("changed symbol reached through calls")
            # A candidate on its own, never a strong one: static reach through an orchestrator
            # takes every branch, so a test calling `service.analyze` reaches nearly everything.
            # It stays below a test that imports the module and reaches the symbol (0.40).
            if test_position in reached_elsewhere:
                score += 0.35
                reasons.append("changed symbol reached through another module's calls")
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
                item = CandidateTest(
                    path=test.path,
                    symbol=test.symbol,
                    match_score=round(score, 2),
                    match_reasons=reasons,
                )
                if test_position in specific:
                    ranked.append(item)
                else:
                    by_file[test.path].append(item)
        for items in by_file.values():
            best = max(items, key=lambda item: item.match_score)
            ranked.append(
                best if len(items) == 1 else best.model_copy(update={"symbol": WHOLE_TEST_FILE})
            )
        # Among equal scores, a test whose body names the symbol is about it, while one reaching
        # it through other calls may be about anything along the way.
        naming = {(tests[position].path, tests[position].symbol) for position in bodied}
        ranked.sort(
            key=lambda item: (
                -item.match_score,
                (item.path, item.symbol) not in naming,
                item.path,
                item.symbol,
            )
        )
        result[candidate_index] = ranked[: config.rules.max_candidate_tests_per_obligation]
    return result
