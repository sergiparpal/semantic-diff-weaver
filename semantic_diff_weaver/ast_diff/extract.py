"""Static Python AST extraction and behavior-bearing feature collection."""

from __future__ import annotations

import ast
import hashlib
from collections import Counter

from ..path_policy import redact_text
from . import limits
from .limits import AstBudget
from .types import SymbolSnapshot


class AstResourceLimit(ValueError):
    """Raised when untrusted source exceeds an immutable structural safety budget."""


def unparse_redacted(node: ast.AST | None, limit: int = 500) -> str:
    if node is None:
        return "None"
    try:
        return redact_text(ast.unparse(node), max_chars=limit)
    except (AttributeError, RecursionError, ValueError):
        return type(node).__name__


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
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        self.conditions.append((unparse_redacted(node.test), node.lineno))
        self.statement_order.append(f"if:{unparse_redacted(node.test, 120)}")
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.conditions.append((unparse_redacted(node.test), node.lineno))
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        self.conditions.append((unparse_redacted(node.test), node.lineno))
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
        self.statement_order.append(f"call:{name}")
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        self.returns.append((unparse_redacted(node.value), node.lineno))
        self.statement_order.append(f"return:{unparse_redacted(node.value, 120)}")
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        targets = ", ".join(unparse_redacted(item, 100) for item in node.targets)
        self.assignments.append((f"{targets} = {unparse_redacted(node.value, 250)}", node.lineno))
        self.statement_order.append(f"assign:{targets}")
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        value = f"{unparse_redacted(node.target, 100)} = {unparse_redacted(node.value, 250)}"
        self.assignments.append((value, node.lineno))
        self.statement_order.append(f"assign:{unparse_redacted(node.target, 100)}")
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        value = (
            f"{unparse_redacted(node.target, 100)} {type(node.op).__name__}= "
            f"{unparse_redacted(node.value, 250)}"
        )
        self.assignments.append((value, node.lineno))
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
        self.conditions.append((unparse_redacted(node.test), node.lineno))
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


def snapshot_symbol(node: ast.AST, qualified_name: str, kind: str) -> SymbolSnapshot:
    body = list(getattr(node, "body", []))
    visitor = FeatureVisitor(node)
    visitor.visit(node)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        signature = function_signature(node)
        defaults = default_map(node)
        decorators = tuple(decorator_name(item) for item in node.decorator_list)
    elif isinstance(node, ast.ClassDef):
        bases = ", ".join(unparse_redacted(item, 120) for item in node.bases)
        signature = f"{type_parameters(node)}bases({bases})"
        defaults = {}
        decorators = tuple(decorator_name(item) for item in node.decorator_list)
    else:
        signature = "module"
        defaults = {}
        decorators = ()
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
    )


def extract_symbols(source: str, budget: AstBudget | None = None) -> list[SymbolSnapshot]:
    effective_budget = budget or AstBudget.default()
    tree = ast.parse(source, type_comments=True)
    _validate_ast_budget(tree, effective_budget)
    symbols: list[SymbolSnapshot] = []
    max_symbols = effective_budget.max_symbols_per_file

    def record(snapshot: SymbolSnapshot) -> None:
        if len(symbols) >= max_symbols:
            raise AstResourceLimit("symbol budget exceeded")
        symbols.append(snapshot)

    def walk(body: list[ast.stmt], prefix: str, *, parent_is_class: bool = False) -> None:
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
                        if not isinstance(
                            item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                        )
                    ],
                    decorator_list=statement.decorator_list,
                )
                ast.copy_location(class_shell, statement)
                class_shell.end_lineno = statement.end_lineno
                # The shell exists only to drop nested definitions from the body fingerprint,
                # so every other behavior-bearing field must survive the rebuild.
                if hasattr(statement, TYPE_PARAMS_FIELD):
                    setattr(class_shell, TYPE_PARAMS_FIELD, getattr(statement, TYPE_PARAMS_FIELD))
                record(snapshot_symbol(class_shell, name, "class"))
                walk(statement.body, name, parent_is_class=True)
            elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}.{statement.name}" if prefix else statement.name
                if parent_is_class:
                    kind = (
                        "async_method" if isinstance(statement, ast.AsyncFunctionDef) else "method"
                    )
                else:
                    kind = (
                        "async_function"
                        if isinstance(statement, ast.AsyncFunctionDef)
                        else "function"
                    )
                record(snapshot_symbol(statement, name, kind))
                nested: list[ast.stmt] = [
                    item
                    for item in statement.body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                ]
                walk(nested, name)
            else:
                module_body.append(statement)
        if not prefix:
            module = ast.Module(body=module_body, type_ignores=[])
            snapshot = snapshot_symbol(module, "<module>", "module")
            snapshot.start = 1
            snapshot.end = max(1, len(source.splitlines()))
            record(snapshot)

    walk(tree.body, "")
    return symbols
