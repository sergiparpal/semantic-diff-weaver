"""Static Python AST extraction and behavior-bearing feature collection."""

from __future__ import annotations

import ast
import builtins
import copy
import hashlib
import sys
import warnings
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field

from ..path_policy import redact_text
from . import limits
from .limits import AstBudget
from .types import (
    BARE_RAISE,
    CAUGHT_AGAIN,
    CategoryProbe,
    ExitCondition,
    ExitPath,
    FileStep,
    FunctionExit,
    HandledFailure,
    LocalDefinition,
    ModuleReferences,
    SymbolReferences,
    SymbolSnapshot,
)


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


# What `call_name` names a callee that is neither a name nor an attribute chain of one.
DYNAMIC = "<dynamic>"


def call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return DYNAMIC


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


def description_values(statement: ast.AST) -> list[ast.Constant | ast.JoinedStr]:
    """The `description` values of the schemas a statement holds: each a string literal, or an
    f-string, whose literal parts are prose and whose interpolated values are code.

    A tool's schema, `{"description": "Empty the fridge…", "type": "object", ...}`, tells an agent
    what the tool does and when to call it, and the descriptions of its properties what to pass.
    A validator reads none of them.
    """
    values: list[ast.Constant | ast.JoinedStr] = []
    for node in ast.walk(statement):
        if not isinstance(node, ast.Dict) or not is_schema(node):
            continue
        for key, value in zip(node.keys, node.values, strict=True):
            if _text(key) != "description":
                continue
            if (isinstance(value, ast.Constant) and _text(value)) or isinstance(
                value, ast.JoinedStr
            ):
                values.append(value)
    return values


def description_literals(statement: ast.AST) -> list[ast.Constant]:
    """The prose of the `description` values of the schemas a statement holds: each string
    literal, and each literal part of an f-string (see `description_values`)."""
    literals: list[ast.Constant] = []
    for value in description_values(statement):
        if isinstance(value, ast.Constant):
            literals.append(value)
            continue
        literals.extend(
            part
            for part in value.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str) and part.value
        )
    return literals


def description_text(value: ast.Constant | ast.JoinedStr) -> str:
    """A `description` value as an agent reads it: an f-string's interpolated values in braces."""
    if isinstance(value, ast.Constant):
        return str(value.value)
    return "".join(
        str(part.value)
        if isinstance(part, ast.Constant)
        else f"{{{unparse_redacted(getattr(part, 'value', None), 100)}}}"
        for part in value.values
    )


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


def described_value(value: ast.expr | None) -> tuple[str, tuple[str, ...]]:
    """What the schemas an assigned value holds describe: a digest of the value with their
    `description` texts blanked, and those texts, redacted and bounded. Empty for a value that
    holds no description.

    Two assignments of one name with the same digest differ at most in how their schemas describe
    a tool or its properties, as two bodies do with the same `fingerprint_without_descriptions`. A
    module that adds a constant beside a tool's redescribed schema is no redescribed body, while
    its schema's assignment is still a redescribed value.
    """
    literals = description_literals(value) if value is not None else []
    if value is None or not literals:
        return "", ()
    texts = tuple(
        redact_text(description_text(item), max_chars=limits.MAX_DESCRIPTION_CHARS)
        for item in description_values(value)[: limits.MAX_ASSIGNED_DESCRIPTIONS]
    )
    # Blanked in place for one dump and restored, as `fingerprint_without_descriptions` does.
    wording = [literal.value for literal in literals]
    try:
        for literal in literals:
            literal.value = ""
        dump = ast.dump(value, include_attributes=False)
    finally:
        for literal, text in zip(literals, wording, strict=True):
            literal.value = text
    return hashlib.sha256(dump.encode("utf-8")).hexdigest(), texts


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


def _always_exits(statements: list[ast.stmt]) -> bool:
    """Whether every path through `statements` ends in a `return` or a `raise` of their own.

    Read from the last statement: an `if` whose two blocks both exit, a `with` whose block does, a
    `try` whose body or `else`, and every handler, exit, or whose `finally` does, a `while True`
    with no `break` of its own, and a `match` whose last case matches anything and whose every
    case exits. Anything else may run past the end.
    """
    if not statements:
        return False
    last = statements[-1]
    if isinstance(last, (ast.Return, ast.Raise)):
        return True
    if isinstance(last, ast.If):
        return _always_exits(last.body) and _always_exits(last.orelse)
    if isinstance(last, (ast.With, ast.AsyncWith)):
        return _always_exits(last.body)
    if isinstance(last, (ast.Try, ast.TryStar)):
        if _always_exits(last.finalbody):
            return True
        return (_always_exits(last.body) or _always_exits(last.orelse)) and all(
            _always_exits(handler.body) for handler in last.handlers
        )
    if isinstance(last, ast.While):
        endless = isinstance(last.test, ast.Constant) and last.test.value is True
        return endless and not _breaks(last.body)
    if isinstance(last, ast.Match):
        return (
            bool(last.cases)
            and _matches_anything(last.cases[-1])
            and all(_always_exits(case.body) for case in last.cases)
        )
    return False


def _matches_anything(case: ast.match_case) -> bool:
    """Whether a `match` case matches every value: `case _:` or `case name:`, with no guard."""
    return (
        case.guard is None
        and isinstance(case.pattern, ast.MatchAs)
        and case.pattern.pattern is None
    )


def _breaks(statements: list[ast.stmt]) -> bool:
    """Whether a `break` in `statements`, outside the loops and scopes they hold, ends their loop."""
    pending: list[ast.AST] = list(statements)
    while pending:
        node = pending.pop()
        if isinstance(node, ast.Break):
            return True
        if isinstance(node, (*_NESTED_SCOPES, ast.For, ast.AsyncFor, ast.While)):
            continue
        pending.extend(child for field in _STATEMENT_FIELDS for child in getattr(node, field, ()))
    return False


def function_exits(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    inline: bool = True,
    texts: Mapping[int, str] | None = None,
) -> tuple[tuple[FunctionExit, ...], int]:
    """What each `return` and `raise` of a function's own code hands back or raises, and how many.

    Each exit is its kind, `return` or `raise`; its value as code, empty for a bare `raise`; and
    the conditions on the path that leads there, outermost first, each a word and a test (see
    `BRANCH_CONDITIONS`). They are every branch the path takes, not the innermost alone: an
    `elif`'s `else` is reached only where both tests fail, and an `if` inside another only where
    both hold. `if` and the test stand for an `if`'s body, `else` and that test for its `else`,
    `except` and what a handler catches, empty for a bare one, and `case` and a `match` case's
    pattern. A path that takes no branch records instead what the loops and the `try` statements
    around the exit need: `for` and what a `for` iterates, `while` and a `while`'s test, `try` and
    what each handler of a `try` catches, for its body and its `else`, and `other` and each
    earlier pattern, for a case matching anything. A `while True`, a `with` block, and a
    `finally` need nothing. A path through more branches than `MAX_EXIT_CONDITIONS` records `deep`
    and how many; one through more loops and `try` statements records none. A body that may run
    past its end returns `None` there, with no condition. A generator has no exits: what it yields
    is what it gives. Nested functions and classes are code of their own. A caller that knows the
    body holds no `yield` or `yield from` passes `inline=False`, and one that has unparsed an
    expression already passes its text in `texts`, by the node's identity, as `FeatureVisitor`
    does. At most `MAX_FUNCTION_EXITS` are kept.
    """
    found = callable_exits(node, inline=inline, texts=texts)
    return found.exits, found.total


@dataclass(frozen=True)
class CallableExits:
    """A callable's exits as `function_exits` records them, and the local variables they read."""

    exits: tuple[FunctionExit, ...]
    total: int
    definitions: tuple[LocalDefinition, ...]


def callable_exits(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    inline: bool = True,
    texts: Mapping[int, str] | None = None,
) -> CallableExits:
    """`function_exits`'s exits, and the local variables their values read, in one walk.

    A name an exit's value reads is a local variable where the callable's own code assigns it
    and it is neither a parameter, which a test passes, nor a name the code declares `global` or
    `nonlocal`; a comprehension's or a lambda's own names are none. `return cls(file, size)`
    says little of what it returns until `cls = self.environ.get('wsgi.file_wrapper', Wrapper)`
    does. Each such variable, at most `MAX_DEFINED_LOCALS` in the order the exits first read them,
    keeps its assignments in the order of the code, at most `MAX_LOCAL_ASSIGNMENTS`, and how many
    there are. An assignment is `value` and the expression assigned, by `=`, an annotated `=`, or
    an augmented one as the operation it performs, `total + item`; or `item` and what a `for`
    iterates, or a target unpacks into more than one name. Each carries the conditions on the
    path to it, as an exit does. So does each statement that changes the object a variable names
    in place, at most `MAX_LOCAL_CHANGES`: `new_state['last_change'] = record` after `new_state =
    dict(state)` returns more than `dict(state)`. A change is `set` and an item or an attribute
    assigned, `delete` and one deleted, or `call` and a call of one of its methods made for what
    it does, its value unused.
    """
    if inline and any(
        isinstance(item, (ast.Yield, ast.YieldFrom)) for item, _ in _own_code(node.body)
    ):
        return CallableExits((), 0, ())
    found: list[FunctionExit] = []
    total = 0
    known = texts or {}
    # The recorded exits' values, in order, and what the code assigns, changes, and declares.
    values: list[ast.expr] = []
    # Each kept as its node until it proves needed: most are of names no exit reads, and
    # unparsing every assignment of every callable made extraction a seventh slower.
    assigned: dict[str, list[tuple[str, ast.expr, ExitPath]]] = {}
    assignments: dict[str, int] = {}
    changed: dict[str, list[tuple[str, ast.expr, ExitPath]]] = {}
    changes: dict[str, int] = {}
    declared: set[str] = set()

    def code(expression: ast.AST) -> str:
        return known.get(id(expression)) or unparse_redacted(expression, limits.MAX_EXIT_CHARS)

    def caught(handler: ast.ExceptHandler) -> str:
        return code(handler.type) if handler.type else ""

    def conditions_of(branches: ExitPath, around: ExitPath) -> ExitPath:
        conditions = branches or around
        if len(conditions) > limits.MAX_EXIT_CONDITIONS:
            return (("deep", str(len(branches))),) if branches else ()
        return conditions

    def record(
        kind: str,
        value: str,
        expression: ast.expr | None,
        branches: ExitPath,
        around: ExitPath,
    ) -> None:
        nonlocal total
        total += 1
        if len(found) >= limits.MAX_FUNCTION_EXITS:
            return
        found.append((kind, value, conditions_of(branches, around)))
        if expression is not None:
            values.append(expression)

    def assign(target: ast.expr, value: ast.expr, how: str, path: ExitPath) -> None:
        """Record each name `target` binds, to `value` whole or to an item of it."""
        if isinstance(target, ast.Name):
            assignments[target.id] = assignments.get(target.id, 0) + 1
            kept = assigned.setdefault(target.id, [])
            if len(kept) < limits.MAX_LOCAL_ASSIGNMENTS:
                kept.append((how, value, path))
        elif isinstance(target, ast.Starred):
            assign(target.value, value, "item", path)
        elif isinstance(target, (ast.Tuple, ast.List)):
            items = value.elts if isinstance(value, (ast.Tuple, ast.List)) else None
            whole = (
                how == "value"
                and items is not None
                and len(items) == len(target.elts)
                and not any(isinstance(item, ast.Starred) for item in (*items, *target.elts))
            )
            for index, element in enumerate(target.elts):
                if whole and items is not None:
                    assign(element, items[index], "value", path)
                else:
                    assign(element, value, "item", path)
        # An attribute or a subscript binds no local name: it changes what one names.
        elif isinstance(target, (ast.Attribute, ast.Subscript)):
            change("set", target, target, path)

    def change(how: str, target: ast.expr, shown: ast.expr, path: ExitPath) -> None:
        """Record that a statement changes, in place, what the name `target` is rooted at names."""
        root = target
        while isinstance(root, (ast.Attribute, ast.Subscript)):
            root = root.value
        if isinstance(root, ast.Name):
            changes[root.id] = changes.get(root.id, 0) + 1
            kept = changed.setdefault(root.id, [])
            if len(kept) < limits.MAX_LOCAL_CHANGES:
                kept.append((how, shown, path))

    def walk(statements: list[ast.stmt], branches: ExitPath, around: ExitPath) -> None:
        for statement in statements:
            if isinstance(statement, ast.Return):
                value = "None" if statement.value is None else code(statement.value)
                record("return", value, statement.value, branches, around)
            elif isinstance(statement, ast.Raise):
                exception = statement.exc
                record("raise", code(exception) if exception else "", exception, branches, around)
            elif isinstance(statement, ast.Assign):
                for target in statement.targets:
                    assign(target, statement.value, "value", conditions_of(branches, around))
            elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
                assign(statement.target, statement.value, "value", conditions_of(branches, around))
            elif isinstance(statement, ast.AugAssign) and isinstance(
                statement.target, (ast.Attribute, ast.Subscript)
            ):
                change("set", statement.target, statement.target, conditions_of(branches, around))
            elif isinstance(statement, ast.Delete):
                for target in statement.targets:
                    if isinstance(target, (ast.Attribute, ast.Subscript)):
                        change("delete", target, target, conditions_of(branches, around))
            elif (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Call)
                and isinstance(statement.value.func, ast.Attribute)
            ):
                # A method called for what it does, its value unused: `state.pop('lock', None)`.
                call = statement.value
                change("call", call.func, call, conditions_of(branches, around))
            elif isinstance(statement, ast.AugAssign) and isinstance(statement.target, ast.Name):
                # `total += item` assigns what the operation gives: `total + item`.
                operation = ast.BinOp(
                    left=ast.Name(id=statement.target.id, ctx=ast.Load()),
                    op=statement.op,
                    right=statement.value,
                )
                assign(statement.target, operation, "value", conditions_of(branches, around))
            elif isinstance(statement, (ast.Global, ast.Nonlocal)):
                declared.update(statement.names)
            elif isinstance(statement, ast.If):
                condition = code(statement.test)
                walk(statement.body, (*branches, ("if", condition)), around)
                walk(statement.orelse, (*branches, ("else", condition)), around)
            elif isinstance(statement, (ast.For, ast.AsyncFor)):
                assign(statement.target, statement.iter, "item", conditions_of(branches, around))
                walk(statement.body, branches, (*around, ("for", code(statement.iter))))
                walk(statement.orelse, branches, around)
            elif isinstance(statement, ast.While):
                endless = isinstance(statement.test, ast.Constant) and bool(statement.test.value)
                inside = around if endless else (*around, ("while", code(statement.test)))
                walk(statement.body, branches, inside)
                walk(statement.orelse, branches, around)
            elif isinstance(statement, (ast.With, ast.AsyncWith)):
                walk(statement.body, branches, around)
            elif isinstance(statement, (ast.Try, ast.TryStar)):
                uncaught = (*around, *(("try", caught(item)) for item in statement.handlers))
                walk(statement.body, branches, uncaught)
                for handler in statement.handlers:
                    walk(handler.body, (*branches, ("except", caught(handler))), around)
                walk(statement.orelse, branches, uncaught)
                walk(statement.finalbody, branches, around)
            elif isinstance(statement, ast.Match):
                earlier: list[ExitCondition] = []
                for case in statement.cases:
                    if _matches_anything(case):
                        # `case _:` is reached where no case before it matched.
                        walk(case.body, branches, (*around, *earlier))
                        continue
                    pattern = unparse_redacted(case.pattern, limits.MAX_EXIT_CHARS)
                    if case.guard is not None:
                        pattern += f" if {unparse_redacted(case.guard, limits.MAX_EXIT_CHARS)}"
                    walk(case.body, (*branches, ("case", pattern)), around)
                    earlier.append(("other", pattern))

    walk(node.body, (), ())
    if not _always_exits(node.body):
        record("return", "None", None, (), ())
    if not assigned:
        # What the values read matters only for a name the code assigns: most callables have none.
        return CallableExits(tuple(found), total, ())
    reads = (name for value in values for name in _names_read(value))
    read = [name for name in dict.fromkeys(reads) if name in assigned]
    arguments = node.args
    parameters = {
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
    definitions: list[LocalDefinition] = []
    for name in read:
        if name not in parameters and name not in declared:
            definitions.append(
                (
                    name,
                    tuple((how, code(value), path) for how, value, path in assigned[name]),
                    assignments[name],
                    tuple((how, code(value), path) for how, value, path in changed.get(name, ())),
                    changes.get(name, 0),
                )
            )
            if len(definitions) == limits.MAX_DEFINED_LOCALS:
                break
    return CallableExits(tuple(found), total, tuple(definitions))


def _names_read(expression: ast.expr) -> list[str]:
    """The names an expression reads, in the order they appear, but for those its comprehensions
    and lambdas bind for themselves."""
    if isinstance(expression, ast.Name):
        return [expression.id]
    if isinstance(expression, ast.Constant):
        return []
    own: set[str] = set()
    names: list[ast.Name] = []
    for item in ast.walk(expression):
        if isinstance(item, ast.Name):
            if isinstance(item.ctx, ast.Load):
                names.append(item)
        elif isinstance(item, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for generator in item.generators:
                own.update(
                    name.id for name in ast.walk(generator.target) if isinstance(name, ast.Name)
                )
        elif isinstance(item, ast.Lambda):
            arguments = item.args
            own.update(
                argument.arg
                for argument in (
                    *arguments.posonlyargs,
                    *arguments.args,
                    *arguments.kwonlyargs,
                    arguments.vararg,
                    arguments.kwarg,
                )
                if argument is not None
            )
    if len(names) > 1:
        names.sort(key=lambda item: (item.lineno, item.col_offset))
    return [item.id for item in names if item.id not in own]


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


def module_exports(body: list[ast.stmt]) -> tuple[str, ...]:
    """The names a module's top-level code lists in `__all__`, sorted and bounded: what
    `import *` binds."""
    listed: set[str] = set()
    for statement in body:
        if isinstance(statement, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            listed.update(_listed_in_all(statement))
        elif isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            listed.update(_listed_in_all(statement.value))
    return tuple(sorted(listed)[: limits.MAX_EXPORTED_NAMES])


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


# What a call is named for when it only reports a failure and undoes nothing: a logger, a warning.
_REPORTING_NAMES = frozenset(
    {"log", "logger", "logging", "warnings", "_log", "_logger", "LOG", "LOGGER"}
)
_REPORTING_CALLS = frozenset({"print", "traceback.print_exc", "traceback.print_exception"})
# The bodies of these run when what they define is called, not as the code around them does.
_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _reports(name: str) -> bool:
    """Whether a call, by its name, only reports: `logger.exception`, `warnings.warn`, `print`."""
    return name in _REPORTING_CALLS or bool(_REPORTING_NAMES & set(name.split(".")))


def _own_nodes(statements: Sequence[ast.AST]) -> Iterator[ast.AST]:
    """Every node of some statements of a callable's own code, in the order of the code, without
    the bodies of the functions, lambdas, and classes they define."""
    pending = list(reversed(statements))
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(node, _DEFINITIONS):
            pending.extend(reversed(list(ast.iter_child_nodes(node))))


def _statement_call(statement: ast.stmt) -> ast.Call | None:
    """The call a statement makes for what it does, its result unused: `repo.reinstate(event)`,
    `await stream.aclose()`. A call whose result a test or an assignment reads asks something."""
    if not isinstance(statement, ast.Expr):
        return None
    value = statement.value.value if isinstance(statement.value, ast.Await) else statement.value
    return value if isinstance(value, ast.Call) else None


def _only_reports(handler: ast.ExceptHandler) -> bool:
    """Whether a handler only reports what it caught, or passes: it raises nothing and calls
    nothing but a logger, a warning, or a print."""
    return not any(
        isinstance(node, ast.Raise)
        or (isinstance(node, ast.Call) and not _reports(call_name(node.func)))
        for node in _own_nodes(handler.body)
    )


def _undoing_calls(statements: Sequence[ast.stmt], reported: bool) -> Iterator[tuple[str, bool]]:
    """The calls some statements of a handler make for what they do whenever they run, a report
    aside, each with whether a failure of it is caught and only reported: by a `try` around it,
    every handler of which only reports, or by one around the statements themselves, `reported`.

    Those are the calls of the statements themselves, of a `with` block, and of the code a `try`
    guards and of its `finally`. A call under an `if`, a loop, or a `match`, or in a handler or
    the `else` of a `try`, runs only sometimes. pyjwt's `fetch_data` closed what it caught only
    `if isinstance(e, HTTPError)`, and its obligation said the close ran for every error it
    caught.
    """
    for statement in statements:
        call = _statement_call(statement)
        if call is not None:
            name = call_name(call.func)
            if not _reports(name):
                # `connection().rollback()` reads better as code than as `<dynamic>.rollback`.
                yield (name if DYNAMIC not in name else unparse_redacted(call.func, 120)), reported
        elif isinstance(statement, (ast.Try, ast.TryStar)):
            caught = bool(statement.handlers) and all(map(_only_reports, statement.handlers))
            yield from _undoing_calls(statement.body, reported or caught)
            yield from _undoing_calls(statement.finalbody, reported)
        elif isinstance(statement, (ast.With, ast.AsyncWith)):
            yield from _undoing_calls(statement.body, reported)


def _raised_at_end(handler: ast.ExceptHandler) -> str | None:
    """What a handler's last statement raises, `CAUGHT_AGAIN` for what it caught, or None if that
    statement raises nothing."""
    last = handler.body[-1]
    if not isinstance(last, ast.Raise):
        return None
    if last.exc is None or (isinstance(last.exc, ast.Name) and last.exc.id == handler.name):
        return CAUGHT_AGAIN
    named = call_name(last.exc.func if isinstance(last.exc, ast.Call) else last.exc)
    return named if DYNAMIC not in named else unparse_redacted(last.exc, 120)


# What a list, a mapping, a set, or a string does in memory: no step a handler guards against.
_IN_MEMORY_METHODS = frozenset(
    {
        "add",
        "append",
        "clear",
        "copy",
        "discard",
        "extend",
        "format",
        "get",
        "insert",
        "items",
        "join",
        "keys",
        "pop",
        "remove",
        "setdefault",
        "split",
        "strip",
        "update",
        "values",
    }
)


def _takes_a_step(name: str) -> bool:
    """Whether a guarded call, by its name, may be the step that fails.

    A class called by its name builds a value, `_Handler()` as much as `Request()`. A container's
    or a string's method works in memory. Both were what pyjwt's `fetch_data` did before its
    opener sent the request.
    """
    last = name.rpartition(".")[2]
    if DYNAMIC in name or _reports(name) or last.lstrip("_")[:1].isupper():
        return False
    return not ("." in name and last in _IN_MEMORY_METHODS)


def _guarded_calls(statements: Sequence[ast.stmt]) -> tuple[str, ...]:
    """The calls of the code a `try` guards that may be the step that fails, in order, each once.

    A call on what another call returns, `urlopen(url).read()`, is named by that other call alone.
    The calls come in the order they run, where they end: the call an argument makes before the
    call it is passed to.
    """
    calls = [node for node in _own_nodes(statements) if isinstance(node, ast.Call)]
    calls.sort(key=lambda node: (node.end_lineno or 0, node.end_col_offset or 0))
    names = (call_name(node.func) for node in calls)
    return tuple(dict.fromkeys(filter(_takes_a_step, names)))[: limits.MAX_HANDLER_CALLS]


def handled_failures(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[HandledFailure, ...]:
    """The handlers of a callable's own code that undo or close something and then raise.

    A handler counts when its last statement raises and something it runs whenever it runs, before
    that, is called for what it does, a logger's report aside. The field test's `HANDLER` put a
    retracted event back with `reinstate_event` when restoring the fridge failed, logged a failure
    of that, and raised again what it caught. Its obligation said only "Exercise the changed
    failure trigger". A handler that asks something, `if repo.damaged():`, and returns or raises
    on the answer undoes nothing.
    """
    found: list[HandledFailure] = []
    for statement in _own_nodes(node.body):
        if not isinstance(statement, (ast.Try, ast.TryStar)):
            continue
        for handler in statement.handlers:
            raised = _raised_at_end(handler)
            calls = list(_undoing_calls(handler.body[:-1], False)) if raised else []
            if raised is None or not calls:
                continue
            found.append(
                (
                    unparse_redacted(handler.type, 200) if handler.type is not None else "",
                    _guarded_calls(statement.body),
                    tuple(dict.fromkeys(name for name, _ in calls))[: limits.MAX_HANDLER_CALLS],
                    all(reported for _, reported in calls),
                    raised,
                )
            )
            if len(found) == limits.MAX_HANDLED_FAILURES:
                return tuple(found)
    return tuple(found)


# The calls that put, copy, or remove a file, write one, or make a temporary one, by their name.
_FILE_CALLS: tuple[tuple[str, frozenset[str]], ...] = (
    ("move", frozenset({"os.replace", "os.rename", "os.renames", "shutil.move"})),
    ("copy", frozenset({"shutil.copy", "shutil.copy2", "shutil.copyfile", "shutil.copytree"})),
    ("delete", frozenset({"os.remove", "os.unlink", "os.rmdir", "shutil.rmtree"})),
    (
        "temp",
        frozenset(
            {
                "tempfile.mkstemp",
                "tempfile.mkdtemp",
                "tempfile.NamedTemporaryFile",
                "tempfile.TemporaryDirectory",
            }
        ),
    ),
    ("write", frozenset({"json.dump", "pickle.dump", "marshal.dump", "os.write", "os.fsync"})),
)
# The functions that open a file, its mode their second argument, and `Path.open`'s first.
_FILE_OPENERS = frozenset({"open", "io.open", "os.fdopen", "codecs.open"})


def file_operation(call: ast.Call) -> str | None:
    """What a call does to a file, by its name and arguments: a kind of `FILE_STEP_KINDS`, or None.

    A method names a path's operation only when no other common type has it with those arguments.
    `Path.rename` and `Path.replace` take one target, where `str.replace` takes two.
    """
    name = call_name(call.func)
    for kind, names in _FILE_CALLS:
        if name in names:
            return kind
    method = call.func.attr if isinstance(call.func, ast.Attribute) else None
    if name in _FILE_OPENERS or method == "open":
        position = 0 if name not in _FILE_OPENERS else 1
        mode = call.args[position] if len(call.args) > position else None
        for keyword in call.keywords:
            if keyword.arg == "mode":
                mode = keyword.value
        text = mode.value if isinstance(mode, ast.Constant) and isinstance(mode.value, str) else ""
        return "write" if set(text) & set("wax+") else None
    if method in {"write_text", "write_bytes"}:
        return "write"
    if method in {"rename", "replace"} and len(call.args) == 1 and not call.keywords:
        return "move"
    if method in {"unlink", "rmdir"}:
        return "delete"
    return None


def file_steps(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[FileStep, ...]:
    """A callable's own file operations, in the order of its code: the temporary file an atomic
    write writes, and the `os.replace` that puts it in place of the original."""
    steps: list[FileStep] = []
    for item in _own_nodes(node.body):
        if isinstance(item, ast.Call) and (kind := file_operation(item)) is not None:
            steps.append((kind, call_name(item.func), item.lineno))
            if len(steps) == limits.MAX_FILE_STEPS:
                break
    return tuple(steps)


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
        self.descriptions: list[tuple[str, tuple[str, ...]]] = []
        # The text of each test, returned value, raised exception, and caught type it recorded,
        # by the node's identity, for `function_exits` to reuse rather than unparse again.
        self.texts: dict[int, str] = {}

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
        text = unparse_redacted(test)
        self.texts[id(test)] = text
        self.conditions.append((text, lineno))
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
        text = BARE_RAISE if node.exc is None else unparse_redacted(node.exc)
        if node.exc is not None:
            self.texts[id(node.exc)] = text
        self.raises.append((text, node.lineno))
        self.statement_order.append(
            BARE_RAISE if node.exc is None else f"raise:{unparse_redacted(node.exc, 120)}"
        )
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        text = unparse_redacted(node.type)
        if node.type is not None:
            self.texts[id(node.type)] = text
        self.handlers.append((text, node.lineno))
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
        text = unparse_redacted(node.value)
        if node.value is not None:
            self.texts[id(node.value)] = text
        self.returns.append((text, node.lineno))
        self.statement_order.append(f"return:{unparse_redacted(node.value, 120)}")
        self.generic_visit(node)

    def _record_assignment(
        self, text: str, lineno: int, value: ast.expr | None, *targets: ast.expr
    ) -> None:
        # The probe reads the targets alone: what a value is written to tells state from
        # configuration or data, while a value such as `get_state()` or `RandomState(0)`
        # names state without moving any.
        self.assignments.append((text, lineno))
        self.probes["assignments"].append(category_probe(*targets))
        self.assigned.append(_assigned_variables(*targets))
        self.stored.append(_stored_names(*targets))
        self.on_instance.append(_writes_on_instance(*targets))
        self.descriptions.append(described_value(value))

    def visit_Assign(self, node: ast.Assign) -> None:
        targets = ", ".join(unparse_redacted(item, 100) for item in node.targets)
        value = unparse_whole(node.value, limits.MAX_ASSIGNED_VALUE_CHARS)
        self._record_assignment(f"{targets} = {value}", node.lineno, node.value, *node.targets)
        self.statement_order.append(f"assign:{targets}")
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        value = (
            f"{unparse_redacted(node.target, 100)} = "
            f"{unparse_whole(node.value, limits.MAX_ASSIGNED_VALUE_CHARS)}"
        )
        self._record_assignment(value, node.lineno, node.value, node.target)
        self.statement_order.append(f"assign:{unparse_redacted(node.target, 100)}")
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        value = (
            f"{unparse_redacted(node.target, 100)} {type(node.op).__name__}= "
            f"{unparse_whole(node.value, limits.MAX_ASSIGNED_VALUE_CHARS)}"
        )
        # An update in place is never read as a value redescribed: it adds to what was there.
        self._record_assignment(value, node.lineno, None, node.target)
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


def parameter_kinds(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[tuple[str, str], ...]:
    """Each parameter in order, with how a caller passes it."""
    arguments = node.args
    kinds = [(item.arg, "positional_only") for item in arguments.posonlyargs]
    kinds += [(item.arg, "positional") for item in arguments.args]
    if arguments.vararg:
        kinds.append((arguments.vararg.arg, "var_positional"))
    kinds += [(item.arg, "keyword_only") for item in arguments.kwonlyargs]
    if arguments.kwarg:
        kinds.append((arguments.kwarg.arg, "var_keyword"))
    return tuple(kinds)


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
    exits: tuple[FunctionExit, ...] = ()
    exits_total = 0
    definitions: tuple[LocalDefinition, ...] = ()
    exports: tuple[str, ...] = ()
    kinds: tuple[tuple[str, str], ...] | None = None
    failures: tuple[HandledFailure, ...] = ()
    steps: tuple[FileStep, ...] = ()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        failures, steps = handled_failures(node), file_steps(node)
        signature = function_signature(node)
        parameters = function_parameters(node)
        kinds = parameter_kinds(node)
        defaults = default_map(node)
        decorators = tuple(decorator_name(item) for item in node.decorator_list)
        display = signature
        beyond_parameters = typing_beyond_parameters(node)
        # Without an assignment expression or a `yield` anywhere in it, a body's statements
        # alone say what feeds its value.
        inline = any(name in _INLINE_NAMES for name, _ in inventory)
        outputs = output_lines(node, inline=inline)
        walked = callable_exits(node, inline=inline, texts=visitor.texts)
        exits, exits_total, definitions = walked.exits, walked.total, walked.definitions
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
        exports = module_exports(body)
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
        descriptions=tuple(visitor.descriptions),
        exports=exports,
        exits=exits,
        exits_total=exits_total,
        definitions=definitions,
        docstring_only=not body_without_docstring(body),
        display=display,
        parameters=parameters,
        parameter_kinds=kinds,
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
        handled_failures=failures,
        file_steps=steps,
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


def _declares(statement: ast.stmt) -> bool:
    """Whether a statement of a method's body only declares it: `...`, `pass`, or a raise of
    `NotImplementedError`."""
    if isinstance(statement, ast.Pass):
        return True
    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
        return statement.value.value is Ellipsis
    if isinstance(statement, ast.Raise) and statement.exc is not None:
        raised = statement.exc.func if isinstance(statement.exc, ast.Call) else statement.exc
        return isinstance(raised, ast.Name) and raised.id == "NotImplementedError"
    return False


def declares_only(method: ast.FunctionDef | ast.AsyncFunctionDef, owner: ast.ClassDef) -> bool:
    """Whether a method only declares what the implementations of its class define: a member of a
    `Protocol`, or an abstract method, whose body holds nothing but a docstring, `...`, `pass`, or
    a raise of `NotImplementedError`.

    No code of its own runs: a call through it reaches an implementation's. A plain class's method
    that does nothing by default is a hook, whose default is its behavior, and is not one.
    """
    protocol = any(base.rpartition(".")[2] == "Protocol" for base in _bases(owner))
    abstract = any(
        decorator_name(item).rpartition(".")[2] == "abstractmethod"
        for item in method.decorator_list
    )
    if not (protocol or abstract):
        return False
    return all(_declares(statement) for statement in body_without_docstring(method.body))


def _method_owners(tree: ast.AST) -> dict[int, ast.ClassDef]:
    """Each method's class, by the identity of the method's node."""
    return {
        id(item): node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        for item in node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def extract_symbols(source: str, budget: AstBudget | None = None) -> list[SymbolSnapshot]:
    effective_budget = budget or AstBudget.default()
    tree = parse_module(source)
    _validate_ast_budget(tree, effective_budget)
    symbols: list[SymbolSnapshot] = []
    # A file that never writes `TYPE_CHECKING` spells it no way, and is spared the walk.
    type_checking = type_checking_spellings(tree) if "TYPE_CHECKING" in source else frozenset()
    typing = typing_context(tree, type_checking)
    owners = _method_owners(tree)
    for name, kind, node in _symbol_nodes(tree.body):
        if len(symbols) >= effective_budget.max_symbols_per_file:
            raise AstResourceLimit("symbol budget exceeded")
        snapshot = snapshot_symbol(node, name, kind, type_checking=type_checking, typing=typing)
        owner = owners.get(id(node))
        if owner is not None and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            snapshot.owner_bases = _bases(owner)
            snapshot.declared_only = declares_only(node, owner)
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


# The nodes `symbol_references` reads. Most of a symbol's nodes are none of them, contexts,
# constants, and operators above all, and one lookup of a node's type skips each such node where
# a test of each type would make six.
_REFERENCE_NODES = frozenset(
    {ast.Name, ast.Attribute, ast.Call, ast.arg, ast.ExceptHandler, ast.Global}
)


# What a symbol that calls nothing of a kind shares, rather than an empty set of its own: most
# of a call graph's symbols are small, and building their references is most of building it.
_NONE: frozenset[str] = frozenset()


def symbol_references(node: ast.AST, kind: str) -> SymbolReferences:
    """What a symbol's code uses — every name and attribute, called or not — and its chains, and
    which of those names and attributes it calls."""
    names: set[str] = set()
    dotted: set[str] = set()
    attributes: set[str] = set()
    own_attributes: set[str] = set()
    bound: set[str] = set()
    declared: set[str] = set()
    called_names: set[str] = set()
    called_attributes: set[str] = set()
    called_own_attributes: set[str] = set()
    receiver = _receiver(node, kind)
    for child in ast.walk(node):
        if type(child) not in _REFERENCE_NODES:
            continue
        if isinstance(child, ast.Name):
            names.add(child.id)
            if isinstance(child.ctx, ast.Store):
                bound.add(child.id)
        elif isinstance(child, ast.Attribute):
            if isinstance(child.value, ast.Name) and child.value.id == receiver:
                own_attributes.add(child.attr)
            else:
                attributes.add(child.attr)
            if chain := _dotted(child):
                dotted.add(chain)
        elif isinstance(child, ast.arg):
            bound.add(child.arg)
        elif isinstance(child, ast.ExceptHandler) and child.name:
            bound.add(child.name)
        elif isinstance(child, ast.Global):
            declared.update(child.names)
        elif isinstance(child, ast.Call):
            function = child.func
            if isinstance(function, ast.Name):
                called_names.add(function.id)
            elif isinstance(function, ast.Attribute):
                if isinstance(function.value, ast.Name) and function.value.id == receiver:
                    called_own_attributes.add(function.attr)
                else:
                    called_attributes.add(function.attr)
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
        called_names=frozenset(called_names) if called_names else _NONE,
        called_attributes=frozenset(called_attributes) if called_attributes else _NONE,
        called_own_attributes=frozenset(called_own_attributes) if called_own_attributes else _NONE,
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
