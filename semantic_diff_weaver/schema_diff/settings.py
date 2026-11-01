"""What changed between two revisions of a configuration file, key by key.

A file the repository names in `paths.config_files` is compared as configuration: its values are
flattened to their dotted key paths, `database.pool.max_size`, and each key added, removed, or
given another value is a delta named by its path. A list is one value as a whole, since its items
have no names to compare them by. Nothing a value names — a URL, a host, a file — is followed.
"""

from __future__ import annotations

from typing import Any

from ..ast_diff import StructuralDelta
from .deltas import ABSENT, Deltas
from .load import JsonValue

# Past this depth, a mapping is one value as a whole.
MAX_KEY_DEPTH = 32


def _leaves(value: Any, prefix: str, depth: int, leaves: dict[str, Any]) -> None:
    """Each value the document sets, by its dotted key path; a document whose root is no
    mapping is one value, `<root>`."""
    if isinstance(value, dict) and value and depth < MAX_KEY_DEPTH:
        for key, item in value.items():
            _leaves(item, f"{prefix}.{key}" if prefix else key, depth + 1, leaves)
    else:
        leaves[prefix or "<root>"] = value


def value_type(value: Any) -> str | None:
    """What kind of value a key holds, for the classification: `boolean`, `number`, `string`,
    `list`, `mapping`, or `null`; None for a key that is not there."""
    if value is ABSENT:
        return None
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    return "null" if value is None else "mapping"


def compare_settings(
    path: str, old: JsonValue | None, new: JsonValue | None
) -> tuple[list[StructuralDelta], bool]:
    """The deltas between two revisions of a configuration file, and whether their budget ran
    out. A side that is None — absent, or empty — sets nothing."""
    deltas = Deltas(path)
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    if old is not None:
        _leaves(old, "", 0, before)
    if new is not None:
        _leaves(new, "", 0, after)
    for key in sorted(before.keys() | after.keys()):
        old_value, new_value = before.get(key, ABSENT), after.get(key, ABSENT)
        old_type, new_type = value_type(old_value), value_type(new_value)
        # `True == 1` in Python, but a switch turned into a count is a change.
        if old_type == new_type and old_value == new_value:
            continue
        if old_value is ABSENT:
            kind = "config_key_added"
        elif new_value is ABSENT:
            kind = "config_key_removed"
        else:
            kind = "config_value_change"
        deltas.add(
            key,
            kind,
            old_value,
            new_value,
            location=key,
            context="configuration",
            old_type=old_type,
            new_type=new_type,
        )
    return deltas.items, deltas.truncated
