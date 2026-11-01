"""Which findings in Python code calls connect, and the ones a test reaches the rest through.

A test of `checkout` runs the `parse_amount` it calls, so a change to both is one change to test
through `checkout`. The call graph says which changed symbols reach which, through any code
between them, changed or not. Two changed symbols that only share a caller are not connected:
every symbol of a program shares `main`.
"""

from __future__ import annotations

from collections.abc import Sequence

from .call_graph import CallGraph, Node
from .models import BehaviorChange, ChangeGroup


def group_changes(
    behaviors: Sequence[BehaviorChange], graph: CallGraph | None
) -> list[ChangeGroup]:
    """The groups of findings calls connect, each led by its entry points, in the findings' order.

    A finding is grouped by its first evidence's symbol when the graph indexes it: a module's
    top-level code, which nothing calls, a file that did not parse, an API schema, and a
    configuration key are not. The findings of one symbol go together, and a group has two
    symbols at least.
    """
    if graph is None:
        return []
    position = {behavior.id: index for index, behavior in enumerate(behaviors)}
    findings: dict[Node, list[str]] = {}
    for behavior in behaviors:
        evidence = behavior.evidence[0]
        node = (evidence.path, evidence.symbol or "<module>")
        # A synthetic symbol, `<module>` or `<unparsed>`, is no code a call reaches.
        if not node[1].startswith("<") and graph.defines(node):
            findings.setdefault(node, []).append(behavior.id)
    nodes = list(findings)
    reaches = {
        node: {other for other in nodes if other != node and node in graph.callers(other)}
        for node in nodes
    }
    reached_by = {node: {other for other in nodes if node in reaches[other]} for node in nodes}
    # An entry point reaches another finding's code, and reaches back whatever reaches it.
    entries = [node for node in nodes if reaches[node] and reached_by[node] <= reaches[node]]
    groups: list[ChangeGroup] = []
    placed: set[Node] = set()
    for node in entries:
        if node in placed:
            continue
        # Entry points that reach each other lead one group together.
        leading = [other for other in entries if other == node or other in reached_by[node]]
        placed.update(leading)
        members = {*leading}.union(*(reaches[entry] for entry in leading))
        groups.append(
            ChangeGroup(
                id=f"cg-{len(groups) + 1:03d}",
                behavior_change_ids=sorted(
                    (item for member in members for item in findings[member]),
                    key=position.__getitem__,
                ),
                entry_points=sorted(
                    (item for entry in leading for item in findings[entry]),
                    key=position.__getitem__,
                ),
            )
        )
    return groups
