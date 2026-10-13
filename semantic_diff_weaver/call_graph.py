"""Which symbols of the analyzed repository reach which, across its modules.

Candidate mapping follows a test into the code it calls: from the modules the test imports,
through every call and reference their symbols make, and into other modules through those
modules' own imports. A test that calls `cli.main`, which calls
`messages.unexpected_failure`, reaches it although it never imports `messages`.

Modules are read at the head revision, starting from the ones tests import and the changed
ones and following imports from there, as untrusted source: parsed under the AST stage's
budgets, never imported or executed, and bounded like the candidate-test index.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath

from .ast_diff import AstResourceLimit, ModuleReferences, SymbolReferences, module_references
from .errors import WeaverError
from .git_diff import GitRepository
from .models import WeaverConfig
from .path_policy import exclusion_reason, glob_matches, is_test_support_path

MAX_CALL_GRAPH_FILES = 500
MAX_CALL_GRAPH_BYTES = 8 * 1024 * 1024
CALL_GRAPH_TIMEOUT_SECONDS = 5.0
# How many re-exporting imports a name is followed through, as `pkg/__init__.py` does with
# `from .analyze import analyze_ast`.
MAX_REEXPORT_HOPS = 5

Node = tuple[str, str]
"""A symbol of an indexed module: its path and qualified name."""


def bare_name(identifier: str) -> str:
    """An identifier casefolded and without the underscores that make it private.

    A test's `_serve(...)` helper wraps `serve`, so the two read alike. A dunder such as
    `__init__` is a protocol name rather than a word, and stays whole.
    """
    folded = identifier.casefold()
    if len(folded) > 4 and folded.startswith("__") and folded.endswith("__"):
        return folded
    return folded.strip("_")


def importable_name(path: str) -> str:
    """The dotted name code imports a file by.

    `src/distiller/cli.py` is `distiller.cli`, and a package's `__init__.py` is the package.
    """
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[0] in {"src", "lib"}:
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def package_of(path: str) -> str:
    """The package a file's relative imports resolve against."""
    name = importable_name(path)
    return name if PurePosixPath(path).name == "__init__.py" else name.rpartition(".")[0]


def _locator(paths: Iterable[str]) -> dict[str, str]:
    """Importable name, casefolded, to path; the first path in order wins a shared name."""
    located: dict[str, str] = {}
    for path in sorted(paths):
        located.setdefault(importable_name(path).casefold(), path)
    return located


def _locate(located: Mapping[str, str], dotted: str) -> tuple[str, list[str]] | None:
    """The file a dotted name's longest importable prefix names, and the rest of the name.

    A name written from the repository root, such as `src.distiller.cli`, is read without its
    `src` or `lib` too.
    """
    parts = dotted.split(".")
    starts = [parts, parts[1:]] if parts[0] in {"src", "lib"} else [parts]
    for candidate in starts:
        for size in range(len(candidate), 0, -1):
            path = located.get(".".join(candidate[:size]).casefold())
            if path is not None:
                return path, candidate[size:]
    return None


class CallGraph:
    """Which symbols of the indexed modules use which, through names and imports."""

    def __init__(
        self,
        modules: Mapping[str, ModuleReferences],
        *,
        incomplete: bool = False,
        warnings: Iterable[str] = (),
    ) -> None:
        self.modules = dict(modules)
        self.incomplete = incomplete
        self.warnings = list(warnings)
        self._located = _locator(self.modules)
        self._named: dict[str, dict[str, frozenset[str]]] = {}
        # Per module, each class's protocol methods, which making an instance runs although
        # nothing calls them by name.
        self._protocol: dict[str, dict[str, set[str]]] = {}
        for path, module in self.modules.items():
            named: dict[str, set[str]] = defaultdict(set)
            protocol: dict[str, set[str]] = defaultdict(set)
            for qualified in module.symbols:
                owner, _, leaf = qualified.rpartition(".")
                named[bare_name(leaf)].add(qualified)
                if owner in module.symbols and leaf.startswith("__") and leaf.endswith("__"):
                    protocol[owner].add(qualified)
            self._named[path] = {name: frozenset(items) for name, items in named.items()}
            self._protocol[path] = protocol
        self._resolved: dict[str, Node | None] = {}
        self._users: dict[Node, set[Node]] = defaultdict(set)
        for path, module in self.modules.items():
            for qualified, item in module.symbols.items():
                for target in self._uses(path, module, qualified, item):
                    self._users[target].add((path, qualified))
        self._reaching: dict[Node, frozenset[Node]] = {}

    def locate(self, dotted: str) -> tuple[str, list[str]] | None:
        """The indexed module a dotted name starts with, and the rest of the name."""
        return _locate(self._located, dotted)

    def named(self, path: str, name: str) -> frozenset[str]:
        """The symbols of a module whose own name reads as `name`, already bare."""
        return self._named.get(path, {}).get(name, frozenset())

    def resolve(self, dotted: str) -> Node | None:
        """The symbol an absolute dotted name stands for, through re-exporting imports."""
        if dotted not in self._resolved:
            self._resolved[dotted] = self._resolve(dotted, 0)
        return self._resolved[dotted]

    def _resolve(self, dotted: str, hops: int) -> Node | None:
        located = self.locate(dotted)
        if located is None:
            return None
        path, rest = located
        if not rest:
            return None
        module = self.modules[path]
        # `mod.Class.method` names the method; `mod.Class.attribute` still names the class.
        for size in range(len(rest), 0, -1):
            qualified = ".".join(rest[:size])
            if qualified in module.symbols:
                return path, qualified
        target = module.imports.get(rest[0])
        if target is None or hops >= MAX_REEXPORT_HOPS:
            return None
        return self._resolve(".".join([target, *rest[1:]]), hops + 1)

    def _uses(
        self, path: str, module: ModuleReferences, qualified: str, item: SymbolReferences
    ) -> set[Node]:
        named = self._named[path]
        uses: set[Node] = set()
        for name in item.names:
            # A name in the module's own code, by name alone: `self._helper()` reaches the
            # method, at the cost of every other symbol called `_helper`.
            uses.update((path, symbol) for symbol in named.get(bare_name(name), ()))
            if (bound := module.imports.get(name)) and (node := self.resolve(bound)):
                uses.add(node)
        for chain in item.dotted:
            root, _, rest = chain.partition(".")
            if (bound := module.imports.get(root)) and (node := self.resolve(f"{bound}.{rest}")):
                uses.add(node)
        uses.update((path, symbol) for symbol in self._protocol[path].get(qualified, ()))
        if qualified == "<module>":
            # Loading a module loads what it imports, which runs their top-level code.
            for loaded in module.loads:
                found = self.locate(loaded)
                if found and "<module>" in self.modules[found[0]].symbols:
                    uses.add((found[0], "<module>"))
        uses.discard((path, qualified))
        return uses

    def reaching(self, node: Node) -> frozenset[Node]:
        """Every symbol from which `node` is reached, itself included."""
        cached = self._reaching.get(node)
        if cached is None:
            seen = {node}
            pending = [node]
            while pending:
                for user in self._users.get(pending.pop(), ()):
                    if user not in seen:
                        seen.add(user)
                        pending.append(user)
            cached = self._reaching[node] = frozenset(seen)
        return cached


def build_call_graph(
    repo: GitRepository,
    head_commit: str,
    config: WeaverConfig,
    *,
    imports: Iterable[str],
    changed: Iterable[str],
) -> CallGraph:
    """Index the modules tests import and the changed ones, following imports, under bounds.

    Reaching a bound makes the graph incomplete, which stops it from ruling a helper untested
    but keeps every reach it did find.
    """
    try:
        repository_files = repo.list_files(head_commit)
    except WeaverError:
        return CallGraph(
            {},
            incomplete=True,
            warnings=["Following calls across modules exceeded a safe Git boundary."],
        )
    product = [
        path
        for path in repository_files
        if path.endswith(".py")
        and not exclusion_reason(path)
        and not any(glob_matches(path, pattern) for pattern in config.paths.exclude)
        and not is_test_support_path(path, config.paths.test_roots)
    ]
    located = _locator(product)
    known = set(product)
    seeds = {path for path in changed if path in known}
    seeds.update(found[0] for name in imports if (found := _locate(located, name)))
    pending = sorted(seeds)
    seen = set(pending)
    modules: dict[str, ModuleReferences] = {}
    warnings: list[str] = []
    incomplete = False
    read_bytes = 0
    deadline = time.monotonic() + CALL_GRAPH_TIMEOUT_SECONDS
    while pending:
        room = MAX_CALL_GRAPH_FILES - len(modules)
        if len(pending) > room:
            incomplete = True
            warnings.append(
                f"Following calls across modules was capped at {MAX_CALL_GRAPH_FILES} modules; "
                "tests that reach a change through other modules may be missing."
            )
            pending = pending[:room]
        entries = repo.tree_entries(head_commit, pending)
        eligible = {
            path: entry for path, entry in entries.items() if entry.mode not in {"120000", "160000"}
        }
        texts = repo.read_blob_objects(
            {entry.object_id for entry in eligible.values()},
            config.rules.max_file_bytes,
            max_total_bytes=max(0, MAX_CALL_GRAPH_BYTES - read_bytes),
        )
        following: set[str] = set()
        for path in pending:
            entry = eligible.get(path)
            source = texts.get(entry.object_id) if entry else None
            if source is None or time.monotonic() > deadline:
                incomplete = True
                continue
            read_bytes += len(source.encode("utf-8"))
            try:
                module = module_references(source, package_of(path))
            except (
                AstResourceLimit,
                MemoryError,
                RecursionError,
                SyntaxError,
                TypeError,
                ValueError,
            ):
                incomplete = True
                continue
            modules[path] = module
            for name in (*module.imports.values(), *module.loads):
                found = _locate(located, name)
                if found and found[0] not in seen:
                    seen.add(found[0])
                    following.add(found[0])
        pending = sorted(following)
    if incomplete and not warnings:
        warnings.append(
            "At least one module could not be read within bounds for following calls; tests "
            "that reach a change through it may be missing."
        )
    return CallGraph(modules, incomplete=incomplete, warnings=warnings)
