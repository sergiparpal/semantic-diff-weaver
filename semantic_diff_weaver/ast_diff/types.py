"""AST analysis value types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import LineRange


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
    Internal to classification and never reported.
    """

    # Identifiers, attribute and keyword names, and string literals without whitespace.
    words: str
    # Whether the value contains an ordering or equality comparison: <, <=, >, >=, ==, !=.
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
    # What an added or removed symbol's evidence shows. The signature alone said `bases()` for a
    # dataclass and `module` for a whole `__main__.py`; empty means "show the signature".
    display: str = ""

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
    # Every attribute it uses, which alone calls a method on an instance: a local variable
    # named `ordered` is not `LineRange.ordered`.
    attributes: frozenset[str] = frozenset()
    # A class's bases as written, each a name or an attribute chain: its instances inherit
    # their methods.
    bases: tuple[str, ...] = ()
    # The names and attribute chains in a function's return annotation: whoever calls it
    # holds an instance of the class it returns.
    returns: frozenset[str] = frozenset()


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
