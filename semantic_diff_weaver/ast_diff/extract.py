"""Static Python AST extraction and behavior-bearing feature collection."""

from __future__ import annotations

import ast
import copy
import hashlib
from collections import Counter, defaultdict
from collections.abc import Iterator

from ..path_policy import redact_text
from . import limits
from .limits import AstBudget
from .types import CategoryProbe, ModuleReferences, SymbolReferences, SymbolSnapshot


class AstResourceLimit(ValueError):
    """Raised when untrusted source exceeds an immutable structural safety budget."""


def unparse_redacted(node: ast.AST | None, limit: int = 500) -> str:
    if node is None:
        return "None"
    try:
        return redact_text(ast.unparse(node), max_chars=limit)
    except (AttributeError, RecursionError, ValueError):
        return type(node).__name__


# A boundary is what has a just-below, an exact, and a just-above case to test: an ordering, or an
# equality with a number or a length, such as `count == 0` or `index == len(items)`. An equality
# between other values picks a case and orders nothing — `method == 'GET'`, `kind == token.STAR` —
# and membership and identity tests (`in`, `is`) never were boundaries. A shift (`<<`, `>>`) is
# arithmetic that only looked like a comparison in text.
ORDERING_COMPARISONS = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)
EQUALITY_COMPARISONS = (ast.Eq, ast.NotEq)


def _is_number(node: ast.expr) -> bool:
    """Whether `node` is a number written in the code, signed or not, or a call to `len`."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        node = node.operand
    if isinstance(node, ast.Constant):
        # `type`, not `isinstance`: `flag == True` compares a bool, which is an int subclass.
        return type(node.value) in (int, float, complex)
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "len"


def compares_a_boundary(node: ast.Compare) -> bool:
    """Whether any operator of a comparison, chained or not, tests a boundary."""
    operands = (node.left, *node.comparators)
    for index, operator in enumerate(node.ops):
        if isinstance(operator, ORDERING_COMPARISONS):
            return True
        if isinstance(operator, EQUALITY_COMPARISONS) and (
            _is_number(operands[index]) or _is_number(operands[index + 1])
        ):
            return True
    return False


def _probe_word(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.arg):
        return node.arg
    if isinstance(node, ast.keyword):
        return node.arg
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if not any(character.isspace() for character in node.value):
            return node.value
    return None


def category_probe(*nodes: ast.AST | None) -> CategoryProbe:
    """What a category rule may read from `nodes`: their words and whether they compare.

    `unparse_redacted` keeps string literals verbatim, which is right for display and wrong
    for classification: the message 'Retry with a narrower diff' made an error constructor
    read as a retry change. The words keep identifiers, attribute and keyword names, and string
    literals without whitespace — keys, headers, and permission names such as "Authorization"
    or "retry_limit" are real signal — and drop literals that contain whitespace, whose words
    are prose about the code rather than the code. Each f-string part is judged on its own.
    Whether the value compares a boundary is read from its operators and their operands, never
    from characters in its text.
    """
    words: list[str] = []
    compares = False
    for node in nodes:
        if node is None:
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Compare) and compares_a_boundary(child):
                compares = True
            elif (word := _probe_word(child)) is not None:
                words.append(word)
    text = redact_text(" ".join(words), max_chars=limits.MAX_PROBE_CHARS_PER_FEATURE)
    return CategoryProbe(words=text, compares=compares)


def _assigned_variables(*targets: ast.expr) -> frozenset[str]:
    """The variables assignment targets write to: each name they bind or unpack into, and the
    variable whose item or attribute they set, `HANDLERS` for `HANDLERS["run"] = ...`."""
    names: set[str] = set()
    pending = list(targets)
    while pending:
        target = pending.pop()
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            pending.extend(target.elts)
        elif isinstance(target, (ast.Starred, ast.Attribute, ast.Subscript)):
            pending.append(target.value)
    return frozenset(names)


def parse_module(source: str) -> ast.Module:
    """Parse committed source, preferring type comments but never failing because of them.

    ``type_comments=True`` accepts a strictly narrower grammar than the default: a stray
    module-level ``# type:`` comment is a ``SyntaxError`` under the flag and valid Python
    without it. Type comments only enrich ``function_signature``, so the flag has to degrade
    rather than decide parseability — reporting a valid file as unparsed would emit a
    fabricated ``parse_incomplete`` finding about source that Python itself accepts.
    """
    try:
        return ast.parse(source, type_comments=True)
    except SyntaxError:
        return ast.parse(source)


def _validate_ast_budget(tree: ast.AST, budget: AstBudget) -> None:
    pending = [(tree, 1)]
    nodes = 0
    max_nodes = budget.max_nodes_per_file
    max_depth = budget.max_depth
    while pending:
        node, depth = pending.pop()
        nodes += 1
        if nodes > max_nodes:
            raise AstResourceLimit("AST node budget exceeded")
        if depth > max_depth:
            raise AstResourceLimit("AST depth budget exceeded")
        pending.extend((child, depth + 1) for child in ast.iter_child_nodes(node))


def call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return "<dynamic>"


def decorator_name(node: ast.AST) -> str:
    """Return only a bounded decorator name, never its untrusted arguments."""
    target = node.func if isinstance(node, ast.Call) else node
    return redact_text(call_name(target), max_chars=120)


TYPE_PARAMS_FIELD = "type_params"


def type_parameters(node: ast.AST) -> str:
    """Return a symbol's PEP 695 type parameters, which only exist on Python 3.12 and later.

    Read dynamically rather than as an attribute so this module keeps parsing on 3.11, where
    the field does not exist and the syntax cannot be written in the first place.
    """
    params = getattr(node, TYPE_PARAMS_FIELD, ())
    if not params:
        return ""
    return f"[{', '.join(unparse_redacted(item, 120) for item in params)}]"


def function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Normalize the complete callable signature, including its return annotation."""
    signature = f"{type_parameters(node)}({unparse_redacted(node.args, 900)})"
    if node.returns is not None:
        signature += f" -> {unparse_redacted(node.returns, 300)}"
    if node.type_comment:
        signature += f" # type: {redact_text(node.type_comment, max_chars=300)}"
    return signature


def _bare(argument: ast.arg) -> ast.arg:
    return ast.arg(arg=argument.arg)


def function_parameters(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """The parameters as a caller passes them: names, kinds, order, and defaults.

    Annotations, the return annotation, type parameters, and type comments are left out, so two
    signatures that differ only in their typing have the same parameters.
    """
    arguments = node.args
    bare = ast.arguments(
        posonlyargs=[_bare(item) for item in arguments.posonlyargs],
        args=[_bare(item) for item in arguments.args],
        vararg=_bare(arguments.vararg) if arguments.vararg else None,
        kwonlyargs=[_bare(item) for item in arguments.kwonlyargs],
        kw_defaults=arguments.kw_defaults,
        kwarg=_bare(arguments.kwarg) if arguments.kwarg else None,
        defaults=arguments.defaults,
    )
    return f"({unparse_redacted(bare, 900)})"


def body_without_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def body_fingerprint(body: list[ast.stmt]) -> str:
    material = body_without_docstring(body)
    dump = ast.dump(ast.Module(body=material, type_ignores=[]), include_attributes=False)
    return hashlib.sha256(dump.encode("utf-8")).hexdigest()


def node_inventory(body: list[ast.stmt]) -> tuple[tuple[str, int], ...]:
    module = ast.Module(body=body_without_docstring(body), type_ignores=[])
    counts = Counter(type(node).__name__ for node in ast.walk(module))
    return tuple(sorted(counts.items()))


# The endings of an exception class's name, which every builtin exception and warning has: a
# `raise` that calls anything else, `os.system(...)` or `subprocess.Popen(...)`, runs it first.
EXCEPTION_NAME_ENDINGS = ("Error", "Exception", "Warning", "Exit", "Interrupt", "Iteration")


def message_literals(node: ast.Raise) -> list[ast.Constant]:
    """The prose of the message a `raise` builds: each string literal with whitespace that the
    exception's constructor receives, alone, in an f-string, joined by `+`, or as the format of
    `%` or `.format()`.

    Only a constructor named as an exception class counts. A literal it passes to any other call,
    as in `ValueError(eval("..."))`, is code rather than a message, and one without whitespace is
    a key or a code, as `KeyError("user_id")` holds, as `category_probe` reads prose.
    """
    exception = node.exc
    if not isinstance(exception, ast.Call):
        return []
    callee = exception.func
    name = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", "")
    if not name.endswith(EXCEPTION_NAME_ENDINGS):
        return []
    literals: list[ast.Constant] = []
    pending = [*exception.args, *(keyword.value for keyword in exception.keywords)]
    while pending:
        part = pending.pop()
        if isinstance(part, ast.Constant):
            if isinstance(part.value, str) and any(item.isspace() for item in part.value):
                literals.append(part)
        elif isinstance(part, ast.JoinedStr):
            pending.extend(item for item in part.values if isinstance(item, ast.Constant))
        elif isinstance(part, ast.BinOp) and isinstance(part.op, ast.Add):
            pending.extend((part.left, part.right))
        elif isinstance(part, ast.BinOp) and isinstance(part.op, ast.Mod):
            pending.append(part.left)
        elif (
            isinstance(part, ast.Call)
            and isinstance(part.func, ast.Attribute)
            and part.func.attr == "format"
            and isinstance(part.func.value, ast.Constant)
        ):
            pending.append(part.func.value)
    return literals


def fingerprint_without_messages(body: list[ast.stmt], fingerprint: str) -> str:
    """The body's fingerprint, `fingerprint`, with the prose of the messages it raises blanked.

    Two bodies with the same fingerprint here raise the same exceptions from the same places with
    the same values in their messages, and differ at most in how those messages are worded (see
    `message_literals`).
    """
    literals = [
        literal
        for statement in body_without_docstring(body)
        for node in ast.walk(statement)
        if isinstance(node, ast.Raise)
        for literal in message_literals(node)
    ]
    if not literals:
        return fingerprint
    # Blanked in place for one dump and restored: the statements are shared with every symbol
    # whose body holds them.
    wording = [literal.value for literal in literals]
    try:
        for literal in literals:
            literal.value = ""
        return body_fingerprint(body)
    finally:
        for literal, text in zip(literals, wording, strict=True):
            literal.value = text


TYPING_MODULES = frozenset({"typing", "typing_extensions"})


def type_checking_spellings(tree: ast.AST) -> frozenset[str]:
    """How a file writes `typing.TYPE_CHECKING`, false whenever its code runs.

    `TYPE_CHECKING` counts when every binding of it in the file imports it from `typing` or
    `typing_extensions`, star-imports one of them, or assigns it `False`, the spelling that spares
    importing `typing`; `t.TYPE_CHECKING` counts when every binding of `t` imports one of those
    modules. None counts in a file that sets an attribute named `TYPE_CHECKING`, names it in a
    string, as `globals()` or `setattr` would, or star-imports another module, any of which could
    make it true: code is set aside as a type checker's only when nothing in its file can run it.
    """
    imported: dict[str, set[str]] = defaultdict(set)
    rebound: set[str] = set()
    assigned_false: set[int] = set()
    starred = False
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.Assign, ast.AnnAssign))
            and isinstance(node.value, ast.Constant)
            and node.value.value is False
        ):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            assigned_false.update(
                id(target)
                for target in targets
                if isinstance(target, ast.Name) and target.id == "TYPE_CHECKING"
            )
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and any(item.name == "*" for item in node.names):
                if node.module not in TYPING_MODULES or node.level:
                    return frozenset()
                starred = True
            for name, target in _bound_by(node):
                imported[name].add(target)
        elif isinstance(node, ast.Attribute):
            if node.attr == "TYPE_CHECKING" and not isinstance(node.ctx, ast.Load):
                return frozenset()
        elif isinstance(node, ast.Constant):
            if node.value == "TYPE_CHECKING":
                return frozenset()
        elif isinstance(node, ast.Name):
            if not isinstance(node.ctx, ast.Load) and id(node) not in assigned_false:
                rebound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            rebound.add(node.name)
        elif isinstance(node, ast.arg):
            rebound.add(node.arg)
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            rebound.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            rebound.add(node.rest)
    spellings = {
        f"{name}.TYPE_CHECKING"
        for name, targets in imported.items()
        if name not in rebound and targets <= TYPING_MODULES
    }
    bare = imported.get("TYPE_CHECKING", set())
    if (
        "TYPE_CHECKING" not in rebound
        and bare <= {f"{module}.TYPE_CHECKING" for module in TYPING_MODULES}
        and (bare or starred or assigned_false)
    ):
        spellings.add("TYPE_CHECKING")
    return frozenset(spellings)


def _type_checking(test: ast.expr, spellings: frozenset[str]) -> bool | None:
    """True for a test of `TYPE_CHECKING` as the file spells it, False for `not TYPE_CHECKING`,
    and None for any other test (see `type_checking_spellings`)."""
    negated = False
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        negated, test = True, test.operand
    if isinstance(test, ast.Name):
        spelled = test.id
    elif isinstance(test, ast.Attribute) and isinstance(test.value, ast.Name):
        spelled = f"{test.value.id}.{test.attr}"
    else:
        return None
    return not negated if spelled in spellings else None


def runtime_statements(body: list[ast.stmt], spellings: frozenset[str]) -> list[ast.stmt]:
    """The statements of a body that run, without what only a type checker reads.

    An `if TYPE_CHECKING:` block runs its `else` alone, and an `if not TYPE_CHECKING:` block its
    body alone, where `spellings` says how the file writes `TYPE_CHECKING`. The rest holds imports
    and aliases for annotations, so editing it changes nothing that runs; an import that moves out
    of it and starts to run is still a change.
    """
    statements: list[ast.stmt] = []
    for statement in body:
        if (
            isinstance(statement, ast.If)
            and (checking := _type_checking(statement.test, spellings)) is not None
        ):
            kept = statement.orelse if checking else statement.body
            statements.extend(runtime_statements(kept, spellings))
        else:
            statements.append(statement)
    return statements


def _bound_by(node: ast.Import | ast.ImportFrom) -> list[tuple[str, str]]:
    """Each name an import binds, with what it imports as written, a relative import's dots
    included: `import a.b` binds `a` and imports `a.b`, which it loads. A star import binds no
    name that could be followed, and a `__future__` import none at all."""
    if isinstance(node, ast.Import):
        return [(alias.asname or alias.name.split(".", 1)[0], alias.name) for alias in node.names]
    if node.module == "__future__":
        return []
    base = "." * node.level + (f"{node.module}." if node.module else "")
    return [
        (alias.asname or alias.name, f"{base}{alias.name}")
        for alias in node.names
        if alias.name != "*"
    ]


def top_level_imports(body: list[ast.stmt]) -> tuple[dict[str, tuple[str, ...]], list[ast.stmt]]:
    """What a module body's own imports bind, each name to what it imports, and its other
    statements. A star or `__future__` import is one of those: what the one binds cannot be
    followed, and the other changes how the module compiles."""
    bindings: dict[str, list[str]] = defaultdict(list)
    others: list[ast.stmt] = []
    for statement in body:
        if isinstance(statement, ast.Import) or (
            isinstance(statement, ast.ImportFrom)
            and statement.module != "__future__"
            and all(alias.name != "*" for alias in statement.names)
        ):
            for name, target in _bound_by(statement):
                bindings[name].append(target)
        else:
            others.append(statement)
    return {name: tuple(targets) for name, targets in bindings.items()}, others


# Scopes whose own names a function binds: what it reads of any other is a global or a builtin.
_FUNCTION_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def _own_names(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> frozenset[str]:
    """The names a function binds in its own scope: its parameters, and what its body assigns,
    imports, defines, catches, or captures, unless it declares them `global` or `nonlocal`. The
    bodies of the functions and classes it defines are scopes of their own."""
    arguments = node.args
    bound = {
        item.arg
        for item in (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
            arguments.vararg,
            arguments.kwarg,
        )
        if item is not None
    }
    declared: set[str] = set()
    pending: list[ast.AST] = list(node.body) if isinstance(node.body, list) else [node.body]
    while pending:
        child = pending.pop()
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(child.name)
            continue
        if isinstance(child, ast.Lambda):
            continue
        if isinstance(child, ast.Name) and not isinstance(child.ctx, ast.Load):
            bound.add(child.id)
        elif isinstance(child, (ast.Import, ast.ImportFrom)):
            bound.update(name for name, _ in _bound_by(child))
        elif isinstance(child, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and child.name:
            bound.add(child.name)
        elif isinstance(child, ast.MatchMapping) and child.rest:
            bound.add(child.rest)
        elif isinstance(child, (ast.Global, ast.Nonlocal)):
            declared.update(child.names)
        pending.extend(ast.iter_child_nodes(child))
    return frozenset(bound - declared)


def _names_read(tree: ast.AST) -> set[str]:
    """Every name some code of a file reads without binding it in its own function.

    Module and class code reads each name it uses as a global or a builtin. A function reads its
    own names, and any other as a global, a builtin, or an enclosing function's, which counts
    here too; its decorators, defaults, and annotations are read where it is defined.
    """
    read: set[str] = set()
    pending: list[tuple[ast.AST, frozenset[str]]] = [(tree, frozenset())]
    while pending:
        node, own = pending.pop()
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load) and node.id not in own:
                read.add(node.id)
        elif isinstance(node, _FUNCTION_SCOPES):
            body = node.body if isinstance(node.body, list) else [node.body]
            inner = _own_names(node)
            pending.extend((child, inner) for child in body)
            in_body = {id(item) for item in body}
            pending.extend(
                (child, own) for child in ast.iter_child_nodes(node) if id(child) not in in_body
            )
        else:
            pending.extend((child, own) for child in ast.iter_child_nodes(node))
    return read


def _listed_in_all(node: ast.AST) -> list[str]:
    """The names a statement or call puts in `__all__`: `__all__ = [...]`, `__all__ += [...]`,
    and `__all__.extend([...])` or `.append(...)`."""
    listed: ast.AST | None = None
    if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(item, ast.Name) and item.id == "__all__" for item in targets):
            listed = node.value
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "__all__"
    ):
        listed = node
    if listed is None:
        return []
    return [
        item.value
        for item in ast.walk(listed)
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    ]


def file_names(
    tree: ast.AST,
) -> tuple[frozenset[str], frozenset[str], dict[str, frozenset[str]]]:
    """The names a file mentions, the names it reads, and what its imports bind.

    It mentions each name its code holds, in any role and any block, annotations and
    `TYPE_CHECKING` blocks among them, and reads each one some code reads without binding it in
    its own function (see `_names_read`). Each name its `__all__` lists, which a star import of the
    module reads, counts as both. Each name an import binds, at the top level or not, maps to what
    the imports binding it import.
    """
    mentioned: set[str] = set()
    exported: set[str] = set()
    imported: dict[str, set[str]] = defaultdict(set)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            mentioned.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for name, target in _bound_by(node):
                imported[name].add(target)
        else:
            exported.update(_listed_in_all(node))
    return (
        frozenset(mentioned | exported),
        frozenset(_names_read(tree) | exported),
        {name: frozenset(targets) for name, targets in imported.items()},
    )


class FeatureVisitor(ast.NodeVisitor):
    """Collect bounded behavior-bearing syntax while avoiding nested symbol bodies."""

    def __init__(self, root: ast.AST) -> None:
        self.root = root
        self.comparisons: list[tuple[str, int]] = []
        self.conditions: list[tuple[str, int]] = []
        self.raises: list[tuple[str, int]] = []
        self.handlers: list[tuple[str, int]] = []
        self.calls: list[tuple[str, int]] = []
        self.returns: list[tuple[str, int]] = []
        self.assignments: list[tuple[str, int]] = []
        self.loops: list[tuple[str, int]] = []
        self.contexts: list[tuple[str, int]] = []
        self.statement_order: list[str] = []
        self.probes: dict[str, list[CategoryProbe]] = {name: [] for name in limits.PROBED_FEATURES}
        self.assigned: list[frozenset[str]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node is self.root:
            self._visit_root_body(node.body)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if node is self.root:
            self._visit_root_body(node.body)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if node is self.root:
            self._visit_root_body(node.body)

    def _visit_root_body(self, body: list[ast.stmt]) -> None:
        """Visit executable statements without retaining definition metadata values."""
        for statement in body:
            self.visit(statement)

    def visit_Compare(self, node: ast.Compare) -> None:
        operators = ",".join(type(item).__name__ for item in node.ops)
        self.comparisons.append((f"{unparse_redacted(node)} [{operators}]", node.lineno))
        self.probes["comparisons"].append(category_probe(node))
        self.generic_visit(node)

    def _record_condition(self, test: ast.expr, lineno: int) -> None:
        self.conditions.append((unparse_redacted(test), lineno))
        self.probes["conditions"].append(category_probe(test))

    def visit_If(self, node: ast.If) -> None:
        self._record_condition(node.test, node.lineno)
        self.statement_order.append(f"if:{unparse_redacted(node.test, 120)}")
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self._record_condition(node.test, node.lineno)
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        self._record_condition(node.test, node.lineno)
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        self.raises.append((unparse_redacted(node.exc), node.lineno))
        self.statement_order.append(f"raise:{unparse_redacted(node.exc, 120)}")
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self.handlers.append((unparse_redacted(node.type), node.lineno))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = call_name(node.func)
        positional = [unparse_redacted(item, 100) for item in node.args[:5]]
        keywords = [
            f"{item.arg or '**'}={unparse_redacted(item.value, 100)}" for item in node.keywords[:5]
        ]
        arguments = ", ".join([*positional, *keywords])
        self.calls.append((f"{name}({arguments})", node.lineno))
        self.probes["calls"].append(category_probe(node.func, *node.args[:5], *node.keywords[:5]))
        self.statement_order.append(f"call:{name}")
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        self.returns.append((unparse_redacted(node.value), node.lineno))
        self.statement_order.append(f"return:{unparse_redacted(node.value, 120)}")
        self.generic_visit(node)

    def _record_assignment(self, text: str, lineno: int, *targets: ast.expr) -> None:
        # The probe reads the targets alone: what a value is written to tells state from
        # configuration or data, while a value such as `get_state()` or `RandomState(0)`
        # names state without moving any.
        self.assignments.append((text, lineno))
        self.probes["assignments"].append(category_probe(*targets))
        self.assigned.append(_assigned_variables(*targets))

    def visit_Assign(self, node: ast.Assign) -> None:
        targets = ", ".join(unparse_redacted(item, 100) for item in node.targets)
        self._record_assignment(
            f"{targets} = {unparse_redacted(node.value, 250)}", node.lineno, *node.targets
        )
        self.statement_order.append(f"assign:{targets}")
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        value = f"{unparse_redacted(node.target, 100)} = {unparse_redacted(node.value, 250)}"
        self._record_assignment(value, node.lineno, node.target)
        self.statement_order.append(f"assign:{unparse_redacted(node.target, 100)}")
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        value = (
            f"{unparse_redacted(node.target, 100)} {type(node.op).__name__}= "
            f"{unparse_redacted(node.value, 250)}"
        )
        self._record_assignment(value, node.lineno, node.target)
        self.statement_order.append(f"assign:{unparse_redacted(node.target, 100)}")
        self.generic_visit(node)

    def _record_loop(self, label: str, lineno: int, *nodes: ast.expr) -> None:
        # The probe reads what the loop iterates over or stops on, which names a retry when the
        # loop is one: `for attempt in range(retries)`, `while time.monotonic() < deadline`.
        self.loops.append((label, lineno))
        self.probes["loops"].append(category_probe(*nodes))

    def visit_For(self, node: ast.For) -> None:
        self._record_loop(
            f"for {unparse_redacted(node.target, 100)} in {unparse_redacted(node.iter, 250)}",
            node.lineno,
            node.target,
            node.iter,
        )
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._record_loop(
            f"async for {unparse_redacted(node.target, 100)} in {unparse_redacted(node.iter, 250)}",
            node.lineno,
            node.target,
            node.iter,
        )
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self._record_loop(f"while {unparse_redacted(node.test, 250)}", node.lineno, node.test)
        self._record_condition(node.test, node.lineno)
        self.generic_visit(node)

    def _record_context(self, names: str, lineno: int) -> None:
        self.contexts.append((names, lineno))

    def visit_With(self, node: ast.With) -> None:
        names = ", ".join(unparse_redacted(item.context_expr, 150) for item in node.items)
        self._record_context(names, node.lineno)
        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        names = ", ".join(unparse_redacted(item.context_expr, 150) for item in node.items)
        self._record_context(names, node.lineno)
        self.generic_visit(node)


def default_map(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
    positional = [*node.args.posonlyargs, *node.args.args]
    result: dict[str, str] = {}
    if node.args.defaults:
        for argument, default in zip(
            positional[-len(node.args.defaults) :], node.args.defaults, strict=True
        ):
            result[argument.arg] = unparse_redacted(default, 200)
    for argument, kw_default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True):
        if kw_default is not None:
            result[argument.arg] = unparse_redacted(kw_default, 200)
    return result


def class_display(node: ast.ClassDef, decorators: tuple[str, ...]) -> str:
    """An added or removed class as source would introduce it: decorators, bases, and fields."""
    header = "".join(f"@{name} " for name in decorators) + f"class {node.name}"
    header += type_parameters(node)
    bases = [unparse_redacted(item, 120) for item in (*node.bases, *node.keywords)]
    if bases:
        header += f"({', '.join(bases)})"
    fields: list[str] = []
    for item in body_without_docstring(node.body):
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            fields.append(f"{item.target.id}: {unparse_redacted(item.annotation, 80)}")
        elif isinstance(item, ast.Assign):
            fields.extend(target.id for target in item.targets if isinstance(target, ast.Name))
    if len(fields) > limits.MAX_DISPLAY_CLASS_FIELDS:
        fields = [*fields[: limits.MAX_DISPLAY_CLASS_FIELDS], "…"]
    return f"{header}: {', '.join(fields)}" if fields else header


def module_display(body: list[ast.stmt]) -> str:
    """An added or removed module's top-level code: its first statements, one per line."""
    statements = body_without_docstring(body)
    lines = [
        unparse_redacted(item, 160) for item in statements[: limits.MAX_DISPLAY_MODULE_STATEMENTS]
    ]
    if len(statements) > limits.MAX_DISPLAY_MODULE_STATEMENTS:
        lines.append("…")
    return "\n".join(lines) or "module"


def snapshot_symbol(
    node: ast.AST,
    qualified_name: str,
    kind: str,
    *,
    type_checking: frozenset[str] = frozenset(),
) -> SymbolSnapshot:
    """A symbol as the comparison reads it. A module's or a class's own statements are taken as
    they run: the code of an `if TYPE_CHECKING:` block, spelled as one of `type_checking`, is no
    part of either."""
    if isinstance(node, (ast.Module, ast.ClassDef)):
        running = copy.copy(node)
        running.body = runtime_statements(node.body, type_checking)
        node = running
    body = list(getattr(node, "body", []))
    fingerprint = body_fingerprint(body)
    visitor = FeatureVisitor(node)
    visitor.visit(node)
    parameters: str | None = None
    imports: dict[str, tuple[str, ...]] = {}
    without_imports: str | None = None
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        signature = function_signature(node)
        parameters = function_parameters(node)
        defaults = default_map(node)
        decorators = tuple(decorator_name(item) for item in node.decorator_list)
        display = signature
    elif isinstance(node, ast.ClassDef):
        bases = ", ".join(unparse_redacted(item, 120) for item in node.bases)
        signature = f"{type_parameters(node)}bases({bases})"
        defaults = {}
        decorators = tuple(decorator_name(item) for item in node.decorator_list)
        display = class_display(node, decorators)
    else:
        signature = "module"
        defaults = {}
        decorators = ()
        display = module_display(body)
        imports, others = top_level_imports(body)
        without_imports = body_fingerprint(others)
    return SymbolSnapshot(
        qualified_name=qualified_name,
        kind=kind,
        start=getattr(node, "lineno", 1),
        end=getattr(node, "end_lineno", max(1, len(body))),
        signature=signature,
        default_map=defaults,
        decorators=decorators,
        fingerprint=fingerprint,
        features={name: tuple(getattr(visitor, name)) for name in limits.FEATURE_NAMES},
        statement_order=tuple(visitor.statement_order),
        node_inventory=node_inventory(body),
        probes={name: tuple(values) for name, values in visitor.probes.items()},
        assigned=tuple(visitor.assigned),
        docstring_only=not body_without_docstring(body),
        display=display,
        parameters=parameters,
        fingerprint_without_messages=fingerprint_without_messages(body, fingerprint),
        imports=imports,
        fingerprint_without_imports=without_imports,
    )


def _symbol_nodes(
    body: list[ast.stmt], prefix: str = "", *, parent_is_class: bool = False
) -> Iterator[tuple[str, str, ast.AST]]:
    """Yield each symbol's qualified name, kind, and node, in extraction order.

    A class is yielded as a shell without its nested definitions, which are symbols of their
    own, and the module's remaining top-level statements come last, as `<module>`.
    """
    module_body: list[ast.stmt] = []
    for statement in body:
        if isinstance(statement, ast.ClassDef):
            name = f"{prefix}.{statement.name}" if prefix else statement.name
            class_shell = ast.ClassDef(
                name=statement.name,
                bases=statement.bases,
                keywords=statement.keywords,
                body=[
                    item
                    for item in statement.body
                    if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                ],
                decorator_list=statement.decorator_list,
            )
            ast.copy_location(class_shell, statement)
            class_shell.end_lineno = statement.end_lineno
            # The shell exists only to drop nested definitions from the body fingerprint,
            # so every other behavior-bearing field must survive the rebuild.
            if hasattr(statement, TYPE_PARAMS_FIELD):
                setattr(class_shell, TYPE_PARAMS_FIELD, getattr(statement, TYPE_PARAMS_FIELD))
            yield name, "class", class_shell
            yield from _symbol_nodes(statement.body, name, parent_is_class=True)
        elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = f"{prefix}.{statement.name}" if prefix else statement.name
            if parent_is_class:
                kind = "async_method" if isinstance(statement, ast.AsyncFunctionDef) else "method"
            else:
                kind = (
                    "async_function" if isinstance(statement, ast.AsyncFunctionDef) else "function"
                )
            yield name, kind, statement
            nested: list[ast.stmt] = [
                item
                for item in statement.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            ]
            yield from _symbol_nodes(nested, name)
        else:
            module_body.append(statement)
    if not prefix:
        yield "<module>", "module", ast.Module(body=module_body, type_ignores=[])


def extract_symbols(source: str, budget: AstBudget | None = None) -> list[SymbolSnapshot]:
    effective_budget = budget or AstBudget.default()
    tree = parse_module(source)
    _validate_ast_budget(tree, effective_budget)
    symbols: list[SymbolSnapshot] = []
    type_checking = type_checking_spellings(tree)
    for name, kind, node in _symbol_nodes(tree.body):
        if len(symbols) >= effective_budget.max_symbols_per_file:
            raise AstResourceLimit("symbol budget exceeded")
        snapshot = snapshot_symbol(node, name, kind, type_checking=type_checking)
        if kind == "module":
            snapshot.start = 1
            snapshot.end = max(1, len(source.splitlines()))
            snapshot.names, snapshot.reads, snapshot.imported = file_names(tree)
        symbols.append(snapshot)
    return symbols


def _dotted(node: ast.Attribute) -> str | None:
    """`a.b.c` for an attribute chain rooted at a name, and None for one rooted elsewhere."""
    parts = [node.attr]
    value = node.value
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if not isinstance(value, ast.Name):
        return None
    parts.append(value.id)
    return ".".join(reversed(parts))


def _bases(node: ast.ClassDef) -> tuple[str, ...]:
    """Each base a class names, as a name or an attribute chain: `Generic[T]` names `Generic`."""
    bases: list[str] = []
    for base in node.bases:
        if isinstance(base, ast.Subscript):
            base = base.value
        if isinstance(base, ast.Name):
            bases.append(base.id)
        elif isinstance(base, ast.Attribute) and (chain := _dotted(base)):
            bases.append(chain)
    return tuple(bases)


def _written_names(node: ast.AST) -> frozenset[str]:
    """Every name and attribute chain rooted at a name in an expression, such as an annotation."""
    written: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            written.add(child.id)
        elif isinstance(child, ast.Attribute) and (chain := _dotted(child)):
            written.add(chain)
    return frozenset(written)


def _receiver(node: ast.AST, kind: str) -> str | None:
    """The parameter a method's own instance or class arrives in, `self` or `cls`, and none for
    a static method or anything but a method."""
    if kind not in {"method", "async_method"} or not isinstance(
        node, (ast.FunctionDef, ast.AsyncFunctionDef)
    ):
        return None
    for decorator in node.decorator_list:
        if (isinstance(decorator, ast.Name) and decorator.id == "staticmethod") or (
            isinstance(decorator, ast.Attribute) and decorator.attr == "staticmethod"
        ):
            return None
    parameters = [*node.args.posonlyargs, *node.args.args]
    return parameters[0].arg if parameters else None


def symbol_references(node: ast.AST, kind: str) -> SymbolReferences:
    """What a symbol's code uses — every name and attribute, called or not — and its chains."""
    names: set[str] = set()
    dotted: set[str] = set()
    attributes: set[str] = set()
    own_attributes: set[str] = set()
    bound: set[str] = set()
    declared: set[str] = set()
    receiver = _receiver(node, kind)
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
            if isinstance(child.ctx, ast.Store):
                bound.add(child.id)
        elif isinstance(child, ast.arg):
            bound.add(child.arg)
        elif isinstance(child, ast.ExceptHandler) and child.name:
            bound.add(child.name)
        elif isinstance(child, ast.Global):
            declared.update(child.names)
        elif isinstance(child, ast.Attribute):
            if isinstance(child.value, ast.Name) and child.value.id == receiver:
                own_attributes.add(child.attr)
            else:
                attributes.add(child.attr)
            if chain := _dotted(child):
                dotted.add(chain)
    returns = getattr(node, "returns", None)
    return SymbolReferences(
        names=frozenset(names),
        decorated=bool(getattr(node, "decorator_list", None)),
        kind=kind,
        dotted=frozenset(dotted),
        attributes=frozenset(attributes),
        own_attributes=frozenset(own_attributes),
        bases=_bases(node) if isinstance(node, ast.ClassDef) else (),
        returns=_written_names(returns) if isinstance(returns, ast.expr) else frozenset(),
        bound=frozenset(bound - declared),
    )


def import_bindings(tree: ast.AST, package: str) -> tuple[dict[str, str], frozenset[str]]:
    """What the module's imports bind, each to the absolute dotted name it stands for, and load.

    `import a.b` binds `a` to `a` and loads `a.b`, `import a.b as c` binds `c` to `a.b`, and
    `from a import b` binds `b` to `a.b`, which may be a module or a symbol of `a`. A relative
    import resolves against `package`, and each dot past the first goes up one package. A star
    import binds no name that could be followed.
    """
    bindings: dict[str, str] = {}
    loads: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                loads.add(alias.name)
                if alias.asname:
                    bindings[alias.asname] = alias.name
                else:
                    root = alias.name.split(".", 1)[0]
                    bindings[root] = root
        elif isinstance(node, ast.ImportFrom):
            parts = [node.module] if node.module else []
            if node.level:
                anchor = package.split(".") if package else []
                parts = [*anchor[: max(0, len(anchor) - (node.level - 1))], *parts]
            base = ".".join(parts)
            if base:
                loads.add(base)
            for alias in node.names:
                if alias.name != "*":
                    target = f"{base}.{alias.name}" if base else alias.name
                    bindings[alias.asname or alias.name] = target
                    loads.add(target)
    return bindings, frozenset(loads)


# Scopes of their own, whose assignments bind no module-level variable.
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
# Statements, and the parts of `try` and `match` that hold statements.
_BLOCKS = (ast.stmt, ast.excepthandler, ast.match_case)


def _value_names(node: ast.expr, *, called: bool = True) -> frozenset[str]:
    """What an expression's value is: the class or function a call calls, the name or attribute
    chain it reads, and the items a container, a conditional, or a comprehension yields.

    `JsonDishRepository(path)` is a `JsonDishRepository` whatever `path` is, and
    `dish_repo.load()` is whatever `load` returns, never `dish_repo` itself. Not `called`, the
    names are what the value refers to without calling: a call's arguments rather than what it
    calls, which ran when the module loaded.
    """
    values: set[str] = set()
    pending: list[ast.expr] = [node]
    while pending:
        item = pending.pop()
        if isinstance(item, ast.Name):
            values.add(item.id)
        elif isinstance(item, ast.Attribute):
            if chain := _dotted(item):
                values.add(chain)
        elif isinstance(item, ast.Call):
            if called:
                pending.append(item.func)
            else:
                pending.extend((*item.args, *(keyword.value for keyword in item.keywords)))
        elif isinstance(item, (ast.List, ast.Tuple, ast.Set)):
            pending.extend(item.elts)
        elif isinstance(item, ast.Dict):
            pending.extend(item.values)
        elif isinstance(item, ast.IfExp):
            pending.extend((item.body, item.orelse))
        elif isinstance(item, ast.BoolOp):
            pending.extend(item.values)
        elif isinstance(item, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            pending.append(item.elt)
        elif isinstance(item, ast.DictComp):
            pending.append(item.value)
        elif isinstance(item, (ast.Starred, ast.NamedExpr, ast.Await)):
            pending.append(item.value)
    return frozenset(values)


def _bind(variables: dict[str, set[str]], target: ast.expr, value: ast.expr, called: bool) -> None:
    """Record what an assignment puts in each module-level variable its target binds."""
    if isinstance(target, ast.Name):
        variables[target.id] |= _value_names(value, called=called)
    elif isinstance(target, (ast.Tuple, ast.List)):
        # `a, b = A(), B()` pairs them; any other unpacking may put anything in each name.
        values: list[ast.expr] = [value] * len(target.elts)
        if (
            isinstance(value, (ast.Tuple, ast.List))
            and len(value.elts) == len(target.elts)
            and not any(isinstance(item, ast.Starred) for item in (*target.elts, *value.elts))
        ):
            values = value.elts
        for item, part in zip(target.elts, values, strict=True):
            _bind(variables, item, part, called)
    elif isinstance(target, ast.Starred):
        _bind(variables, target.value, value, called)
    elif isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
        # `HANDLERS["run"] = RunHandler()` puts a handler in the table.
        variables[target.value.id] |= _value_names(value, called=called)


def module_variables(tree: ast.Module, *, called: bool = True) -> dict[str, frozenset[str]]:
    """Each module-level variable, to what its assignments put in it and its annotation names.

    Assignments inside a module-level `if`, `try`, or `with` count, and those inside a function
    or class bind something else. Not `called`, each variable maps to what its value refers to
    without calling, as a table of functions does, and annotations are left out.
    """
    variables: dict[str, set[str]] = defaultdict(set)
    pending: list[ast.AST] = list(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, ast.Assign):
            for target in node.targets:
                _bind(variables, target, node.value, called)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                if called:
                    variables[node.target.id] |= _written_names(node.annotation)
                if node.value is not None:
                    variables[node.target.id] |= _value_names(node.value, called=called)
        elif not isinstance(node, _SCOPES):
            pending.extend(
                child for child in ast.iter_child_nodes(node) if isinstance(child, _BLOCKS)
            )
    return {name: frozenset(written) for name, written in variables.items()}


def module_references(
    source: str, package: str, budget: AstBudget | None = None
) -> ModuleReferences:
    """What each symbol of a module uses and what its imports bind, for test reachability.

    Bounded like extraction, and lighter: only names, attribute chains, class bases, imports,
    and module-level assignments are read.
    """
    effective_budget = budget or AstBudget.default()
    tree = parse_module(source)
    _validate_ast_budget(tree, effective_budget)
    symbols: dict[str, SymbolReferences] = {}
    for name, kind, node in _symbol_nodes(tree.body):
        if len(symbols) >= effective_budget.max_symbols_per_file:
            raise AstResourceLimit("symbol budget exceeded")
        symbols[name] = symbol_references(node, kind)
    bindings, loads = import_bindings(tree, package)
    return ModuleReferences(
        symbols=symbols,
        imports=bindings,
        loads=loads,
        variables=module_variables(tree),
        tables=module_variables(tree, called=False),
    )
