"""AST analysis value types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import LineRange

# A condition on the path to an exit of a callable, as a word and a test (see
# `extract.function_exits`), and an exit: its kind, its value as code, and the conditions on the
# path to it, outermost first.
ExitCondition = tuple[str, str]
ExitPath = tuple[ExitCondition, ...]
FunctionExit = tuple[str, str, ExitPath]
# A local variable an exit's value reads, as `callable_exits` records it: its name, each of its
# assignments in the callable's own code, in order, and how many it has; and each statement of
# that code that then changes the object it names in place, and how many. An assignment is how
# it assigns, `value` for a whole expression and `item` for an item of one a `for` iterates or a
# target unpacks, that expression as code, and the conditions on the path to it, as an exit's.
# A change is how, `set` for an item or an attribute assigned, `delete` for one deleted, and
# `call` for a method called for what it does, the target or the call as code, and its path.
LocalAssignment = tuple[str, str, ExitPath]
LocalChange = tuple[str, str, ExitPath]
LocalDefinition = tuple[str, tuple[LocalAssignment, ...], int, tuple[LocalChange, ...], int]
# The words of a condition. A branch the path takes: an `if`'s test for its body (`if`) or for its
# `else` (`else`), what a handler catches (`except`), a `match` case's pattern (`case`), or, for
# more of them than an exit records, how many (`deep`). Or what a loop or a `try` around the exit
# needs, recorded only where no branch leads there: an iteration of a `for` (`for`) or of a
# `while` (`while`), no exception a handler of a `try` catches (`try`), or no earlier case of a
# `match` (`other`).
BRANCH_CONDITIONS = frozenset({"if", "else", "except", "case", "deep"})
ENCLOSING_CONDITIONS = frozenset({"for", "while", "try", "other"})
# What a bare `raise` records among a symbol's raises: no exception, but what its handler caught,
# raised again. Recorded as its missing exception, it read `None`, and an obligation said the code
# raised `None`.
BARE_RAISE = "raise"
# A handler of a callable's own code that undoes or closes something and then raises, as
# `extract.handled_failures` records it:
# - what it catches, as code, and empty for a bare `except`;
# - the calls of the code its `try` guards, which are the steps that may fail;
# - the calls it makes as statements before it raises;
# - whether a failure of each of those is caught and only reported;
# - what it raises, `CAUGHT_AGAIN` for what it caught;
# - a digest of the guarded code and the handler, which tells a changed handler from one the
#   other side has too.
HandledFailure = tuple[str, tuple[str, ...], tuple[str, ...], bool, str, str]
CAUGHT_AGAIN = "again"
# A file operation of a callable's own code, as `extract.file_steps` records it: its kind, the call
# as its name, and its line. The kind is one of `move`, `copy`, `delete`, `temp`, or `write`.
FileStep = tuple[str, str, int]
# A file operation a callable takes, as `analyze.attach_reached_steps` follows it: the kind, the
# call's name, and the function it is reached through, which is empty for the callable's own code.
ReachedStep = tuple[str, str, str]
FILE_STEP_KINDS = frozenset({"move", "copy", "delete", "temp", "write"})
# The kinds that put a file in place of another or beside it: a sequence holding one of them and a
# second step leaves the original or a copy behind when it fails partway.
FILE_PLACING_KINDS = frozenset({"move", "copy"})


def at_risk_midway(steps: tuple[ReachedStep, ...]) -> bool:
    """Whether a failure partway through a callable's file steps can leave the original replaced
    or its copy lost: two steps or more, one of them a move or a copy."""
    return len(steps) >= 2 and any(kind in FILE_PLACING_KINDS for kind, _, _ in steps)


def reparameterize(signature: str) -> str:
    """Normalize identifiers while preserving argument structure for rename matching."""
    result: list[str] = []
    in_identifier = False
    for char in signature:
        if char.isalnum() or char == "_":
            if not in_identifier:
                result.append("_")
                in_identifier = True
        else:
            result.append(char)
            in_identifier = False
    return "".join(result)


@dataclass(frozen=True)
class CategoryProbe:
    """What the category rules may read from a value, taken from its AST rather than its text.

    The display text keeps string literals verbatim and is redacted and cut, so it cannot tell
    code from strings: a message's words read as keywords, a `'<'` inside a literal read as a
    comparison, and redaction could turn `token == expected` into `token =[REDACTED] expected`.
    An assignment's probe reads its targets only. Internal to classification and never reported.
    """

    # Identifiers, attribute and keyword names, and string literals without whitespace. A delta's
    # probe of comparisons or conditions holds only those of the values the delta changed.
    words: str
    # Whether the value compares a boundary: an ordering (<, <=, >, >=), or an equality or
    # inequality with a number or a length on one side. A delta's probe says whether a value the
    # delta changed does, so an unchanged `count > 3` beside a changed `parent is None` does not.
    compares: bool


@dataclass
class SymbolSnapshot:
    qualified_name: str
    kind: str
    start: int
    end: int
    signature: str
    default_map: dict[str, str]
    decorators: tuple[str, ...]
    fingerprint: str
    features: dict[str, tuple[tuple[str, int], ...]]
    statement_order: tuple[str, ...]
    node_inventory: tuple[tuple[str, int], ...]
    match_ambiguous: bool = False
    # Per probed feature, one category probe per recorded value, in the same order.
    probes: dict[str, tuple[CategoryProbe, ...]] = field(default_factory=dict)
    # Per assignment, in the order of `features["assignments"]`, the variables it writes to.
    assigned: tuple[frozenset[str], ...] = ()
    # Per assignment, in the same order, the last name each target writes: the attribute it sets,
    # `upcoming_sleep` for `retry_state.upcoming_sleep = 0.0`, where `assigned` names
    # `retry_state`. A snapshot built without them names none.
    stored: tuple[frozenset[str], ...] = ()
    # Per assignment, in the same order, whether it sets an attribute or an item of `self` or
    # `cls`: what a method keeps on its instance or class for later calls to read.
    on_instance: tuple[bool, ...] = ()
    # Per assignment, in the same order, what the schemas its value holds describe: a digest of
    # the value with their `description` texts blanked, and those texts (see
    # `extract.described_value`). Empty for a value that holds no description.
    descriptions: tuple[tuple[str, tuple[str, ...]], ...] = ()
    # A module's names its `__all__` lists, sorted: what `import *` binds. Empty for any other
    # symbol, or a module whose top-level code lists none.
    exports: tuple[str, ...] = ()
    # Whether its body holds nothing but a docstring, or nothing at all. A module's body is its
    # top-level code without its definitions, which are symbols of their own.
    docstring_only: bool = False
    # Whether it is a method that only declares what the implementations of its class define: a
    # member of a `Protocol`, or an abstract method, whose body holds nothing but a docstring,
    # `...`, `pass`, or a raise of `NotImplementedError` (see `extract.declares_only`).
    declared_only: bool = False
    # For a method, the bases its class names, as `extract._bases` reads them.
    owner_bases: tuple[str, ...] = ()
    # What an added or removed symbol's evidence shows. The signature alone said `bases()` for a
    # dataclass and `module` for a whole `__main__.py`; empty means "show the signature".
    display: str = ""
    # A callable's signature without its typing: what a caller passes. None for a class, a module,
    # or a snapshot built without it, whose signature changes are never read as typing alone.
    parameters: str | None = None
    # A callable's parameters in order, each name and how a caller passes it: `positional_only`,
    # `positional`, `var_positional`, `keyword_only`, or `var_keyword`. None for a class, a module,
    # or a snapshot built without them, whose signature never reads as gaining optional parameters.
    parameter_kinds: tuple[tuple[str, str], ...] | None = None
    # The fingerprint with the prose of the exceptions its body raises blanked: equal to another's
    # while the fingerprints differ, the two differ only in how a message is worded. None for a
    # snapshot built without it, which is never read as reworded.
    fingerprint_without_messages: str | None = None
    # A callable's exits, at most `MAX_FUNCTION_EXITS` of them, as `function_exits` records them:
    # what each `return` and `raise` of its own code hands back or raises, and the conditions on
    # the path leading there. And how many it has. None for a generator or any other symbol.
    exits: tuple[FunctionExit, ...] = ()
    exits_total: int = 0
    # The local variables those exits' values read, at most `MAX_DEFINED_LOCALS`, with their
    # assignments: what a test of the value needs to know a returned name stands for.
    definitions: tuple[LocalDefinition, ...] = ()
    # The fingerprint with the `description` texts of the schemas its body holds blanked: equal to
    # another's while the fingerprints differ, the two differ only in how a schema describes a
    # tool or its properties. None for a snapshot built without it, never read as redescribed.
    fingerprint_without_descriptions: str | None = None
    # A module's own top-level imports as they run: each name one binds, to what it imports as
    # written, `._types` for `from . import _types`.
    imports: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # A module's fingerprint without those imports. None for any other symbol, or a module built
    # without it, whose changes are never read as imports alone.
    fingerprint_without_imports: str | None = None
    # A module's whole file: every name its code mentions, in any role and any block, and every
    # name its `__all__` lists.
    names: frozenset[str] = frozenset()
    # A module's whole file: every name some code reads without binding it in its own function, as
    # a global or a builtin, and every name its `__all__` lists. A local `last_leaf` is not one.
    reads: frozenset[str] = frozenset()
    # A module's whole file: each name an import anywhere in it binds, to what those imports import.
    imported: dict[str, frozenset[str]] = field(default_factory=dict)
    # A module's or a class's fingerprint without what only a type checker reads: annotations that
    # run nothing, and a module's type variables (see `extract.without_typing`). None for any other
    # symbol, or a class whose annotations may make fields, never read as typing alone.
    fingerprint_without_typing: str | None = None
    # A module's type variables that `fingerprint_without_typing` leaves out.
    type_variables: frozenset[str] = frozenset()
    # A module's fingerprint with the text of its `__version__` string blanked, and that text. None
    # for any other symbol, or a module that sets no such string.
    fingerprint_without_version: str | None = None
    version: str | None = None
    # A callable's typing beyond its parameters: type parameters, return annotation, type comment.
    typing_beyond_parameters: str | None = None
    # A callable's annotated parameters: each name, a digest of its annotation, and whether
    # evaluating that annotation runs nothing (see `extract.parameter_typing`).
    parameter_typing: tuple[tuple[str, str, bool], ...] = ()
    # Whether nothing a callable is decorated with or defined in reads its annotations (see
    # `extract.annotations_unread`).
    annotations_unread: bool = False
    # A function's lines whose code feeds the value it returns or yields (see
    # `extract.output_lines`). Empty for any other symbol.
    output_lines: frozenset[int] = frozenset()
    # A module's names bound once at its top level to a constant, each to a digest of its value,
    # and its fingerprint without them (see `extract.module_constants`). None for any other symbol,
    # or a module built without its file's typing context, never read as constants alone.
    constants: dict[str, str] = field(default_factory=dict)
    fingerprint_without_constants: str | None = None
    # A callable's handlers that undo or close something and then raise, at most
    # `MAX_HANDLED_FAILURES` of them (see `extract.handled_failures`). Empty for any other symbol.
    handled_failures: tuple[HandledFailure, ...] = ()
    # A callable's own file operations in order, at most `MAX_FILE_STEPS` (see `extract.file_steps`).
    file_steps: tuple[FileStep, ...] = ()
    # The file operations it takes in order, its own and those of the analyzed files' functions it
    # calls, at most `MAX_REACHED_STEPS`, and how many. `analyze.attach_reached_steps` sets them
    # once every changed file of its revision is parsed. It also sets `step_calls`, the calls of
    # its own code those steps come from, in order: each file operation's name, or the function
    # that takes some. A call whose arguments alone changed leaves them as they were.
    reached_steps: tuple[ReachedStep, ...] = ()
    reached_steps_total: int = 0
    step_calls: tuple[str, ...] = ()

    @property
    def signature_shape(self) -> str:
        return reparameterize(self.signature)


@dataclass(frozen=True)
class SymbolReferences:
    """What one symbol's code uses, for which symbols a test reaches through calls."""

    # Every name it uses, called or not: `type=pr_number` hands a function to argparse, which
    # calls it later.
    names: frozenset[str]
    # A decorator may register the symbol with a framework that calls it, which no name shows.
    decorated: bool
    # As extraction labels it: `class`, `function`, `method`, and so on. A class's methods are
    # what a call on one of its instances reaches.
    kind: str
    # Attribute chains rooted at a name, such as `messages.unexpected_failure`, which the
    # module's imports resolve to a symbol of another module.
    dotted: frozenset[str] = frozenset()
    # Every attribute it reads on something other than its own instance, which alone calls a
    # method on another: a local variable named `ordered` is not `LineRange.ordered`.
    attributes: frozenset[str] = frozenset()
    # The attributes a method reads on its own instance or class, `self.path`, which only its
    # class and the classes it inherits from define.
    own_attributes: frozenset[str] = frozenset()
    # A class's bases as written, each a name or an attribute chain: its instances inherit
    # their methods.
    bases: tuple[str, ...] = ()
    # The names and attribute chains in a function's return annotation: whoever calls it
    # holds an instance of the class it returns.
    returns: frozenset[str] = frozenset()
    # The names its code binds itself — parameters, assignment targets, and caught exceptions —
    # unless it declares them `global`: a local `config` is no module's `config`.
    bound: frozenset[str] = frozenset()
    # Of those names and attributes, the ones its code calls: `f(...)`, `x.f(...)`, and, for a
    # method, `self.f(...)`. A name it only hands on, `register(f)`, runs where whatever it is
    # handed to calls it, which need not be this code.
    called_names: frozenset[str] = frozenset()
    called_attributes: frozenset[str] = frozenset()
    called_own_attributes: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ModuleReferences:
    """A module's symbols and what each name its imports bind stands for."""

    symbols: dict[str, SymbolReferences]
    # Local name to absolute dotted name: `from distiller import messages` binds `messages` to
    # `distiller.messages`, a module, and `from .analyze import run` binds `run` to a symbol.
    imports: dict[str, str]
    # Every absolute name an import loads, bound or not: `import a.b` binds only `a`.
    loads: frozenset[str] = frozenset()
    # Each module-level variable, to the names and attribute chains of what its assignments
    # put in it and of its annotation: `dish_repo: DishRepository = JsonDishRepository(path)`
    # holds a `JsonDishRepository`, whose methods a module importing `dish_repo` calls.
    variables: dict[str, frozenset[str]] = field(default_factory=dict)
    # Each module-level variable, to what its value refers to without calling it: a table of
    # functions, `CLASSIFIERS = (_classify_error, ...)`, that the code reading it calls later.
    # `repo = Repository()` called its class when the module loaded, and refers to nothing.
    tables: dict[str, frozenset[str]] = field(default_factory=dict)


@dataclass
class StructuralDelta:
    path: str
    symbol: str
    kind: str
    old: str | None
    new: str | None
    old_lines: LineRange | None
    new_lines: LineRange | None
    hunk_id: str | None
    parser_complete: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    # What the category rules read; None means "read the `old` and `new` display text instead".
    probe: CategoryProbe | None = None
    # As extraction labels the symbol, `function`, `class`, `module`, and so on: what a test does
    # to reach it. None for a delta of no Python symbol.
    symbol_kind: str | None = None


@dataclass
class AstAnalysis:
    deltas: list[StructuralDelta]
    warnings: list[str]
    parsed_files: int
    failed_files: int
    changed_symbols: int
    resource_limited_files: int = 0


@dataclass(frozen=True)
class SymbolPair:
    old: SymbolSnapshot | None
    new: SymbolSnapshot | None
    ambiguous: bool = False
