"""Static discovery and ranking of unverified candidate existing tests."""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from functools import cache, lru_cache
from itertools import pairwise
from pathlib import PurePosixPath
from typing import ClassVar, TypeGuard

from .call_graph import CallGraph, Node, bare_name, package_of
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
# How deeply a module name built from literals is followed: `"pkg." + name`, an f-string.
MAX_NAME_EXPRESSION_DEPTH = 16
MAX_TEST_INDEX_FILES = 500
MAX_TEST_INDEX_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class IndexedTest:
    path: str
    symbol: str
    # As written, since case tells `from shop import Cart`, a class `shop/__init__.py` may
    # re-export, from the module `shop.cart`. Matching a changed module by name folds it.
    imports: frozenset[str]
    # Words as well as whole identifiers: category terminology reads these.
    name_tokens: frozenset[str]
    body_tokens: frozenset[str]
    # Whole identifiers, for whether the body uses a changed symbol or module by name.
    body_identifiers: frozenset[str] = frozenset()
    # Modules the test's file runs as a program, as `python -m` or `runpy` does, as written.
    modules_run: frozenset[str] = frozenset()
    # Identifiers the test uses together with those of the same file's helpers and fixtures it
    # names: `run_args_from(...)` reaches `cli.main` although the test never spells `main`.
    reach_names: frozenset[str] = frozenset()
    # The attributes among them, which alone call a method on an instance the test holds.
    reach_attributes: frozenset[str] = frozenset()
    # Names the test's file binds to what it loads by name, each with the absolute name it
    # stands for: `add_dish = _load_handler("add_dish")` is `src.handlers.add_dish.HANDLER`.
    bound: frozenset[tuple[str, str]] = frozenset()


@dataclass
class TestIndex:
    __test__: ClassVar[bool] = False
    tests: list[IndexedTest]
    incomplete: bool
    warnings: list[str]


def _static_imports(nodes: Iterable[ast.AST]) -> set[str]:
    """What the `import` statements among `nodes` import."""
    imports: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
                imports.update(f"{node.module}.{alias.name}" for alias in node.names)
            elif node.level:
                imports.update(alias.name for alias in node.names)
    return imports


def _outside(tree: ast.AST, excluded: set[int]) -> Iterator[ast.AST]:
    """Every node of `tree` except those inside the excluded ones, by identity."""
    pending = [tree]
    while pending:
        node = pending.pop()
        if id(node) not in excluded:
            yield node
            pending.extend(ast.iter_child_nodes(node))


def _loads_module(node: ast.AST) -> TypeGuard[ast.Call]:
    """Whether a call is `importlib.import_module`, written either way."""
    if not isinstance(node, ast.Call):
        return False
    function = node.func
    if isinstance(function, ast.Name):
        return function.id == "import_module"
    return (
        isinstance(function, ast.Attribute)
        and function.attr == "import_module"
        and isinstance(function.value, ast.Name)
        and function.value.id == "importlib"
    )


def _text(node: ast.AST | None, constants: Mapping[str, str], depth: int = 0) -> str | None:
    """The string an expression always is: a literal, a name the file binds to one, or an
    f-string or concatenation of those. None for anything known only when it runs."""
    if node is None or depth > MAX_NAME_EXPRESSION_DEPTH:
        return None
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.FormattedValue):
                if value.conversion != -1 or value.format_spec is not None:
                    return None
                value = value.value
            text = _text(value, constants, depth + 1)
            if text is None:
                return None
            parts.append(text)
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _text(node.left, constants, depth + 1)
        right = _text(node.right, constants, depth + 1)
        return None if left is None or right is None else left + right
    return None


def _argument(call: ast.Call, position: int, keyword: str) -> ast.expr | None:
    if position < len(call.args):
        return call.args[position]
    return next((item.value for item in call.keywords if item.arg == keyword), None)


def _loaded(call: ast.Call, constants: Mapping[str, str], package: str) -> str | None:
    """The absolute name of the module an `import_module` call loads, when the file says it.

    A relative name resolves against the package the call names, or, when that is known only
    at run time, against the test file's own: `meal-manager` passes `Path(__file__).parent`'s
    name to load `.src.dish` from the package its tests live in.
    """
    name = _text(_argument(call, 0, "name"), constants)
    if not name:
        return None
    level = len(name) - len(name.lstrip("."))
    if level:
        anchor_name = _text(_argument(call, 1, "package"), constants)
        anchor = (package if anchor_name is None else anchor_name).split(".")
        anchor = [part for part in anchor if part]
        if level - 1 > len(anchor):
            return None
        name = ".".join([*anchor[: len(anchor) - (level - 1)], *filter(None, [name[level:]])])
    return name if MODULE_NAME_RE.fullmatch(name) else None


@dataclass(frozen=True)
class _Loader:
    """A same-file function that loads a module named by its arguments, as a test's
    `_load_handler(module_suffix)` loads `f".src.handlers.{module_suffix}"`."""

    parameters: tuple[str, ...]
    calls: tuple[ast.Call, ...]
    # What of the module it loads the function returns: "" for the module itself, `HANDLER`
    # for `return mod.HANDLER`, and None for anything else.
    returns: str | None


def _loader(node: ast.FunctionDef | ast.AsyncFunctionDef) -> _Loader | None:
    calls = tuple(item for item in ast.walk(node) if _loads_module(item))
    if not calls:
        return None
    holders = {
        item.targets[0].id
        for item in ast.walk(node)
        if isinstance(item, ast.Assign)
        and len(item.targets) == 1
        and isinstance(item.targets[0], ast.Name)
        and _loads_module(item.value)
    }
    returns: str | None = None
    for item in ast.walk(node):
        if isinstance(item, ast.Return) and item.value is not None:
            value = item.value
            if _loads_module(value) or (isinstance(value, ast.Name) and value.id in holders):
                returns = ""
            elif isinstance(value, ast.Attribute) and (chain := _chain(value)):
                root, _, rest = chain.partition(".")
                returns = rest if root in holders else None
            break
    parameters = tuple(arg.arg for arg in [*node.args.posonlyargs, *node.args.args])
    return _Loader(parameters=parameters, calls=calls, returns=returns)


def _chain(node: ast.Attribute) -> str | None:
    """`a.b.c` for an attribute chain rooted at a name."""
    parts = [node.attr]
    value = node.value
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if not isinstance(value, ast.Name):
        return None
    parts.append(value.id)
    return ".".join(reversed(parts))


class _DynamicImports:
    """The modules a test file loads by name, as `importlib.import_module` does, and what each
    name it assigns from one stands for.

    `dish_mod = importlib.import_module(".src.dish", PACKAGE)` loads `src.dish` and binds
    `dish_mod` to it, `Dish = dish_mod.Dish` binds `Dish` to `src.dish.Dish`, and a same-file
    function that builds the name from its arguments loads one module for each call that
    passes them written out. A name known only when the test runs loads nothing the index can
    tell.
    """

    def __init__(self, tree: ast.Module, package: str) -> None:
        self.package = package
        self.constants = {
            statement.targets[0].id: statement.value.value
            for statement in tree.body
            if isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        }
        self.loaders = {
            statement.name: found
            for statement in tree.body
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not statement.name.startswith("test")
            and (found := _loader(statement)) is not None
        }

    def load(self, call: ast.Call) -> tuple[set[str], str | None]:
        """What a call loads, and what it returns: a module or one of its attributes."""
        if _loads_module(call):
            module = _loaded(call, self.constants, self.package)
            return ({module} if module else set()), module
        if not isinstance(call.func, ast.Name) or call.func.id not in self.loaders:
            return set(), None
        loader = self.loaders[call.func.id]
        arguments = dict(self.constants)
        for name, value in zip(loader.parameters, call.args, strict=False):
            if (text := _text(value, self.constants)) is not None:
                arguments[name] = text
        for keyword in call.keywords:
            if keyword.arg and (text := _text(keyword.value, self.constants)) is not None:
                arguments[keyword.arg] = text
        modules = {
            module for item in loader.calls if (module := _loaded(item, arguments, self.package))
        }
        returned = None
        if loader.returns is not None and len(modules) == 1:
            [module] = modules
            returned = f"{module}.{loader.returns}" if loader.returns else module
        return modules, returned

    def scan(
        self, nodes: Iterable[ast.AST], bound: Mapping[str, str]
    ) -> tuple[set[str], dict[str, str]]:
        """What `nodes` load, and the names they bind, given what is `bound` already."""
        imports: set[str] = set()
        binding = dict(bound)
        ordered = sorted(
            (item for item in nodes if isinstance(item, (ast.Assign, ast.Call))),
            key=lambda item: (item.lineno, item.col_offset),
        )
        for item in ordered:
            if isinstance(item, ast.Call):
                imports |= self.load(item)[0]
            elif len(item.targets) == 1 and isinstance(item.targets[0], ast.Name):
                value = item.value
                target: str | None = None
                if isinstance(value, ast.Call):
                    target = self.load(value)[1]
                elif isinstance(value, ast.Name):
                    target = binding.get(value.id)
                elif isinstance(value, ast.Attribute) and (chain := _chain(value)):
                    root, _, rest = chain.partition(".")
                    target = f"{binding[root]}.{rest}" if root in binding else None
                if target is not None:
                    binding[item.targets[0].id] = target
        return imports, binding


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


def _attributes(node: ast.AST) -> frozenset[str]:
    """The attributes code uses, read as bodies are."""
    return frozenset(
        name
        for child in ast.walk(node)
        if isinstance(child, ast.Attribute) and (name := bare_name(child.attr))
    )


def _helpers_named(names: frozenset[str], helpers: Mapping[str, frozenset[str]]) -> set[str]:
    """The same-file helpers and fixtures code names, with those they name in turn."""
    pending = [name for name in names if name in helpers]
    visited: set[str] = set()
    while pending:
        helper = pending.pop()
        if helper not in visited:
            visited.add(helper)
            pending.extend(name for name in helpers[helper] if name in helpers)
    return visited


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
    tests = list(_test_functions(tree.body))
    # An import in a test's own body is that test's; the rest of the file imports for all.
    shared = list(_outside(tree, {id(node) for _, node in tests}))
    dynamic = _DynamicImports(tree, package_of(path))
    file_loaded, file_bound = dynamic.scan(shared, {})
    file_imports = _static_imports(shared) | file_loaded
    runs_visitor = _ModuleRunVisitor()
    runs_visitor.visit(tree)
    modules_run = frozenset(runs_visitor.modules)
    helper_nodes = {
        bare_name(node.name): node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("test")
    }
    helpers = {name: _code_names(node) for name, node in helper_nodes.items()}
    indexed: list[IndexedTest] = []
    for symbol, node in tests:
        body_text = redact_text(ast.unparse(node), max_chars=4000)
        names = _code_names(node)
        named_helpers = _helpers_named(names, helpers)
        own = list(ast.walk(node))
        loaded, bound = dynamic.scan(own, file_bound)
        indexed.append(
            IndexedTest(
                path=path,
                symbol=symbol,
                imports=frozenset(file_imports | _static_imports(own) | loaded),
                name_tokens=_tokens(node.name),
                body_tokens=_tokens(body_text),
                body_identifiers=_identifiers(body_text),
                modules_run=modules_run,
                reach_names=names.union(*(helpers[helper] for helper in named_helpers)),
                reach_attributes=_attributes(node).union(
                    *(_attributes(helper_nodes[helper]) for helper in named_helpers)
                ),
                bound=frozenset(bound.items()),
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
    reaches, which no name or import in the test shows. A test that reaches a class may hold
    one of its instances, and reaches the methods it calls on it by name.
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
    # Every attribute a test's code uses, with its same-file helpers': a method it calls on an
    # instance shows only as one.
    attribute_positions: dict[str, set[int]] = defaultdict(set)
    all_tokens_by_test: list[frozenset[str]] = []
    # Changed modules and symbols are matched by name, regardless of case.
    folded_imports = [frozenset(name.casefold() for name in test.imports) for test in tests]
    for position, test in enumerate(tests):
        all_tokens_by_test.append(test.name_tokens | test.body_tokens)
        for attribute in test.reach_attributes:
            attribute_positions[attribute].add(position)
        for identifier in test.body_identifiers:
            body_identifier_positions[identifier].add(position)
        for module_run in test.modules_run:
            run_positions[module_run.casefold()].add(position)
        for import_path in folded_imports[position]:
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
            for position, imports in enumerate(folded_imports)
            if any(
                imported == module
                or imported.startswith(module_prefix)
                or imported.endswith(module_suffix)
                for imported in imports
            )
        )

    @cache
    def package_imported_for(module: str) -> frozenset[int]:
        return frozenset(
            position
            for position, imports in enumerate(folded_imports)
            if any(module.startswith(f"{imported}.") for imported in imports)
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
            # `from shop import Cart` names a class that `shop/__init__.py` only re-exports. The
            # file imports it for all of its tests, and only those that use it start from it.
            if bare_name(imported_name.rpartition(".")[2]) in test.reach_names and (
                imported_symbol := graph.resolve(imported_name)
            ):
                entries.add(imported_symbol)
        # `list_fridge = _load_handler("list_fridge")` is the module's `HANDLER` by another name.
        for name, target in test.bound:
            if bare_name(name) in test.reach_names and (symbol := graph.resolve(target)):
                entries.add(symbol)
        for program in test.modules_run:
            found = graph.locate(f"{program}.__main__")
            if found is not None and not found[1]:
                entries.add((found[0], "<module>"))
        return frozenset(entries)

    @cache
    def class_reachers(cls: Node) -> frozenset[int]:
        """The tests that reach a class, and so may hold one of its instances."""
        ancestors = graph.reaching(cls) if graph is not None else frozenset()
        return frozenset(
            position for position in range(len(tests)) if entries_of(position) & ancestors
        )

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
            # `cart = make_cart(); cart.total()`: a test that reaches a class through its calls
            # and uses a method's name as an attribute reaches the method. Only the classes its
            # own entries reach count, not those of the methods it reaches this way.
            for method in ancestors & graph.methods:
                callers = (
                    attribute_positions.get(bare_name(method[1].rpartition(".")[2]), empty)
                    - reaching
                )
                for receiver in graph.receivers(method) if callers else ():
                    reaching |= callers & class_reachers(receiver)
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
