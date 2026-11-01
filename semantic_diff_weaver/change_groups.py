"""Which findings in Python code calls connect, and the ones a test reaches the rest through.

A test of `checkout` runs the `parse_amount` it calls, so a change to both is one change to test
through `checkout`. The call graph says which changed symbols reach which, through any code
between them, changed or not. Two changed symbols that only share a caller are not connected:
every symbol of a program shares `main`.

A change to a module's top-level code that assigns a variable, `TIMEOUT_SECONDS = 60`, is reached
by the code that reads the variable, in its own module or through an import, and by whatever
reaches that code: a test of `fetch` runs with the timeout it reads.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence

from .call_graph import CallGraph, Node
from .models import BehaviorChange, ChangedVariables, ChangeGroup

Variable = tuple[str, str]
"""A module-level variable: its module's path and its name."""


def group_changes(
    behaviors: Sequence[BehaviorChange],
    graph: CallGraph | None,
    assigned: Mapping[str, frozenset[str]] | None = None,
) -> list[ChangeGroup]:
    """The groups of connected findings, each led by its entry points, in the findings' order.

    A finding is grouped by its first evidence's symbol when the graph indexes it: a file that did
    not parse, an API schema, and a configuration key are not. The findings of one symbol go
    together, and a group has two symbols at least. A finding in a module's top-level code is
    grouped through the variables `assigned` says its changed assignments write, by finding ID: it
    joins the group of each entry point that reaches code reading one of them, and leads none,
    since nothing calls top-level code.
    """
    if graph is None:
        return []
    assigned = assigned or {}
    position = {behavior.id: index for index, behavior in enumerate(behaviors)}
    findings: dict[Node, list[str]] = {}
    writers: dict[Variable, list[str]] = {}
    for behavior in behaviors:
        evidence = behavior.evidence[0]
        node = (evidence.path, evidence.symbol or "<module>")
        if node[1] == "<module>":
            for name in sorted(assigned.get(behavior.id, ())):
                writers.setdefault((evidence.path, name), []).append(behavior.id)
        # A synthetic symbol, `<unparsed>`, is no code a call reaches.
        elif not node[1].startswith("<") and graph.defines(node):
            findings.setdefault(node, []).append(behavior.id)
    nodes = list(findings)
    reaches = {
        node: {other for other in nodes if other != node and node in graph.callers(other)}
        for node in nodes
    }
    reached_by = {node: {other for other in nodes if node in reaches[other]} for node in nodes}
    # The variables each changed symbol reaches code reading, its own code among it.
    reads: dict[Node, set[Variable]] = {node: set() for node in nodes}
    for variable in writers:
        for node in graph.reading(*variable) & findings.keys():
            reads[node].add(variable)
    # An entry point reaches another finding's code, and reaches back whatever reaches it.
    entries = [
        node
        for node in nodes
        if (reaches[node] or reads[node]) and reached_by[node] <= reaches[node]
    ]
    groups: list[ChangeGroup] = []
    placed: set[Node] = set()
    for node in entries:
        if node in placed:
            continue
        # Entry points that reach each other lead one group together.
        leading = [other for other in entries if other == node or other in reached_by[node]]
        placed.update(leading)
        members = {*leading}.union(*(reaches[entry] for entry in leading))
        written: dict[str, set[str]] = defaultdict(set)
        for variable in set().union(*(reads[entry] for entry in leading)):
            for item in writers[variable]:
                written[item].add(variable[1])
        groups.append(
            ChangeGroup(
                id=f"cg-{len(groups) + 1:03d}",
                behavior_change_ids=sorted(
                    {*(item for member in members for item in findings[member]), *written},
                    key=position.__getitem__,
                ),
                entry_points=sorted(
                    (item for entry in leading for item in findings[entry]),
                    key=position.__getitem__,
                ),
                variables=[
                    ChangedVariables(behavior_change_id=item, names=sorted(written[item]))
                    for item in sorted(written, key=position.__getitem__)
                ],
            )
        )
    return groups
