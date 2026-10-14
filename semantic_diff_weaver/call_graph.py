"""Which symbols of the analyzed repository reach which, across its modules.

Candidate mapping follows a test into the code it calls: from the modules the test imports,
through every call and reference their symbols make, and into other modules through those
modules' own imports. A test that calls `cli.main`, which calls
`messages.unexpected_failure`, reaches it although it never imports `messages`.

A call on an instance, `cart.total()`, names no class, so it is followed through the classes
the calling code knows: those it names, wherever they live, those the functions it calls are
annotated to return, those the module-level variables it uses hold, imported or not, and the
classes their instances hold in fields.

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

# What extraction calls a function defined in a class body, which an instance reaches by name.
METHOD_KINDS = frozenset({"method", "async_method"})


def bare_name(identifier: str) -> str:
    """An identifier casefolded and without the underscores that make it private.

    A test's `_serve(...)` helper wraps `serve`, so the two read alike. A dunder such as
    `__init__` is a protocol name rather than a word, and stays whole.
    """
    folded = identifier.casefold()
    if len(folded) > 4 and folded.startswith("__") and folded.endswith("__"):
        return folded
    return folded.strip("_")


# Directories that hold a repository's packages rather than being one.
SOURCE_ROOTS = frozenset({"src", "lib"})


def source_roots(paths: Iterable[str]) -> frozenset[str]:
    """The source roots of a repository: a `src` or `lib` with an `__init__.py` is a package."""
    files = set(paths)
    return frozenset(root for root in SOURCE_ROOTS if f"{root}/__init__.py" not in files)


def importable_name(path: str, roots: frozenset[str] = SOURCE_ROOTS) -> str:
    """The dotted name code imports a file by.

    `src/distiller/cli.py` is `distiller.cli`, and a package's `__init__.py` is the package.
    A `src` that is a package itself, as `meal-manager`'s is, keeps its name: `src/dish.py` is
    `src.dish`, the module that `from .. import dish` in `src/handlers` climbs to.
    """
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[0] in roots:
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def package_of(path: str, roots: frozenset[str] = SOURCE_ROOTS) -> str:
    """The package a file's relative imports resolve against."""
    name = importable_name(path, roots)
    return name if PurePosixPath(path).name == "__init__.py" else name.rpartition(".")[0]


def _locator(paths: Iterable[str], roots: frozenset[str]) -> dict[str, str]:
    """Importable name to path; the first path in order wins a shared name.

    Case counts, as it does for Python's imports: `shop.Cart` is what `shop/__init__.py` binds,
    perhaps a class it re-exports, and never the module `shop/cart.py`. A module under a `src`
    package is also found by its name without `src`, after every other name.
    """
    ordered = sorted(paths)
    located: dict[str, str] = {}
    for names in (roots, SOURCE_ROOTS):
        for path in ordered:
            located.setdefault(importable_name(path, names), path)
    return located


def _closure(node: Node, edges: Mapping[Node, Iterable[Node]]) -> frozenset[Node]:
    """`node` and everything its edges lead to, transitively."""
    seen = {node}
    pending = [node]
    while pending:
        for following in edges.get(pending.pop(), ()):
            if following not in seen:
                seen.add(following)
                pending.append(following)
    return frozenset(seen)


def _locate(located: Mapping[str, str], dotted: str) -> tuple[str, list[str]] | None:
    """The file a dotted name's longest importable prefix names, and the rest of the name.

    A name written from the repository root, such as `src.distiller.cli`, is read without its
    `src` or `lib` too. One that starts with a dot is relative to the repository's root package,
    `""`, as a test loading its own package names it: `.register` is a module of that name, or
    what the root `__init__.py` binds.
    """
    parts = dotted.split(".")
    if parts[0] in {"src", "lib"}:
        starts = [parts, parts[1:]]
    elif not parts[0]:
        starts = [parts[1:], parts]
    else:
        starts = [parts]
    for candidate in starts:
        for size in range(len(candidate), 0, -1):
            path = located.get(".".join(candidate[:size]))
            if path is not None:
                return path, candidate[size:]
    return None


class CallGraph:
    """Which symbols of the indexed modules use which, through names, imports, and instances."""

    def __init__(
        self,
        modules: Mapping[str, ModuleReferences],
        *,
        incomplete: bool = False,
        warnings: Iterable[str] = (),
        roots: frozenset[str] | None = None,
    ) -> None:
        self.modules = dict(modules)
        self.incomplete = incomplete
        self.warnings = list(warnings)
        self._located = _locator(
            self.modules, source_roots(self.modules) if roots is None else roots
        )
        self._named: dict[str, dict[str, frozenset[str]]] = {}
        # Per module, each class's protocol methods, which making an instance runs although
        # nothing calls them by name.
        self._protocol: dict[str, dict[str, set[str]]] = {}
        # Per class, its own methods by bare name, which a call on one of its instances reaches.
        self._methods: dict[Node, dict[str, frozenset[str]]] = {}
        for path, module in self.modules.items():
            named: dict[str, set[str]] = defaultdict(set)
            protocol: dict[str, set[str]] = defaultdict(set)
            methods: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
            for qualified, item in module.symbols.items():
                owner, _, leaf = qualified.rpartition(".")
                named[bare_name(leaf)].add(qualified)
                if owner in module.symbols and leaf.startswith("__") and leaf.endswith("__"):
                    protocol[owner].add(qualified)
                if item.kind in METHOD_KINDS:
                    methods[owner][bare_name(leaf)].add(qualified)
            self._named[path] = {name: frozenset(items) for name, items in named.items()}
            self._protocol[path] = protocol
            for owner, by_name in methods.items():
                self._methods[(path, owner)] = {
                    name: frozenset(items) for name, items in by_name.items()
                }
        # Every method of the indexed classes.
        self.methods = frozenset(
            (path, method)
            for (path, _), by_name in self._methods.items()
            for items in by_name.values()
            for method in items
        )
        self._resolved: dict[str, Node | None] = {}
        # Per class, the classes it names as bases that the indexed modules define, and the
        # reverse: which classes inherit from it directly.
        self._bases: dict[Node, frozenset[Node]] = {}
        self._derived: dict[Node, set[Node]] = defaultdict(set)
        for path, module in self.modules.items():
            for qualified, item in module.symbols.items():
                if item.kind == "class":
                    bases = frozenset(
                        base
                        for written in item.bases
                        if (base := self._class(path, module, written)) is not None
                    )
                    self._bases[(path, qualified)] = bases
                    for base in bases:
                        self._derived[base].add((path, qualified))
        # Per function, the classes its return annotation names.
        self._returns: dict[Node, frozenset[Node]] = {}
        for path, module in self.modules.items():
            for qualified, item in module.symbols.items():
                returned = frozenset(
                    cls
                    for written in item.returns
                    if (cls := self._class(path, module, written)) is not None
                )
                if returned:
                    self._returns[(path, qualified)] = returned
        self._heirs: dict[Node, frozenset[Node]] = {}
        # What each module-level variable holds, and each dotted name that names one.
        self._holdings: dict[tuple[str, str], frozenset[Node]] = {}
        self._instances: dict[str, frozenset[Node]] = {}
        # What each module-level variable's value names, and each dotted name naming one.
        self._contents: dict[tuple[str, str], frozenset[Node]] = {}
        self._imported_contents: dict[str, frozenset[Node]] = {}
        uses = {
            (path, qualified): self._uses(path, module, qualified, item)
            for path, module in self.modules.items()
            for qualified, item in module.symbols.items()
        }
        self._call_methods(uses)
        self._users: dict[Node, set[Node]] = defaultdict(set)
        for node, targets in uses.items():
            targets.discard(node)
            for target in targets:
                self._users[target].add(node)
        self._reaching: dict[Node, frozenset[Node]] = {}
        self._callers: dict[Node, frozenset[Node]] = {}
        self._calling: dict[Node, set[Node]] | None = None

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

    def _symbol(self, path: str, module: ModuleReferences, written: str) -> Node | None:
        """The symbol a name written in a module stands for: its own, or one an import binds."""
        if written in module.symbols:
            return path, written
        root, _, rest = written.partition(".")
        bound = module.imports.get(root)
        if not bound:
            return None
        return self.resolve(f"{bound}.{rest}" if rest else bound)

    def _class(self, path: str, module: ModuleReferences, written: str) -> Node | None:
        """The class a name written in a module stands for: its own, or one an import binds."""
        node = self._symbol(path, module, written)
        if node is None or self.modules[node[0]].symbols[node[1]].kind != "class":
            return None
        return node

    def instances(self, dotted: str) -> frozenset[Node]:
        """The classes whose instances an absolute dotted name holds, when it names a module-level
        variable, as `repositories.dish_repo` does, or a method called on one; none otherwise.

        Followed through re-exporting imports like `resolve`.
        """
        if dotted not in self._instances:
            # A cycle of re-exports holds nothing.
            self._instances[dotted] = frozenset()
            self._instances[dotted] = self._find_instances(dotted, 0)
        return self._instances[dotted]

    def _find_instances(self, dotted: str, hops: int) -> frozenset[Node]:
        located = self.locate(dotted)
        if located is None or not located[1]:
            return frozenset()
        path, (variable, *attributes) = located
        module = self.modules[path]
        if variable in module.variables and variable not in module.symbols:
            return self._through(self._holding(path, variable), attributes)
        target = module.imports.get(variable)
        if target is None or hops >= MAX_REEXPORT_HOPS:
            return frozenset()
        return self._find_instances(".".join([target, *attributes]), hops + 1)

    def _contents_of(self, path: str, variable: str) -> frozenset[Node]:
        """The symbols a module-level variable's value names: the functions of a dispatch
        table, through the variables and imports it copies."""
        key = (path, variable)
        if key not in self._contents:
            # `a = b` and `b = a` name nothing more than what else they are assigned.
            self._contents[key] = frozenset()
            module = self.modules[path]
            found: set[Node] = set()
            for written in module.tables.get(variable, frozenset()):
                if (node := self._symbol(path, module, written)) is not None:
                    found.add(node)
                elif written in module.variables and written not in module.symbols:
                    found |= self._contents_of(path, written)
                elif bound := module.imports.get(written):
                    found |= self.contents(bound)
            self._contents[key] = frozenset(found)
        return self._contents[key]

    def contents(self, dotted: str) -> frozenset[Node]:
        """The symbols named by the value of the module-level variable an absolute dotted name
        names, through re-exporting imports like `resolve`; none for anything else."""
        if dotted not in self._imported_contents:
            # A cycle of re-exports names nothing.
            self._imported_contents[dotted] = frozenset()
            self._imported_contents[dotted] = self._find_contents(dotted, 0)
        return self._imported_contents[dotted]

    def _find_contents(self, dotted: str, hops: int) -> frozenset[Node]:
        located = self.locate(dotted)
        if located is None or len(located[1]) != 1:
            return frozenset()
        path, [variable] = located
        module = self.modules[path]
        if variable in module.variables and variable not in module.symbols:
            return self._contents_of(path, variable)
        target = module.imports.get(variable)
        if target is None or hops >= MAX_REEXPORT_HOPS:
            return frozenset()
        return self._find_contents(target, hops + 1)

    def _holding(self, path: str, variable: str) -> frozenset[Node]:
        """The classes whose instances a module-level variable holds."""
        key = (path, variable)
        if key not in self._holdings:
            # `a = b` and `b = a` hold nothing more than what else they are assigned.
            self._holdings[key] = frozenset()
            module = self.modules[path]
            self._holdings[key] = frozenset(
                cls
                for written in module.variables[variable]
                for cls in self._value(path, module, written)
            )
        return self._holdings[key]

    def _value(self, path: str, module: ModuleReferences, written: str) -> frozenset[Node]:
        """The classes whose instances a name written in a module stands for as a value: a
        class itself, what a function returns, or what a module-level variable holds."""
        node = self._symbol(path, module, written)
        if node is not None:
            if self.modules[node[0]].symbols[node[1]].kind == "class":
                return frozenset((node,))
            return self._returns.get(node, frozenset())
        root, _, rest = written.partition(".")
        if root in module.variables and root not in module.symbols:
            return self._through(self._holding(path, root), rest.split(".") if rest else [])
        if bound := module.imports.get(root):
            return self.instances(f"{bound}.{rest}" if rest else bound)
        return frozenset()

    def _through(self, classes: frozenset[Node], attributes: list[str]) -> frozenset[Node]:
        """What instances of `classes` give through `attributes`: themselves, or what a method of
        theirs returns, `dish_repo.load()` being whatever `load` returns."""
        if not attributes:
            return classes
        if len(attributes) > 1:
            return frozenset()
        name = bare_name(attributes[0])
        return frozenset(
            returned
            for cls in classes
            for ancestor in _closure(cls, self._bases)
            for method in self._methods.get(ancestor, {}).get(name, ())
            for returned in self._returns.get((ancestor[0], method), ())
        )

    def _family(self, node: Node) -> Node | None:
        """The class whose instance `self` is in a symbol: its own, or the nearest enclosing."""
        path, qualified = node
        while qualified:
            if (path, qualified) in self._bases:
                return path, qualified
            qualified = qualified.rpartition(".")[0]
        return None

    def _uses(
        self, path: str, module: ModuleReferences, qualified: str, item: SymbolReferences
    ) -> set[Node]:
        named = self._named[path]
        uses: set[Node] = set()
        for name in item.names | item.attributes | item.own_attributes:
            # A name in the module's own code, by name alone: `self._helper()` reaches the
            # method, at the cost of every other symbol called `_helper`.
            uses.update((path, symbol) for symbol in named.get(bare_name(name), ()))
        for name in item.names:
            # An attribute is never what an import binds: `self.messages` is not `messages`.
            if (bound := module.imports.get(name)) and (node := self.resolve(bound)):
                uses.add(node)
            # A table of functions is how code calls them later: `classify_delta` runs each of
            # the classifiers `CLASSIFIERS` names, which no call names.
            if name in module.variables and name not in module.symbols:
                uses |= self._contents_of(path, name)
            elif bound:
                uses |= self.contents(bound)
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
        return uses

    def _call_methods(self, uses: Mapping[Node, set[Node]]) -> None:
        """Add to each symbol's uses the methods it may call on instances of the classes it knows.

        `cart.total()` names no class, so the call is followed through the classes the symbol
        knows, wherever they live: those it uses — making one, annotating with it, or inheriting
        from it — those the functions it calls are annotated to return, and those the
        module-level variables it uses hold, its own module's or imported ones, such as
        `dish_repo = JsonDishRepository(path)`. An instance holds what its class inherits and the
        instances its class body names, as a dataclass's fields do. A class's members also know
        the class and whatever any of them knows, since `self.cart = Cart()` in `__init__` holds
        what `self.cart.total()` calls in another method, while `self.path` reads a member of
        the class itself. Any other attribute the symbol uses reaches the method of that name on
        each class it so knows, and what that method returns is known in turn. A subclass's override is reached only where the subclass is known: following
        every override from a base class would credit a test of one subclass with all of its
        siblings.
        """
        named: dict[Node, set[Node]] = {}
        for node, targets in uses.items():
            path, qualified = node
            module = self.modules[path]
            item = module.symbols[qualified]
            classes = {
                cls
                for target in targets
                for cls in ((target,) if target in self._bases else self._returns.get(target, ()))
            }
            for name in item.names:
                if name in module.variables and name not in module.symbols:
                    classes |= self._holding(path, name)
                elif bound := module.imports.get(name):
                    classes |= self.instances(bound)
            for chain in item.dotted:
                root, _, rest = chain.partition(".")
                if bound := module.imports.get(root):
                    classes |= self.instances(f"{bound}.{rest}")
            named[node] = classes
        holds = {cls: self._bases[cls] | named[cls] for cls in self._bases}
        held: dict[Node, frozenset[Node]] = {}
        # The attributes each symbol uses: the names of the methods it may call.
        calls = {
            (path, qualified): frozenset(bare_name(name) for name in item.attributes)
            for path, module in self.modules.items()
            for qualified, item in module.symbols.items()
        }

        def known(start: Iterable[Node], called: frozenset[str]) -> set[Node]:
            """The classes code knows from `start`: what their instances hold, and what the
            methods it calls on them return, transitively."""
            seen: set[Node] = set()
            pending = list(start)
            while pending:
                cls = pending.pop()
                if cls in seen:
                    continue
                if cls not in held:
                    held[cls] = _closure(cls, holds)
                for item in held[cls] - seen:
                    seen.add(item)
                    for name, methods in self._methods.get(item, {}).items():
                        if name in called:
                            for method in methods:
                                pending.extend(self._returns.get((item[0], method), ()))
            return seen

        own = {node: known(named[node], calls[node]) for node in uses}
        shared: dict[Node, set[Node]] = defaultdict(set)
        for node, classes in own.items():
            if (family := self._family(node)) is not None:
                shared[family].update(classes, (family,))
        for node, targets in uses.items():
            path, qualified = node
            family = self._family(node)
            start = set(own[node])
            if family is not None:
                start |= shared[family]
                # `self.path` is a member of the method's own class or of one it inherits from,
                # never of another class its class knows.
                own_calls = {
                    bare_name(name) for name in self.modules[path].symbols[qualified].own_attributes
                }
                for cls in _closure(family, self._bases):
                    for name, methods in self._methods.get(cls, {}).items():
                        if name in own_calls:
                            for method in methods:
                                targets.add((cls[0], method))
                                start.update(self._returns.get((cls[0], method), ()))
            if not calls[node]:
                continue
            for cls in known(start, calls[node]):
                for name, methods in self._methods.get(cls, {}).items():
                    if name in calls[node]:
                        targets.update((cls[0], method) for method in methods)

    def receivers(self, node: Node) -> frozenset[Node]:
        """The classes whose instances reach a method by its name: its own and every class that
        inherits from it, and none for any other symbol."""
        path, qualified = node
        module = self.modules.get(path)
        item = module.symbols.get(qualified) if module else None
        if item is None or item.kind not in METHOD_KINDS:
            return frozenset()
        owner = (path, qualified.rpartition(".")[0])
        if owner not in self._heirs:
            self._heirs[owner] = _closure(owner, self._derived)
        return self._heirs[owner]

    def reaching(self, node: Node) -> frozenset[Node]:
        """Every symbol from which `node` is reached, itself included."""
        if node not in self._reaching:
            self._reaching[node] = _closure(node, self._users)
        return self._reaching[node]

    def callers(self, node: Node) -> frozenset[Node]:
        """Every symbol from which `node` is reached through calls, without loading a module.

        Loading `dii/__init__.py` makes its `_store`, which hands no one an instance: code
        holds one only through a call that makes or returns it, or by reading the variable.
        """
        if self._calling is None:
            self._calling = {
                target: {user for user in found if user[1] != "<module>"}
                for target, found in self._users.items()
            }
        if node not in self._callers:
            self._callers[node] = _closure(node, self._calling)
        return self._callers[node]


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
    roots = source_roots(repository_files)
    located = _locator(product, roots)
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
                module = module_references(source, package_of(path, roots))
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
    return CallGraph(modules, incomplete=incomplete, warnings=warnings, roots=roots)
