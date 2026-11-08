"""Which test files a committed change can affect, or that the whole suite has to run.

Importing a module runs it, and the packages that hold it, so a test file can be affected by a
changed file it imports, directly or through the repository's other modules; pytest also runs
each `conftest.py` above a test file, and the plugins those name. The head revision is read as
untrusted source, parsed under the bounds below and never imported or executed, from each test
file and `conftest.py` through the modules they import. A test file is one that defines a test
and that pytest and unittest collect tests from by default, by its name and its directories.

Where the analysis cannot see, the selection gives up narrowness rather than a test:

- A file that loads modules by a name it does not write out or from a file path, or starts
  another Python, may reach any module, so a test file reaching one is selected whenever Python
  changed. So is one reaching a file the analysis never reads by policy.
- A changed file that is not Python may be read by any test, so the whole suite runs, unless
  `selection.ignore` says no test reads it. The whole suite runs too for a changed file never
  read by policy or one that sets up how the repository builds or starts Python, for a head
  revision whose Python files cannot all be read within the bounds, and for a test file path that
  a shell or pytest would read as more than a path.

Nothing here sees a test that reaches the code through an installed command, a pytest plugin
named only in pytest's configuration, or a process it starts other than Python, or one that
reads a changed Python file as data. Nor does it see a test failed by what another test file sets
up for the whole session, as Django builds the test database from the models every collected
module defines: a model a change adds to one test file can fail the setup of tests in all the rest.
"""

from __future__ import annotations

import ast
import fnmatch
import re
import sys
import time
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .ast_diff.extract import import_bindings
from .call_graph import importable_name, locate_module, module_locator, package_of, source_roots
from .errors import WeaverError
from .git_diff import ChangedFile, GitRepository
from .models import SelectedTest, TestSelection, WeaverConfig
from .path_policy import exclusion_reason, glob_matches, is_test_support_path
from .test_mapper import has_tests, loads_at_run_time

MAX_SELECTION_FILES = 2000
MAX_SELECTION_BYTES = 32 * 1024 * 1024
SELECTION_TIMEOUT_SECONDS = 10.0
# How many changed files a reason for the whole suite names before it counts the rest.
MAX_NAMED_PATHS = 3
# Python files that set up how a repository builds, installs, or starts Python: a change to one
# can change what every test runs on, though no test imports it.
SETUP_FILES = frozenset({"setup.py", "noxfile.py", "sitecustomize.py", "usercustomize.py"})
# A selected path goes to a shell and to pytest: these characters only, and never an option.
SAFE_PATH_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*")
# Symbolic links and submodules, whose blob is no module's source.
LINK_MODES = frozenset({"120000", "160000"})
# Calls that load a module the file does not name: from a file path, or as an installed plugin.
UNNAMED_LOADERS = frozenset(
    {
        "ExtensionFileLoader",
        "SourceFileLoader",
        "SourcelessFileLoader",
        "entry_points",
        "load_source",
        "run_path",
        "spec_from_file_location",
    }
)
# Calls that load the module their first argument names.
NAME_LOADERS = frozenset({"__import__", "find_spec", "importorskip"})
# Builtins that run the code their first argument holds.
CODE_RUNNERS = frozenset({"eval", "exec"})
# Interpreter options that take the next argument as their value.
VALUED_FLAGS = frozenset({"-W", "-X"})
# A program named as Python itself, as `["python3", "-m", "pkg"]` starts it.
INTERPRETER_RE = re.compile(r"python(?:\d+(?:\.\d+)*)?(?:\.exe)?")
# File names pytest and unittest collect tests from by default: `test_*.py` and `*_test.py`,
# and the `test.py` and `tests.py` that unittest's `test*.py` pattern and Django use.
COLLECTED_NAMES = frozenset({"test.py", "tests.py"})
# The directories pytest looks for no test in unless configured to, `norecursedirs`'s default: a
# test file in one runs only when named, as the suite never names it.
UNCOLLECTED_DIRECTORIES = (
    ".*",
    "*.egg",
    "CVS",
    "_darcs",
    "build",
    "dist",
    "node_modules",
    "venv",
    "{arch}",
)

SAME_COMMIT = (
    "The base and head are the same commit, so nothing says which tests a change can affect."
)
WITHHELD = "{count} changed file(s) are never read by policy, and a test may read them."
NOT_PYTHON = "Tests may read changed files that are not Python: {paths}."
SETUP = (
    "Changed {paths} set up how the repository builds or starts Python, which every test runs on."
)
UNLISTED = "The head revision's Python files could not be listed safely."
UNREAD = "The head revision's Python files could not be read safely."
UNREADABLE = "{path} could not be read within the bounds for following imports."
UNPARSED = "{path} could not be parsed to follow its imports."
CAPPED = f"Following imports from the tests reached more than {MAX_SELECTION_FILES} Python files."
SLOW = f"Following imports from the tests took longer than {SELECTION_TIMEOUT_SECONDS:.0f} seconds."
UNSAFE = (
    "{count} test file path(s) to select hold characters a shell or pytest would read as more "
    "than a path."
)


@dataclass
class _Reading:
    """What one file imports or loads, and runs with `-m`, as it is read."""

    names: set[str] = field(default_factory=set)
    programs: set[str] = field(default_factory=set)
    # Whether it may load a module it does not name.
    opaque: bool = False


@dataclass(frozen=True)
class _Module:
    names: frozenset[str]
    programs: frozenset[str]
    opaque: bool
    tests: bool


@dataclass(frozen=True)
class _Graph:
    modules: Mapping[str, _Module]
    # Each read file, to the files it imports, loads, or runs, those its package and pytest run
    # with it included.
    edges: Mapping[str, frozenset[str]]
    # Python files never read by policy, which may import anything.
    withheld: frozenset[str]


def _collected(path: str) -> bool:
    """Whether a test runner collects tests from a file of this name and place by default."""
    pure = PurePosixPath(path)
    if any(
        fnmatch.fnmatchcase(part, pattern)
        for part in pure.parts[:-1]
        for pattern in UNCOLLECTED_DIRECTORIES
    ):
        return False
    name = pure.name
    return name.startswith("test_") or name.endswith("_test.py") or name in COLLECTED_NAMES


def _literal(node: ast.expr | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _callee(function: ast.expr) -> str | None:
    if isinstance(function, ast.Name):
        return function.id
    return function.attr if isinstance(function, ast.Attribute) else None


def _is_interpreter(node: ast.AST) -> bool:
    """Whether `node` is `sys.executable`, the running Python."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "executable"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    )


def _starts_python(node: ast.expr) -> bool:
    name = _literal(node)
    if name is not None:
        return INTERPRETER_RE.fullmatch(PurePosixPath(name.replace("\\", "/")).name) is not None
    return _is_interpreter(node)


def _run_code(code: str | None, reading: _Reading, depth: int) -> None:
    """Read code a file runs as text, once: code it runs in turn may be anything."""
    if code is None or depth:
        reading.opaque = True
        return
    try:
        tree = ast.parse(code)
    except (MemoryError, RecursionError, SyntaxError, TypeError, ValueError):
        reading.opaque = True
        return
    _read(tree, "", reading, depth + 1)


def _launch(elements: list[ast.expr], reading: _Reading, depth: int) -> bool:
    """Read the arguments of a Python a file starts: whether they name what it runs.

    `-m pkg` runs a module, and `-c` with its code written out runs what the code imports; a
    script path, or arguments built at run time, may run anything.
    """
    index = 1
    while index < len(elements):
        flag = _literal(elements[index])
        if flag is None or not flag.startswith("-"):
            return False
        if flag in {"-m", "-c"}:
            value = _literal(elements[index + 1]) if index + 1 < len(elements) else None
            if value is None:
                return False
            if flag == "-m":
                reading.programs.add(value)
            else:
                _run_code(value, reading, depth)
            return True
        index += 2 if flag in VALUED_FLAGS else 1
    return False


def _plugins(tree: ast.Module, reading: _Reading) -> None:
    """The plugins a module's `pytest_plugins` names, which pytest imports with it."""
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            targets, value = statement.targets, statement.value
        elif isinstance(statement, (ast.AnnAssign, ast.AugAssign)) and statement.value is not None:
            targets, value = [statement.target], statement.value
        else:
            continue
        if not any(isinstance(item, ast.Name) and item.id == "pytest_plugins" for item in targets):
            continue
        for item in value.elts if isinstance(value, (ast.List, ast.Tuple)) else [value]:
            name = _literal(item)
            if name is None:
                reading.opaque = True
            else:
                reading.names.add(name)


def _read(tree: ast.Module, package: str, reading: _Reading, depth: int = 0) -> None:
    """Add what a module imports, loads, and runs to `reading`."""
    reading.names |= import_bindings(tree, package)[1]
    nodes = list(ast.walk(tree))
    run = loads_at_run_time(tree, package, nodes)
    reading.names |= run.modules
    reading.programs |= run.programs
    reading.opaque |= run.unnamed
    # A walk meets a list before its items, so a Python started with arguments that say what it
    # runs is known before `sys.executable` inside it is.
    launched: set[int] = set()
    for node in nodes:
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts and _starts_python(node.elts[0]):
            if _launch(node.elts, reading, depth):
                launched.add(id(node.elts[0]))
            else:
                reading.opaque = True
        elif isinstance(node, ast.Call):
            callee = _callee(node.func)
            first = node.args[0] if node.args else None
            if callee in NAME_LOADERS:
                name = _literal(first)
                if name is None:
                    reading.opaque = True
                else:
                    reading.names.add(name)
            elif callee in UNNAMED_LOADERS:
                reading.opaque = True
            elif isinstance(node.func, ast.Name) and callee in CODE_RUNNERS:
                # Only the builtins: `model.eval()` runs no code given as text.
                _run_code(_literal(first), reading, depth)
        elif _is_interpreter(node) and id(node) not in launched:
            reading.opaque = True
    _plugins(tree, reading)


def _named(paths: Iterable[str]) -> str:
    """The first few paths, and how many more there are."""
    ordered = sorted(set(paths))
    named = ", ".join(ordered[:MAX_NAMED_PATHS])
    rest = len(ordered) - MAX_NAMED_PATHS
    return f"{named}, and {rest} more" if rest > 0 else named


def _sort_changes(
    changed: Sequence[ChangedFile], config: WeaverConfig
) -> tuple[set[str], list[str]]:
    """The changed Python files to follow, and why the whole suite runs, if it does."""
    python: set[str] = set()
    withheld = 0
    others: list[str] = []
    setup: list[str] = []
    for item in changed:
        for path in dict.fromkeys(value for value in (item.old_path, item.new_path) if value):
            if any(glob_matches(path, pattern) for pattern in config.selection.ignore):
                continue
            if exclusion_reason(path):
                withheld += 1
            elif not path.endswith(".py"):
                others.append(path)
            elif PurePosixPath(path).name in SETUP_FILES:
                setup.append(path)
            else:
                python.add(path)
    reasons = []
    if withheld:
        reasons.append(WITHHELD.format(count=withheld))
    if others:
        reasons.append(NOT_PYTHON.format(paths=_named(others)))
    if setup:
        reasons.append(SETUP.format(paths=_named(setup)))
    return python, reasons


def _follow(
    repo: GitRepository,
    head_commit: str,
    files: Sequence[str],
    gone: Sequence[str],
    config: WeaverConfig,
) -> _Graph | str:
    """Read the head revision from its test files and each `conftest.py` through what they
    import, or say why it could not be read whole."""
    roots = source_roots(files)
    python = sorted(path for path in files if path.endswith(".py"))
    withheld = frozenset(path for path in python if exclusion_reason(path))
    readable = frozenset(python) - withheld
    # A deleted module is still what code that imports it names, and that code fails now.
    present = frozenset(python) | frozenset(gone)
    located = module_locator(sorted(present), roots)
    # Each module by the last part of its name, for code that imports it by a shorter one, as a
    # test that puts `scripts` on `sys.path` imports `scripts/pr_comment.py` as `pr_comment`.
    tails: dict[str, list[tuple[tuple[str, ...], str]]] = defaultdict(list)
    for path in sorted(present):
        parts = tuple(part for part in importable_name(path, roots).split(".") if part)
        if parts:
            tails[parts[-1]].append((parts, path))

    resolved: dict[str, frozenset[str]] = {}

    def resolve(name: str) -> frozenset[str]:
        if name not in resolved:
            resolved[name] = _resolve(name)
        return resolved[name]

    def _resolve(name: str) -> frozenset[str]:
        found = locate_module(located, name)
        if found is not None:
            return frozenset({found[0]})
        parts = name.split(".")
        if not all(parts) or parts[0] in sys.stdlib_module_names:
            return frozenset()
        for size in range(len(parts), 0, -1):
            wanted = tuple(parts[:size])
            matched = frozenset(
                path for tail, path in tails.get(parts[size - 1], ()) if tail[-size:] == wanted
            )
            if matched:
                return matched
        return frozenset()

    def package_init(path: str) -> str | None:
        """The `__init__.py` of the package a file is in, which importing it runs first; the
        repository root's own is no package its modules are in."""
        pure = PurePosixPath(path)
        directory = pure.parent.parent if pure.name == "__init__.py" else pure.parent
        init = (directory / "__init__.py").as_posix()
        return init if directory != PurePosixPath(".") and init in present else None

    def conftests(path: str) -> set[str]:
        """Each `conftest.py` pytest runs before a test file: in its directory and above."""
        found = set()
        for directory in PurePosixPath(path).parents:
            candidate = (directory / "conftest.py").as_posix()
            if candidate in present:
                found.add(candidate)
        return found

    pending = sorted(
        path
        for path in readable
        if is_test_support_path(path, config.paths.test_roots) or _collected(path)
    )
    if len(pending) > MAX_SELECTION_FILES:
        return CAPPED
    seen = set(pending)
    modules: dict[str, _Module] = {}
    edges: dict[str, frozenset[str]] = {}
    read_bytes = 0
    deadline = time.monotonic() + SELECTION_TIMEOUT_SECONDS
    while pending:
        entries = repo.tree_entries(head_commit, pending)
        sources = repo.read_blob_objects(
            {entry.object_id for entry in entries.values() if entry.mode not in LINK_MODES},
            config.rules.max_file_bytes,
            max_total_bytes=max(0, MAX_SELECTION_BYTES - read_bytes),
        )
        following: set[str] = set()
        for path in pending:
            if time.monotonic() > deadline:
                return SLOW
            entry = entries.get(path)
            source = (
                sources.get(entry.object_id)
                if entry is not None and entry.mode not in LINK_MODES
                else None
            )
            if source is None:
                return UNREADABLE.format(path=path)
            read_bytes += len(source.encode("utf-8"))
            reading = _Reading()
            try:
                tree = ast.parse(source)
                _read(tree, package_of(path, roots), reading)
            except (MemoryError, RecursionError, SyntaxError, TypeError, ValueError):
                return UNPARSED.format(path=path)
            module = _Module(
                names=frozenset(reading.names),
                programs=frozenset(reading.programs),
                opaque=reading.opaque,
                tests=_collected(path) and has_tests(tree),
            )
            modules[path] = module
            targets: set[str] = set()
            for name in module.names:
                targets |= resolve(name)
            for program in module.programs:
                targets |= resolve(program) | resolve(f"{program}.__main__")
            if (init := package_init(path)) is not None:
                targets.add(init)
            if module.tests:
                targets |= conftests(path)
            targets.discard(path)
            edges[path] = frozenset(targets)
            following |= {target for target in targets if target in readable} - seen
        seen |= following
        if len(seen) > MAX_SELECTION_FILES:
            return CAPPED
        pending = sorted(following)
    return _Graph(modules=modules, edges=edges, withheld=withheld)


def _nearest(
    users: Mapping[str, Iterable[str]], sources: Iterable[str]
) -> dict[str, tuple[str, str]]:
    """Each file that reaches one of `sources` through its imports, sources included, with the
    nearest it reaches and the first file on the way there."""
    found = {source: (source, source) for source in sources}
    queue = deque(sorted(found))
    while queue:
        node = queue.popleft()
        source = found[node][0]
        for user in sorted(users.get(node, ())):
            if user not in found:
                found[user] = (source, node)
                queue.append(user)
    return found


def _reasons(
    path: str,
    change: tuple[str, str] | None,
    unknown: tuple[str, str] | None,
    withheld: frozenset[str],
) -> list[str]:
    reasons = []
    if change is not None:
        source, through = change
        if source == path:
            reasons.append("It changed.")
        elif through == source:
            reasons.append(f"It loads {source}, which changed.")
        else:
            reasons.append(f"It reaches {source}, which changed, through {through}.")
    if unknown is not None:
        source, through = unknown
        if source in withheld:
            # A file never read by policy is never named either.
            reasons.append("It loads a file never read by policy, so any change may reach it.")
        elif source == path:
            reasons.append("It loads modules it does not name, so any change may reach it.")
        else:
            reasons.append(
                f"It reaches {source}, which loads modules it does not name, so any change may "
                "reach it."
            )
    return reasons


def select_tests(
    repo: GitRepository,
    base_commit: str,
    head_commit: str,
    changed: Sequence[ChangedFile],
    config: WeaverConfig,
) -> TestSelection:
    """The test files the change from `base_commit` to `head_commit` can affect."""
    if base_commit == head_commit:
        return TestSelection(run_all=True, reasons=[SAME_COMMIT])
    python, reasons = _sort_changes(changed, config)
    if reasons:
        return TestSelection(run_all=True, reasons=reasons)
    if not python:
        return TestSelection(run_all=False)
    try:
        files = repo.list_files(head_commit, strict=True)
    except WeaverError:
        return TestSelection(run_all=True, reasons=[UNLISTED])
    try:
        graph = _follow(repo, head_commit, files, sorted(python - set(files)), config)
    except WeaverError:
        return TestSelection(run_all=True, reasons=[UNREAD])
    if isinstance(graph, str):
        return TestSelection(run_all=True, reasons=[graph])
    users: dict[str, set[str]] = defaultdict(set)
    for path, targets in graph.edges.items():
        for target in targets:
            users[target].add(path)
    changes = _nearest(users, python)
    opaque = {path for path, module in graph.modules.items() if module.opaque}
    unknowns = _nearest(users, opaque | (graph.withheld & set(users)))
    selected = [
        path
        for path, module in sorted(graph.modules.items())
        if module.tests and (path in changes or path in unknowns)
    ]
    unsafe = [path for path in selected if not SAFE_PATH_RE.fullmatch(path)]
    if unsafe:
        return TestSelection(run_all=True, reasons=[UNSAFE.format(count=len(unsafe))])
    return TestSelection(
        run_all=False,
        tests=[
            SelectedTest(
                path=path,
                reasons=_reasons(path, changes.get(path), unknowns.get(path), graph.withheld),
            )
            for path in selected
        ],
    )
