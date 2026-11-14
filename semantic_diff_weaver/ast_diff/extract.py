"""Static Python AST extraction and behavior-bearing feature collection."""

from __future__ import annotations

import ast
import builtins
import copy
import hashlib
import sys
import warnings
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field

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


# How many hexadecimal digits of a cut value's digest its text ends with.
VALUE_DIGEST_CHARS = 12


def unparse_whole(node: ast.AST | None, limit: int) -> str:
    """`unparse_redacted`, but a value cut at `limit` ends with a digest of all of it.

    Cut alone, two values that differ only past `limit` read the same, and so did their
    assignments: httpx's `__all__` gained `FunctionAuth` 355 characters in, and no assignment
    changed. The digest is taken of the redacted text, so a credential moves it no more than it
    moves the text shown, and it tells only whether two values are the same.
    """
    if node is None:
        return "None"
    try:
        whole = redact_text(ast.unparse(node), max_chars=sys.maxsize)
    except (AttributeError, RecursionError, ValueError):
        return type(node).__name__
    if len(whole) <= limit:
        return whole
    digest = hashlib.sha256(whole.encode("utf-8", "surrogatepass")).hexdigest()
    return f"{whole[:limit]}…#{digest[:VALUE_DIGEST_CHARS]}"


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


def _stored_names(*targets: ast.expr) -> frozenset[str]:
    """The last name each assignment target writes: the attribute it sets, `upcoming_sleep` for
    `retry_state.upcoming_sleep = ...`, the variable whose item it sets, `HANDLERS` for
    `HANDLERS["run"] = ...`, or the name it binds.

    What an assignment writes to names what it holds. The variable it writes into may name
    something else: `retry_state` is a retry's state, and its `upcoming_sleep` the delay.
    """
    names: set[str] = set()
    pending = list(targets)
    while pending:
        target = pending.pop()
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)
        elif isinstance(target, (ast.Tuple, ast.List)):
            pending.extend(target.elts)
        elif isinstance(target, (ast.Starred, ast.Subscript)):
            pending.append(target.value)
    return frozenset(names)


# The names a method's first parameter has by convention: its instance, or its class.
INSTANCE_NAMES = frozenset({"self", "cls"})


def _writes_on_instance(*targets: ast.expr) -> bool:
    """Whether an assignment target sets an attribute or an item of `self` or `cls`, however
    deep: `self._waiters = ...`, `self._state.waiting[key] = ...`, `cls.registry[name] = ...`."""
    pending = list(targets)
    while pending:
        target = pending.pop()
        if isinstance(target, (ast.Tuple, ast.List)):
            pending.extend(target.elts)
        elif isinstance(target, ast.Starred):
            pending.append(target.value)
        elif isinstance(target, (ast.Attribute, ast.Subscript)):
            root = target.value
            while isinstance(root, (ast.Attribute, ast.Subscript)):
                root = root.value
            if isinstance(root, ast.Name) and root.id in INSTANCE_NAMES:
                return True
    return False


def parse_python(source: str, *, type_comments: bool = False) -> ast.Module:
    """Parse a repository's Python without the warnings its code raises.

    Python warns while it parses some literals: a string with an invalid escape sequence, such
    as a regular expression written without the ``r`` prefix, is a ``SyntaxWarning`` from 3.12
    and a ``DeprecationWarning`` before. Such a warning is about the analyzed code, yet it went
    to this process's stderr, where it read as the tool's own, and where warnings are errors it
    was raised as a ``SyntaxError`` about a file Python runs. Every parse of a repository's code
    comes here.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        warnings.simplefilter("ignore", DeprecationWarning)
        return ast.parse(source, type_comments=type_comments)


def parse_module(source: str) -> ast.Module:
    """Parse committed source, preferring type comments but never failing because of them.

    ``type_comments=True`` accepts a strictly narrower grammar than the default: a stray
    module-level ``# type:`` comment is a ``SyntaxError`` under the flag and valid Python
    without it. Type comments only enrich ``function_signature``, so the flag has to degrade
    rather than decide parseability — reporting a valid file as unparsed would emit a
    fabricated ``parse_incomplete`` finding about source that Python itself accepts.
    """
    try:
        return parse_python(source, type_comments=True)
    except SyntaxError:
        return parse_python(source)


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


# JSON Schema's type names, and the keys under which a schema lists its properties or a tool's
# definition the schema of its arguments: a dict literal holding one is a schema.
SCHEMA_TYPES = frozenset({"array", "boolean", "integer", "null", "number", "object", "string"})
SCHEMA_KEYS = frozenset({"inputSchema", "input_schema", "parameters", "properties"})


def _text(node: ast.expr | None) -> str | None:
    """The string a literal holds, or None for any other expression."""
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def is_schema(node: ast.Dict) -> bool:
    """Whether a dict literal reads as a JSON Schema or a tool's definition: it has a key of
    `SCHEMA_KEYS`, or a `type` that names a JSON Schema type."""
    for key, value in zip(node.keys, node.values, strict=True):
        name = _text(key)
        if name in SCHEMA_KEYS or (name == "type" and _text(value) in SCHEMA_TYPES):
            return True
    return False


def description_literals(statement: ast.AST) -> list[ast.Constant]:
    """The `description` texts of the schemas a statement holds, each a string literal.

    A tool's schema, `{"description": "Empty the fridge…", "type": "object", ...}`, tells an agent
    what the tool does and when to call it, and the descriptions of its properties what to pass.
    A validator reads none of them.
    """
    literals: list[ast.Constant] = []
    for node in ast.walk(statement):
        if not isinstance(node, ast.Dict) or not is_schema(node):
            continue
        for key, value in zip(node.keys, node.values, strict=True):
            if _text(key) == "description" and isinstance(value, ast.Constant) and _text(value):
                literals.append(value)
    return literals


def fingerprint_without_descriptions(body: list[ast.stmt], fingerprint: str) -> str:
    """The body's fingerprint, `fingerprint`, with the `description` texts of its schemas blanked.

    Two bodies with the same fingerprint here hold the same schemas, and differ at most in how
    those describe a tool or its properties (see `description_literals`): text for whoever reads
    the schema, an agent given the tool among them, that no code computes with.
    """
    literals = [
        literal
        for statement in body_without_docstring(body)
        for literal in description_literals(statement)
    ]
    if not literals:
        return fingerprint
    # Blanked in place for one dump and restored, as `fingerprint_without_messages` does.
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


# Beyond `TYPE_CHECKING` blocks, a type checker alone reads annotations whose evaluation runs none
# of the project's code, and the type variables they use. Modules whose names such an annotation
# may read, by import or as a module alias:
SAFE_TYPING_MODULES = frozenset(
    {
        "abc",
        "collections",
        "collections.abc",
        "datetime",
        "decimal",
        "functools",
        "ipaddress",
        "numbers",
        "os",
        "pathlib",
        "re",
        "types",
        "typing",
        "typing_extensions",
        "uuid",
    }
)
# What such an annotation may subscript: the builtin generics, what the typing modules and
# `collections.abc` export, and the other generic classes of the standard library it reads.
SUBSCRIPTABLE_BUILTINS = frozenset({"list", "dict", "set", "frozenset", "tuple", "type"})
SUBSCRIPTABLE_MODULES = frozenset({"typing", "typing_extensions", "collections.abc"})
SUBSCRIPTABLE_TARGETS = frozenset({"os.PathLike", "re.Pattern", "re.Match"})
BUILTIN_TYPES = frozenset(
    name for name in dir(builtins) if isinstance(getattr(builtins, name), type)
)
TYPE_VARIABLE_FACTORIES = frozenset({"TypeVar", "ParamSpec", "TypeVarTuple"})
# Decorators that read no annotation: the builtin kinds of method, and the markers of abstract,
# overloaded, overriding, final, and cached ones.
INERT_DECORATORS = frozenset(
    {
        "builtins.property",
        "builtins.staticmethod",
        "builtins.classmethod",
        "abc.abstractmethod",
        "functools.cached_property",
        *(
            f"{module}.{name}"
            for module in TYPING_MODULES
            for name in ("overload", "override", "final")
        ),
    }
)


@dataclass(frozen=True)
class TypingContext:
    """What a file's annotations and type variables touch when its code runs (see
    `typing_context`)."""

    # Whether `from __future__ import annotations` keeps its annotations from being evaluated.
    deferred: bool = False
    # Each name that evaluates to the standard typing vocabulary, to what it is: a builtin type as
    # `builtins.str`, an imported name as `typing.Union`, and a module alias as the module,
    # `typing` for `import typing as t`.
    names: Mapping[str, str] = field(default_factory=dict)
    # The top-level classes whose annotations make no field and that no hook reads: undecorated,
    # without keywords such as `metaclass=`, bound once, and derived only from `object`, typing's
    # `Generic`, or plain classes of the file that define no `__init_subclass__`.
    plain_classes: frozenset[str] = frozenset()
    # The names the file binds once and reads only in annotations, or nowhere.
    annotation_names: frozenset[str] = frozenset()


def _bindings_and_reads(tree: ast.Module) -> tuple[Counter[str], set[str], set[str]] | None:
    """In one walk of a file: how many times it binds each name, anywhere and in any way; the
    names it reads outside annotations; and the names it sets as attributes or names in strings.
    None when it star-imports a module outside `SAFE_TYPING_MODULES`.

    The walk reaches a node before anything inside it, so a parameter's, a return's, or a
    variable's annotation is known to be one before the walk reaches the names it reads.
    """
    counts: Counter[str] = Counter()
    inside: set[int] = set()
    read: set[str] = set()
    excluded: set[str] = set()
    for node in ast.walk(tree):
        annotation: ast.expr | None = None
        if isinstance(node, ast.Name):
            if not isinstance(node.ctx, ast.Load):
                counts[node.id] += 1
            elif id(node) not in inside:
                read.add(node.id)
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                excluded.add(node.value)
        elif isinstance(node, ast.Attribute):
            if not isinstance(node.ctx, ast.Load):
                excluded.add(node.attr)
        elif isinstance(node, ast.arg):
            counts[node.arg] += 1
            annotation = node.annotation
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            counts[node.name] += 1
            annotation = node.returns
        elif isinstance(node, ast.ClassDef):
            counts[node.name] += 1
        elif isinstance(node, ast.AnnAssign):
            annotation = node.annotation
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            if (
                isinstance(node, ast.ImportFrom)
                and any(alias.name == "*" for alias in node.names)
                and (node.level or node.module not in SAFE_TYPING_MODULES)
            ):
                return None
            for name, _ in _bound_by(node):
                counts[name] += 1
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            counts[node.name] += 1
        elif isinstance(node, ast.MatchMapping) and node.rest:
            counts[node.rest] += 1
        if annotation is not None:
            inside.update(id(item) for item in ast.walk(annotation))
    return counts, read, excluded


def _typing_bindings(statement: ast.Import | ast.ImportFrom) -> list[tuple[str, str | None]]:
    """Each name an import binds, with the safe typing module or name it binds it to, or None:
    `import collections.abc` binds `collections`, `from collections import abc` binds the module
    `collections.abc`, and `from typing import Union` binds `typing.Union`."""
    if isinstance(statement, ast.Import):
        bound = []
        for alias in statement.names:
            module = alias.name if alias.asname else alias.name.split(".", 1)[0]
            name = alias.asname or alias.name.split(".", 1)[0]
            bound.append((name, module if module in SAFE_TYPING_MODULES else None))
        return bound
    if statement.module == "__future__":
        return []
    safe = not statement.level and statement.module in SAFE_TYPING_MODULES
    return [
        (alias.asname or alias.name, f"{statement.module}.{alias.name}" if safe else None)
        for alias in statement.names
        if alias.name != "*"
    ]


def typing_context(tree: ast.Module, type_checking: frozenset[str]) -> TypingContext:
    """What a file's annotations can read without running any of its project's code.

    A name counts when the file binds it only by importing it, in code that runs, from a module of
    `SAFE_TYPING_MODULES`, or binds it nowhere and it is a builtin type. None counts in a file
    that star-imports another module, and no name the file sets as an attribute or names in a
    string, as `setattr` or `globals()` would: either could make reading it run anything.
    """
    deferred = any(
        isinstance(statement, ast.ImportFrom)
        and statement.module == "__future__"
        and any(alias.name == "annotations" for alias in statement.names)
        for statement in tree.body
    )
    walked = _bindings_and_reads(tree)
    if walked is None:
        return TypingContext(deferred=deferred)
    counts, read, excluded = walked
    imported: dict[str, list[str | None]] = defaultdict(list)
    for statement in runtime_statements(tree.body, type_checking):
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            for name, target in _typing_bindings(statement):
                imported[name].append(target)
    names: dict[str, str] = {}
    for name, targets in imported.items():
        resolved = set(targets)
        if (
            len(resolved) == 1
            and None not in resolved
            and counts[name] == len(targets)
            and name not in excluded
        ):
            names[name] = str(resolved.pop())
    for name in BUILTIN_TYPES:
        if not counts[name] and name not in excluded:
            names[name] = f"builtins.{name}"
    context = TypingContext(deferred=deferred, names=names)
    return TypingContext(
        deferred=deferred,
        names=names,
        plain_classes=_plain_classes(tree, counts, context),
        annotation_names=frozenset(
            name for name, count in counts.items() if count == 1 and name not in read
        ),
    )


def _plain_classes(
    tree: ast.Module, counts: Counter[str], context: TypingContext
) -> frozenset[str]:
    """The top-level classes whose annotations make no field and that no hook reads (see
    `TypingContext.plain_classes`): a decorator, a keyword, a base from elsewhere, or a second
    binding, as `Point = dataclass(Point)`, could each turn annotations into fields."""
    plain: set[str] = set()
    hooked: set[str] = set()
    for statement in tree.body:
        if not isinstance(statement, ast.ClassDef):
            continue
        if any(
            isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name == "__init_subclass__"
            for item in statement.body
        ):
            hooked.add(statement.name)
        if (
            not statement.decorator_list
            and not statement.keywords
            and counts[statement.name] == 1
            and all(_plain_base(base, plain - hooked, context) for base in statement.bases)
        ):
            plain.add(statement.name)
    return frozenset(plain)


def _plain_base(base: ast.expr, plain: set[str], context: TypingContext) -> bool:
    if isinstance(base, ast.Name) and base.id in plain:
        return True
    if isinstance(base, ast.Subscript):
        index = base.slice.elts if isinstance(base.slice, ast.Tuple) else [base.slice]
        return (_resolve(base.value, context) or "") in {
            f"{module}.Generic" for module in TYPING_MODULES
        } and all(isinstance(item, ast.Name) for item in index)
    return _resolve(base, context) == "builtins.object"


def _resolve(node: ast.expr, context: TypingContext) -> str | None:
    """What a name or attribute is in the standard typing vocabulary, or None (see
    `TypingContext.names`)."""
    if isinstance(node, ast.Name):
        return context.names.get(node.id)
    if isinstance(node, ast.Attribute):
        base = _resolve(node.value, context)
        if base in SAFE_TYPING_MODULES:
            return f"{base}.{node.attr}"
    return None


def _subscriptable(target: str) -> bool:
    module, _, name = target.rpartition(".")
    return (
        (module == "builtins" and name in SUBSCRIPTABLE_BUILTINS)
        or (module in SUBSCRIPTABLE_MODULES and target not in SAFE_TYPING_MODULES)
        or target in SUBSCRIPTABLE_TARGETS
    )


def runs_nothing(node: ast.expr, context: TypingContext) -> bool:
    """Whether evaluating an annotation can run none of the project's code, and cannot fail: a
    constant; a name or attribute that `context` resolves; a subscript of one that takes a
    subscript, indexed by such annotations; and a `|` union of them and `None`."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.Name, ast.Attribute)):
        return _resolve(node, context) is not None
    if isinstance(node, ast.Subscript):
        target = _resolve(node.value, context)
        return target is not None and _subscriptable(target) and runs_nothing(node.slice, context)
    if isinstance(node, (ast.Tuple, ast.List)):
        return all(runs_nothing(item, context) for item in node.elts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return all(
            side.value is None if isinstance(side, ast.Constant) else runs_nothing(side, context)
            for side in (node.left, node.right)
        )
    return False


def _type_variable(statement: ast.stmt, context: TypingContext) -> str | None:
    """The name a statement defines as a type variable that only annotations read, or None.

    The statement is `NAME = TypeVar("NAME", ...)`, or a `ParamSpec` or `TypeVarTuple`, as one of
    the typing modules defines it, with arguments that run nothing (see `runs_nothing`); and the
    file binds NAME nowhere else and reads it only in annotations, or nowhere.
    """
    if not (
        isinstance(statement, ast.Assign)
        and len(statement.targets) == 1
        and isinstance(statement.targets[0], ast.Name)
        and isinstance(statement.value, ast.Call)
    ):
        return None
    name, call = statement.targets[0].id, statement.value
    module, _, factory = (_resolve(call.func, context) or "").rpartition(".")
    if module not in TYPING_MODULES or factory not in TYPE_VARIABLE_FACTORIES:
        return None
    if not call.args or not isinstance(call.args[0], ast.Constant) or call.args[0].value != name:
        return None
    if any(keyword.arg is None for keyword in call.keywords):
        return None
    arguments = [*call.args[1:], *(keyword.value for keyword in call.keywords)]
    if not all(runs_nothing(argument, context) for argument in arguments):
        return None
    return name if name in context.annotation_names else None


def without_typing(
    body: list[ast.stmt], context: TypingContext, *, module: bool
) -> tuple[list[ast.stmt], frozenset[str]]:
    """A module's or a plain class's own statements without what only a type checker reads, and
    the type variables left out.

    An annotated assignment runs as the plain assignment it holds, and one of a bare name without
    a value runs nothing, when its annotation is deferred or runs nothing (see `runs_nothing`):
    only the `__annotations__` a type checker reads differ. A module's type variables that only
    annotations read are left out (see `_type_variable`).
    """
    kept: list[ast.stmt] = []
    variables: set[str] = set()
    for statement in body:
        if isinstance(statement, ast.AnnAssign) and (
            context.deferred or runs_nothing(statement.annotation, context)
        ):
            if statement.value is not None:
                assign = ast.Assign(
                    targets=[statement.target], value=statement.value, type_comment=None
                )
                kept.append(ast.copy_location(assign, statement))
                continue
            if statement.simple and isinstance(statement.target, ast.Name):
                continue
        elif module and (name := _type_variable(statement, context)) is not None:
            variables.add(name)
            continue
        kept.append(statement)
    return kept, frozenset(variables)


def without_version(body: list[ast.stmt]) -> tuple[list[ast.stmt], str] | None:
    """A module's own statements with the text of its `__version__` string blanked, and that text,
    or None when none of them assigns `__version__` a string."""
    value: ast.expr | None
    for index, statement in enumerate(body):
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target, value = statement.targets[0], statement.value
        elif isinstance(statement, ast.AnnAssign):
            target, value = statement.target, statement.value
        else:
            continue
        if (
            isinstance(target, ast.Name)
            and target.id == "__version__"
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        ):
            blank = copy.copy(statement)
            blank.value = ast.Constant(value="")
            return [*body[:index], blank, *body[index + 1 :]], value.value
    return None


def typing_beyond_parameters(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """A callable's typing other than its parameters' annotations: its type parameters, return
    annotation, and type comment."""
    returns = f" -> {unparse_redacted(node.returns, 300)}" if node.returns is not None else ""
    comment = (
        f" # type: {redact_text(node.type_comment, max_chars=300)}" if node.type_comment else ""
    )
    return f"{type_parameters(node)}{returns}{comment}"


def annotations_unread(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    qualified_name: str,
    kind: str,
    context: TypingContext,
) -> bool:
    """Whether nothing a callable is decorated with or defined in reads its annotations: each
    decorator is one of `INERT_DECORATORS`, and a method's class is plain, so no class decorator,
    metaclass, or hook reads them either."""
    if kind in {"method", "async_method"} and (
        qualified_name.rpartition(".")[0] not in context.plain_classes
    ):
        return False
    return all((_resolve(item, context) or "") in INERT_DECORATORS for item in node.decorator_list)


def parameter_typing(
    node: ast.FunctionDef | ast.AsyncFunctionDef, context: TypingContext
) -> tuple[tuple[str, str, bool], ...]:
    """Each annotated parameter, a digest of its whole annotation, and whether evaluating that
    annotation runs nothing: deferred, or an annotation `runs_nothing` accepts. An annotation
    that did not change runs as it ran, so only a changed one needs to."""
    arguments = node.args
    return tuple(
        (
            item.arg,
            hashlib.sha256(
                ast.dump(item.annotation, include_attributes=False).encode("utf-8")
            ).hexdigest(),
            context.deferred or runs_nothing(item.annotation, context),
        )
        for item in (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
            arguments.vararg,
            arguments.kwarg,
        )
        if item is not None and item.annotation is not None
    )


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
# Code a function defines but does not run itself: what it computes is not what the function
# returns.
_NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
# The statements whose test or header feeds a function's value when code in their blocks does.
_COMPOUND = (ast.If, ast.While, ast.For, ast.AsyncFor, ast.With, ast.AsyncWith)
# What gives a function's value.
_GIVES = (ast.Return, ast.Yield, ast.YieldFrom)
# The expressions that write or give a value from inside another statement's code.
_INLINE = (ast.NamedExpr, ast.Yield, ast.YieldFrom)
# The same node types as `node_inventory` names them.
_INLINE_NAMES = frozenset(kind.__name__ for kind in _INLINE)
# Where a statement keeps the statements it holds, in the order of its fields: a block, a `try`'s
# handlers, a `match`'s cases. A handler or a case keeps its own in its `body`.
_STATEMENT_FIELDS = ("body", "handlers", "orelse", "finalbody", "cases")


def _own_code(
    statements: list[ast.stmt], *, inline: bool = True
) -> list[tuple[ast.AST, tuple[ast.stmt, ...]]]:
    """Every statement in `statements`, none inside a function or class they define, each with
    the compound statements (see `_COMPOUND`) whose blocks hold it. With `inline`, also every
    assignment expression, `yield`, and `yield from` in their code, none inside a lambda: a caller
    that knows the code has none passes False, and no expression is read."""
    found: list[tuple[ast.AST, tuple[ast.stmt, ...]]] = []
    pending: list[tuple[ast.AST, tuple[ast.stmt, ...]]] = [(item, ()) for item in statements]
    while pending:
        node, enclosing = pending.pop()
        if isinstance(node, _NESTED_SCOPES):
            continue
        if isinstance(node, ast.stmt) or (inline and isinstance(node, _INLINE)):
            found.append((node, enclosing))
        # A compound statement's blocks are its only statements; its test or header is not one.
        holding = (*enclosing, node) if isinstance(node, _COMPOUND) else enclosing
        if not inline:
            pending.extend(
                (child, holding)
                for field in _STATEMENT_FIELDS
                for child in getattr(node, field, ())
            )
            continue
        for child in ast.iter_child_nodes(node):
            pending.append((child, holding if isinstance(child, ast.stmt) else enclosing))
    return found


def _chain(node: ast.AST) -> str | None:
    """`a.b.c` for a name or an attribute chain rooted at one, else None."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return ".".join([node.id, *reversed(parts)])
    return None


def _read_chains(node: ast.AST) -> set[str]:
    """The longest names and attribute chains an expression reads: `self.file.read`, and not
    `self.file` or `self` on its account."""
    found: set[str] = set()
    pending: list[ast.AST] = [node]
    while pending:
        item = pending.pop()
        if isinstance(item, (ast.Name, ast.Attribute)) and (chain := _chain(item)) is not None:
            found.add(chain)
            continue
        pending.extend(ast.iter_child_nodes(item))
    return found


def _written_chains(target: ast.AST) -> set[str]:
    """What an assignment target writes to: a name or an attribute chain, and for an item such
    as `result[key]`, the container it changes."""
    if isinstance(target, (ast.Tuple, ast.List)):
        return set().union(*(_written_chains(item) for item in target.elts))
    if isinstance(target, (ast.Starred, ast.Subscript)):
        return _written_chains(target.value)
    chain = _chain(target)
    return {chain} if chain is not None else set()


def _prefixes(chain: str) -> Iterator[str]:
    """The objects `chain` is an attribute of: `a` and `a.b` for `a.b.c`."""
    end = chain.find(".")
    while end != -1:
        yield chain[:end]
        end = chain.find(".", end + 1)


def _related(chain: str, chains: set[str], around: set[str]) -> bool:
    """Whether writing `chain` changes what reading one of `chains` gives: the same name, an
    attribute of it, or an object it is an attribute of. `around` holds every object a chain in
    `chains` is an attribute of (see `_prefixes`), so no chain is compared with each."""
    if chain in chains or chain in around:
        return True
    return "." in chain and any(prefix in chains for prefix in _prefixes(chain))


def _binding(node: ast.AST) -> tuple[set[str], list[ast.AST]] | None:
    """What a statement or expression writes to, and the expressions those values come from."""
    if isinstance(node, ast.Assign):
        return set().union(*(_written_chains(item) for item in node.targets)), [node.value]
    if isinstance(node, (ast.AugAssign, ast.NamedExpr)):
        return _written_chains(node.target), [node.value]
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        return _written_chains(node.target), [node.value]
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return _written_chains(node.target), [node.iter]
    if isinstance(node, (ast.With, ast.AsyncWith)):
        written: set[str] = set()
        values: list[ast.AST] = []
        for item in node.items:
            if item.optional_vars is not None:
                written |= _written_chains(item.optional_vars)
                values.append(item.context_expr)
        return (written, values) if values else None
    return None


def _called_on(node: ast.AST) -> str | None:
    """What a call statement such as `output.append(segment)` calls a method of, else None."""
    if not isinstance(node, ast.Expr):
        return None
    call = node.value.value if isinstance(node.value, ast.Await) else node.value
    if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute):
        return _chain(call.func.value)
    return None


def _span(first: ast.AST, last: ast.AST | None = None) -> range:
    """The lines from `first`'s to `last`'s last one, or `first`'s own."""
    start = int(getattr(first, "lineno", 0))
    end = getattr(last if last is not None else first, "end_lineno", None) or start
    return range(start, int(end) + 1)


def _header(node: ast.AST) -> range:
    """The lines of a compound statement's test or header, without its blocks."""
    if isinstance(node, (ast.If, ast.While)):
        return _span(node, node.test)
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return _span(node, node.iter)
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return _span(node, node.items[-1].optional_vars or node.items[-1].context_expr)
    return _span(node)


def output_lines(
    node: ast.FunctionDef | ast.AsyncFunctionDef, *, inline: bool = True
) -> frozenset[int]:
    """The lines of a function whose code feeds the value it returns or yields.

    What a `return` or a `yield` gives reads some names and attribute chains; a statement that
    writes one of those reads others, and so on (see `_related`). A line feeds the value when it
    returns or yields one, writes such a name or calls a method of one, or holds the test of an
    `if` or a `while`, or the header of a `for` or a `with`, whose blocks do. Nested functions,
    classes, and lambdas are code of their own. Without a `return` or a `yield` that gives a
    value, no line does. A caller that knows the body holds no assignment expression, `yield`, or
    `yield from` passes `inline=False`, and only statements are read (see `_own_code`).
    """
    code = _own_code(node.body, inline=inline)
    given: list[ast.expr] = []
    for item, _ in code:
        if isinstance(item, _GIVES) and item.value is not None:
            given.append(item.value)
    if not given:
        return frozenset()
    reads: set[str] = set().union(*(_read_chains(value) for value in given))
    around = {prefix for chain in reads for prefix in _prefixes(chain)}
    found = [_binding(item) for item, _ in code]
    bindings = [binding for binding in found if binding is not None]
    grew = True
    while grew:
        grew = False
        for written, values in bindings:
            if any(_related(chain, reads, around) for chain in written):
                more = set().union(*(_read_chains(value) for value in values)) - reads
                if more:
                    reads |= more
                    around.update(prefix for chain in more for prefix in _prefixes(chain))
                    grew = True

    def feeds(item: ast.AST, binding: tuple[set[str], list[ast.AST]] | None) -> bool:
        if isinstance(item, _GIVES):
            return item.value is not None
        if binding is not None and any(_related(chain, reads, around) for chain in binding[0]):
            return True
        called = _called_on(item)
        return called is not None and _related(called, reads, around)

    lines: set[int] = set()
    # The compound statements whose blocks hold code that feeds the value, by identity.
    holding: set[int] = set()
    for (item, enclosing), binding in zip(code, found, strict=True):
        if not feeds(item, binding):
            continue
        lines.update(_header(item) if isinstance(item, _COMPOUND) else _span(item))
        holding.update(id(compound) for compound in enclosing)
    for item, _ in code:
        if id(item) in holding:
            lines.update(_header(item))
    return frozenset(lines)


def _literal(node: ast.expr) -> bool:
    """A constant, or arithmetic and concatenation of constants: `2 ** 16`, `-1`, `'a' 'b'`."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.UnaryOp):
        return _literal(node.operand)
    if isinstance(node, ast.BinOp):
        return _literal(node.left) and _literal(node.right)
    return False


def _constant_call(call: ast.Call, context: TypingContext) -> bool:
    """A compiled pattern of a literal, a frozenset or a tuple of constants, or a type variable
    whose arguments run nothing (see `runs_nothing`)."""
    target = _resolve(call.func, context) or ""
    arguments = [*call.args, *(keyword.value for keyword in call.keywords)]
    if any(keyword.arg is None for keyword in call.keywords):
        return False
    if target == "re.compile":
        return (
            bool(call.args)
            and isinstance(call.args[0], ast.Constant)
            and isinstance(call.args[0].value, (str, bytes))
            and all(runs_nothing(argument, context) for argument in arguments[1:])
        )
    if target in {"builtins.frozenset", "builtins.tuple"}:
        if not arguments:
            return True
        items = call.args[0] if len(call.args) == 1 and not call.keywords else None
        return isinstance(items, (ast.Tuple, ast.List, ast.Set)) and all(
            _constant(item, context) for item in items.elts
        )
    module, _, factory = target.rpartition(".")
    return (
        module in TYPING_MODULES
        and factory in TYPE_VARIABLE_FACTORIES
        and all(runs_nothing(argument, context) for argument in arguments)
    )


def _constant(node: ast.expr, context: TypingContext) -> bool:
    """Whether evaluating a module-level value runs nothing, and what it holds cannot change: a
    literal or a tuple of constants, a bare name it aliases, a type expression that runs nothing
    (see `runs_nothing`), or one of the calls `_constant_call` admits.

    An attribute, an item, or an operator of anything else may run code as the module loads, a
    property, a `__getitem__`, or an `__or__`; and a list, a dict, a set, or another call can hold
    state the module's code changes later, as rich's `_id_generator = count()` does.
    """
    if _literal(node) or isinstance(node, ast.Name):
        return True
    if isinstance(node, ast.Tuple):
        return all(_constant(item, context) for item in node.elts)
    if isinstance(node, (ast.Attribute, ast.Subscript, ast.BinOp)):
        return runs_nothing(node, context)
    if isinstance(node, ast.Call):
        return _constant_call(node, context)
    return False


def _star_imports(body: list[ast.stmt]) -> bool:
    """Whether any of a module's top-level code imports `*` from another module."""
    return any(
        isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
        for statement in body
        for node in ast.walk(statement)
    )


def _top_level_binding(statement: ast.stmt) -> tuple[str, ast.expr] | None:
    """The name and value of a plain `NAME = value`, or `NAME: annotation = value`."""
    if (
        isinstance(statement, ast.Assign)
        and len(statement.targets) == 1
        and isinstance(statement.targets[0], ast.Name)
    ):
        return statement.targets[0].id, statement.value
    if (
        isinstance(statement, ast.AnnAssign)
        and isinstance(statement.target, ast.Name)
        and statement.value is not None
    ):
        return statement.target.id, statement.value
    return None


def module_constants(
    body: list[ast.stmt], context: TypingContext
) -> tuple[dict[str, str], list[ast.stmt]]:
    """A module's names bound once in its top-level code, to a constant (see `_constant`), each to
    a digest of its value; and its other statements.

    A dunder such as `__all__` or `__version__` is never one: a new `__all__` changes what
    `import *` gives. What reads a constant is a symbol of its own, so a constant added, or a
    private one removed, changes no behavior of the module itself.
    """
    stored = Counter(
        item.id
        for statement in body
        for item in ast.walk(statement)
        if isinstance(item, ast.Name) and isinstance(item.ctx, (ast.Store, ast.Del))
    )
    for statement in body:
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            stored.update(name for name, _ in _bound_by(statement))
    constants: dict[str, str] = {}
    others: list[ast.stmt] = []
    for statement in body:
        binding = _top_level_binding(statement)
        if binding is not None:
            name, value = binding
            dunder = name.startswith("__") and name.endswith("__")
            if stored[name] == 1 and not dunder and _constant(value, context):
                dump = ast.dump(value, include_attributes=False)
                constants[name] = hashlib.sha256(dump.encode("utf-8")).hexdigest()
                continue
        others.append(statement)
    return constants, others


def _parameters(node: ast.arguments) -> set[str]:
    return {
        item.arg
        for item in (*node.posonlyargs, *node.args, *node.kwonlyargs, node.vararg, node.kwarg)
        if item is not None
    }


def _listed_in_all(node: ast.Assign | ast.AugAssign | ast.AnnAssign | ast.Call) -> list[str]:
    """The names a statement or call puts in `__all__`: `__all__ = [...]`, `__all__ += [...]`,
    and `__all__.extend([...])` or `.append(...)`."""
    listed: ast.AST | None = None
    if isinstance(node, ast.Call):
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "__all__"
        ):
            listed = node
    else:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(item, ast.Name) and item.id == "__all__" for item in targets):
            listed = node.value
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
    """The names a file mentions, the names it reads, and what its imports bind, in one walk.

    It mentions each name its code holds, in any role and any block, annotations and
    `TYPE_CHECKING` blocks among them. It reads each name some code reads without binding it in its
    own function: module and class code reads every name it uses, as a global or a builtin, and a
    function every name it does not bind itself — as a parameter, an assignment, an import, a
    definition, a caught exception, or a match capture — unless it declares the name `global` or
    `nonlocal`. A name a nested function reads from the function around it counts as read too, and
    a function's decorators, defaults, and annotations are read where it is defined. Each name its
    `__all__` lists, which a star import of the module reads, counts as both. Each name an import
    binds, at the top level or not, maps to what the imports binding it import.
    """
    mentioned: set[str] = set()
    exported: set[str] = set()
    imported: dict[str, set[str]] = defaultdict(set)
    # Per scope, the module's and then each function's: what its code reads, binds, and declares.
    loads: list[set[str]] = [set()]
    bound: list[set[str]] = [set()]
    declared: list[set[str]] = [set()]
    # Each node with its scope, and whether what it binds is that scope's: a class body's is not.
    pending: list[tuple[ast.AST, int, bool]] = [(tree, 0, True)]
    while pending:
        node, scope, binds = pending.pop()
        if isinstance(node, ast.Name):
            mentioned.add(node.id)
            if isinstance(node.ctx, ast.Load):
                loads[scope].add(node.id)
            elif binds:
                bound[scope].add(node.id)
            continue
        if isinstance(node, _FUNCTION_SCOPES):
            inner = len(loads)
            loads.append(set())
            bound.append(_parameters(node.args))
            declared.append(set())
            if binds and not isinstance(node, ast.Lambda):
                bound[scope].add(node.name)
            body = node.body if isinstance(node.body, list) else [node.body]
            in_body = {id(item) for item in body}
            pending.extend(
                (child, inner, True) if id(child) in in_body else (child, scope, binds)
                for child in ast.iter_child_nodes(node)
            )
            continue
        if isinstance(node, ast.ClassDef):
            if binds:
                bound[scope].add(node.name)
            in_body = {id(item) for item in node.body}
            pending.extend(
                (child, scope, binds and id(child) not in in_body)
                for child in ast.iter_child_nodes(node)
            )
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for name, target in _bound_by(node):
                imported[name].add(target)
                if binds:
                    bound[scope].add(name)
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
            if node.name and binds:
                bound[scope].add(node.name)
        elif isinstance(node, ast.MatchMapping):
            if node.rest and binds:
                bound[scope].add(node.rest)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            if binds:
                declared[scope].update(node.names)
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Call)):
            exported.update(_listed_in_all(node))
        pending.extend((child, scope, binds) for child in ast.iter_child_nodes(node))
    read = set(loads[0])
    for index in range(1, len(loads)):
        read |= loads[index] - (bound[index] - declared[index])
    return (
        frozenset(mentioned | exported),
        frozenset(read | exported),
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
        self.stored: list[frozenset[str]] = []
        self.on_instance: list[bool] = []

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
        self.stored.append(_stored_names(*targets))
        self.on_instance.append(_writes_on_instance(*targets))

    def visit_Assign(self, node: ast.Assign) -> None:
        targets = ", ".join(unparse_redacted(item, 100) for item in node.targets)
        value = unparse_whole(node.value, limits.MAX_ASSIGNED_VALUE_CHARS)
        self._record_assignment(f"{targets} = {value}", node.lineno, *node.targets)
        self.statement_order.append(f"assign:{targets}")
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        value = (
            f"{unparse_redacted(node.target, 100)} = "
            f"{unparse_whole(node.value, limits.MAX_ASSIGNED_VALUE_CHARS)}"
        )
        self._record_assignment(value, node.lineno, node.target)
        self.statement_order.append(f"assign:{unparse_redacted(node.target, 100)}")
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        value = (
            f"{unparse_redacted(node.target, 100)} {type(node.op).__name__}= "
            f"{unparse_whole(node.value, limits.MAX_ASSIGNED_VALUE_CHARS)}"
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
    typing: TypingContext | None = None,
) -> SymbolSnapshot:
    """A symbol as the comparison reads it. A module's or a class's own statements are taken as
    they run: the code of an `if TYPE_CHECKING:` block, spelled as one of `type_checking`, is no
    part of either. With its file's `typing` context, what else only a type checker reads is
    fingerprinted apart (see `without_typing`, `parameter_typing`, and `annotations_unread`)."""
    if isinstance(node, (ast.Module, ast.ClassDef)):
        running = copy.copy(node)
        running.body = runtime_statements(node.body, type_checking)
        node = running
    body = list(getattr(node, "body", []))
    fingerprint = body_fingerprint(body)
    inventory = node_inventory(body)
    visitor = FeatureVisitor(node)
    visitor.visit(node)
    parameters: str | None = None
    imports: dict[str, tuple[str, ...]] = {}
    without_imports: str | None = None
    without_typing_fingerprint: str | None = None
    type_variables: frozenset[str] = frozenset()
    version_fingerprint: str | None = None
    version: str | None = None
    beyond_parameters: str | None = None
    annotated: tuple[tuple[str, str, bool], ...] = ()
    unread = False
    outputs: frozenset[int] = frozenset()
    constants: dict[str, str] = {}
    without_constants: str | None = None
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        signature = function_signature(node)
        parameters = function_parameters(node)
        defaults = default_map(node)
        decorators = tuple(decorator_name(item) for item in node.decorator_list)
        display = signature
        beyond_parameters = typing_beyond_parameters(node)
        # Without an assignment expression or a `yield` anywhere in it, a body's statements
        # alone say what feeds its value.
        outputs = output_lines(node, inline=any(name in _INLINE_NAMES for name, _ in inventory))
        if typing is not None:
            annotated = parameter_typing(node, typing)
            unread = annotations_unread(node, qualified_name, kind, typing)
    elif isinstance(node, ast.ClassDef):
        bases = ", ".join(unparse_redacted(item, 120) for item in node.bases)
        signature = f"{type_parameters(node)}bases({bases})"
        defaults = {}
        decorators = tuple(decorator_name(item) for item in node.decorator_list)
        display = class_display(node, decorators)
        if typing is not None and qualified_name in typing.plain_classes:
            kept, _ = without_typing(body, typing, module=False)
            without_typing_fingerprint = body_fingerprint(kept)
    else:
        signature = "module"
        defaults = {}
        decorators = ()
        display = module_display(body)
        imports, others = top_level_imports(body)
        without_imports = body_fingerprint(others)
        if typing is not None:
            kept, type_variables = without_typing(body, typing, module=True)
            without_typing_fingerprint = body_fingerprint(kept)
            # A star import may bind any name: none of this module's is read as a constant.
            if not _star_imports(body):
                constants, others_than_constants = module_constants(body, typing)
                without_constants = body_fingerprint(others_than_constants)
        versioned = without_version(body)
        if versioned is not None:
            version_fingerprint, version = body_fingerprint(versioned[0]), versioned[1]
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
        node_inventory=inventory,
        probes={name: tuple(values) for name, values in visitor.probes.items()},
        assigned=tuple(visitor.assigned),
        stored=tuple(visitor.stored),
        on_instance=tuple(visitor.on_instance),
        docstring_only=not body_without_docstring(body),
        display=display,
        parameters=parameters,
        # Only a body that raises can have a message to blank.
        fingerprint_without_messages=(
            fingerprint_without_messages(body, fingerprint)
            if any(name == "Raise" for name, _ in inventory)
            else fingerprint
        ),
        # Only a body with a dict literal can hold a schema.
        fingerprint_without_descriptions=(
            fingerprint_without_descriptions(body, fingerprint)
            if any(name == "Dict" for name, _ in inventory)
            else fingerprint
        ),
        imports=imports,
        fingerprint_without_imports=without_imports,
        fingerprint_without_typing=without_typing_fingerprint,
        type_variables=type_variables,
        fingerprint_without_version=version_fingerprint,
        version=version,
        typing_beyond_parameters=beyond_parameters,
        parameter_typing=annotated,
        annotations_unread=unread,
        output_lines=outputs,
        constants=constants,
        fingerprint_without_constants=without_constants,
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
    # A file that never writes `TYPE_CHECKING` spells it no way, and is spared the walk.
    type_checking = type_checking_spellings(tree) if "TYPE_CHECKING" in source else frozenset()
    typing = typing_context(tree, type_checking)
    for name, kind, node in _symbol_nodes(tree.body):
        if len(symbols) >= effective_budget.max_symbols_per_file:
            raise AstResourceLimit("symbol budget exceeded")
        snapshot = snapshot_symbol(node, name, kind, type_checking=type_checking, typing=typing)
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
