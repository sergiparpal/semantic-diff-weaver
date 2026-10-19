"""Static Python AST extraction and behavior-bearing feature collection."""

from __future__ import annotations

import ast
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


# The comparisons the condition rule treats as a boundary. Membership and identity tests (`in`,
# `is`) never were, and a shift (`<<`, `>>`) is arithmetic that only looked like one in text.
BOUNDARY_COMPARISONS = (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)


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
    Whether the value compares is read from its operators, never from characters in its text.
    """
    words: list[str] = []
    compares = False
    for node in nodes:
        if node is None:
            continue
        for child in ast.walk(node):
            if isinstance(child, BOUNDARY_COMPARISONS):
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

    def _record_loop(self, label: str, lineno: int) -> None:
        self.loops.append((label, lineno))

    def visit_For(self, node: ast.For) -> None:
        self._record_loop(
            f"for {unparse_redacted(node.target, 100)} in {unparse_redacted(node.iter, 250)}",
            node.lineno,
        )
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._record_loop(
            f"async for {unparse_redacted(node.target, 100)} in {unparse_redacted(node.iter, 250)}",
            node.lineno,
        )
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self._record_loop(f"while {unparse_redacted(node.test, 250)}", node.lineno)
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


def snapshot_symbol(node: ast.AST, qualified_name: str, kind: str) -> SymbolSnapshot:
    body = list(getattr(node, "body", []))
    visitor = FeatureVisitor(node)
    visitor.visit(node)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        signature = function_signature(node)
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
    return SymbolSnapshot(
        qualified_name=qualified_name,
        kind=kind,
        start=getattr(node, "lineno", 1),
        end=getattr(node, "end_lineno", max(1, len(body))),
        signature=signature,
        default_map=defaults,
        decorators=decorators,
        fingerprint=body_fingerprint(body),
        features={name: tuple(getattr(visitor, name)) for name in limits.FEATURE_NAMES},
        statement_order=tuple(visitor.statement_order),
        node_inventory=node_inventory(body),
        probes={name: tuple(values) for name, values in visitor.probes.items()},
        assigned=tuple(visitor.assigned),
        docstring_only=not body_without_docstring(body),
        display=display,
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
    for name, kind, node in _symbol_nodes(tree.body):
        if len(symbols) >= effective_budget.max_symbols_per_file:
            raise AstResourceLimit("symbol budget exceeded")
        snapshot = snapshot_symbol(node, name, kind)
        if kind == "module":
            snapshot.start = 1
            snapshot.end = max(1, len(source.splitlines()))
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
    receiver = _receiver(node, kind)
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
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
