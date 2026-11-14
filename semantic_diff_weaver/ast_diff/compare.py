"""Emit structural deltas between matched symbol snapshots."""

from __future__ import annotations

import builtins
from collections import Counter
from collections.abc import Callable, Hashable, Iterable, Sequence
from pathlib import PurePosixPath
from typing import Any, TypeVar

from ..models import LineRange
from ..path_policy import redact_text
from ..source import SourceHunk
from .limits import (
    FEATURE_DELTA_KINDS,
    MAX_CHANGED_VALUE_CHARS,
    MAX_CHANGED_VALUES,
    MAX_DELTA_TEXT_CHARS,
    MAX_PROBE_CHARS_PER_SIDE,
)
from .match import symbol_similarity
from .types import CategoryProbe, StructuralDelta, SymbolSnapshot

# What a module-level name shadows when the module binds it.
BUILTIN_NAMES = frozenset(dir(builtins))

ORDER_SENSITIVE_KINDS = {
    "call_order_change",
    "comparison_change",
    "condition_change",
    "condition_order_change",
    "raise_change",
    "return_change",
    "assignment_change",
    "loop_change",
}


def is_reordering(old_order: Sequence[str], new_order: Sequence[str]) -> bool:
    """Report whether two statement sequences are a permutation of one another.

    ``statement_order`` entries embed unparsed expression content, not just sequence
    identity, so plain inequality also fires on a pure content edit. Ordering means the
    same statements occur in a different sequence, which is exactly a permutation — the
    same test ``_append_call_delta`` already applies to call names.
    """
    return old_order != new_order and sorted(old_order) == sorted(new_order)


def ranges_overlap(start: int, end: int, hunk_start: int, hunk_count: int) -> bool:
    if hunk_count == 0:
        return start <= hunk_start <= end + 1
    return start <= hunk_start + hunk_count - 1 and hunk_start <= end


def matching_hunk(
    old: SymbolSnapshot | None,
    new: SymbolSnapshot | None,
    *,
    old_hunks: Sequence[SourceHunk],
    new_hunks: Sequence[SourceHunk],
    old_path: str,
    new_path: str,
) -> str | None:
    for hunk in new_hunks:
        if new and ranges_overlap(new.start, new.end, hunk.new_start, hunk.new_count):
            return f"{new_path}#{hunk.id}"
    for hunk in old_hunks:
        if old and ranges_overlap(old.start, old.end, hunk.old_start, hunk.old_count):
            return f"{old_path}#{hunk.id}"
    return None


def join_feature_values(values: Iterable[tuple[str, int]]) -> str | None:
    material = [text for text, _ in values]
    return redact_text("; ".join(material), max_chars=MAX_DELTA_TEXT_CHARS) if material else None


def _values_in_display_window(
    snapshot: SymbolSnapshot, feature: str
) -> list[tuple[str, CategoryProbe]]:
    """The values a delta's display text shows, each with its probe.

    The display text joins every value with "; " and stops at `MAX_DELTA_TEXT_CHARS`, so a long
    symbol shows — and has always been classified by — only its first values. Covering the same
    values keeps what a probe changes about classification to prose literals and nothing else;
    a denser probe that reached further was finding new keywords in calls the text never showed.
    """
    kept: list[tuple[str, CategoryProbe]] = []
    offset = 0
    pairs = zip(snapshot.features[feature], snapshot.probes[feature], strict=True)
    for (text, _), probe in pairs:
        if offset >= MAX_DELTA_TEXT_CHARS:
            break
        kept.append((text, probe))
        offset += len(text) + len("; ")
    return kept


def _changed_probes(
    shown: list[tuple[str, CategoryProbe]], other: SymbolSnapshot, feature: str
) -> list[CategoryProbe]:
    """The probes of the shown values the other side does not hold as many times."""
    remaining = Counter(feature_texts(other, feature))
    changed: list[CategoryProbe] = []
    for text, probe in shown:
        if remaining[text]:
            remaining[text] -= 1
        else:
            changed.append(probe)
    return changed


def join_probes(old: SymbolSnapshot, new: SymbolSnapshot, feature: str) -> CategoryProbe | None:
    """Both sides' category probes for `feature`, or None when either side recorded none.

    None sends the category rules back to the display text, which is what a feature without
    probes — or a snapshot built by hand — has always been classified by. Each side's words are
    bounded on their own, so a long old side can never crowd out the new one.

    Both the words and whether the delta compares a boundary are read from the values it
    changed, never from every value it shows: an unchanged `len(items) > 3` made every condition
    changed beside it, a `parent is None` among them, a boundary, and an unchanged
    `order.status = ...` made a changed `count = ...` a state transition.
    """
    if feature not in old.probes or feature not in new.probes:
        return None
    old_shown, new_shown = (_values_in_display_window(snapshot, feature) for snapshot in (old, new))
    sides = (_changed_probes(old_shown, new, feature), _changed_probes(new_shown, old, feature))
    return CategoryProbe(
        words=" ".join(
            " ".join(probe.words for probe in side)[:MAX_PROBE_CHARS_PER_SIDE] for side in sides
        ),
        compares=any(probe.compares for side in sides for probe in side),
    )


def line_ranges(
    old: SymbolSnapshot | None, new: SymbolSnapshot | None
) -> tuple[LineRange | None, LineRange | None]:
    old_range = LineRange(start=old.start, end=old.end) if old else None
    new_range = LineRange(start=new.start, end=new.end) if new else None
    return old_range, new_range


def make_structural_delta(
    path: str,
    old: SymbolSnapshot | None,
    new: SymbolSnapshot | None,
    hunk_id: str | None,
    kind: str,
    old_value: str | None,
    new_value: str | None,
    *,
    probe: CategoryProbe | None = None,
    **metadata: Any,
) -> StructuralDelta:
    old_lines, new_lines = line_ranges(old, new)
    if new is not None:
        symbol_name = new.qualified_name
    elif old is not None:
        symbol_name = old.qualified_name
    else:
        symbol_name = "<unknown>"
    return StructuralDelta(
        path=path,
        symbol=symbol_name,
        kind=kind,
        old=redact_text(old_value, max_chars=MAX_DELTA_TEXT_CHARS) if old_value else None,
        new=redact_text(new_value, max_chars=MAX_DELTA_TEXT_CHARS) if new_value else None,
        old_lines=old_lines,
        new_lines=new_lines,
        hunk_id=hunk_id,
        metadata=metadata,
        probe=probe,
        symbol_kind=new.kind if new is not None else old.kind if old is not None else None,
    )


def feature_texts(snapshot: SymbolSnapshot, feature: str) -> list[str]:
    return [text for text, _ in snapshot.features[feature]]


def suppress_redundant_deltas(deltas: list[StructuralDelta]) -> list[StructuralDelta]:
    """Drop overlapping feature deltas that are explained by a more specific kind.

    Raise changes already imply the underlying call site moved. Pure call-shaped returns
    are also redundant once a call_change is present, and returns of a changed comparison once
    the comparison_change is (see `_maybe_drop_return_when_comparison_explained`).
    """
    result = list(deltas)
    result_kinds = {item.kind for item in result}
    if "raise_change" in result_kinds and "call_change" in result_kinds:
        result = [item for item in result if item.kind != "call_change"]
    return result


def _changed_sides(
    old_values: Iterable[str], new_values: Iterable[str]
) -> tuple[list[str], list[str]]:
    """The values the old side holds more times than the new one, and those the new side does."""
    old_side, new_side = Counter(old_values), Counter(new_values)
    return list((old_side - new_side).elements()), list((new_side - old_side).elements())


def _changed_values(old: SymbolSnapshot, new: SymbolSnapshot, feature: str) -> list[str]:
    """The values of `feature` that one side holds more times than the other, from both sides."""
    removed, added = _changed_sides(feature_texts(old, feature), feature_texts(new, feature))
    return [*removed, *added]


def changed_value_metadata(removed: Sequence[str], added: Sequence[str]) -> dict[str, Any]:
    """Which of the values a delta shows it changed, for its metadata.

    The display texts join every value of each side, and stop at `MAX_DELTA_TEXT_CHARS`, so they
    cannot say which values differ. The obligations of the finding quote these instead: the
    condition a branch now tests, the call whose arguments changed. Each value is redacted again,
    as the display text is.
    """
    return {
        "removed": [
            redact_text(value, max_chars=MAX_CHANGED_VALUE_CHARS)
            for value in removed[:MAX_CHANGED_VALUES]
        ],
        "added": [
            redact_text(value, max_chars=MAX_CHANGED_VALUE_CHARS)
            for value in added[:MAX_CHANGED_VALUES]
        ],
        "removed_total": len(removed),
        "added_total": len(added),
    }


def _maybe_drop_return_when_comparison_explained(
    result: list[StructuralDelta], old: SymbolSnapshot, new: SymbolSnapshot
) -> list[StructuralDelta]:
    """Drop a return change whose changed values each show a changed comparison.

    `return x < 5` to `return x <= 5` changes a comparison and a return value, and the comparison
    says what changed. Any changed comparison used to drop its symbol's return change, so when
    `Argument.get_help_record` lost both an `if self.help is None` and the `return None` under
    it, the one change a caller sees went unreported.
    """
    result_kinds = {item.kind for item in result}
    if "return_change" not in result_kinds or "comparison_change" not in result_kinds:
        return result
    # A comparison's value is its expression followed by its operators, `x < 5 [Lt]`.
    comparisons = [text.rsplit(" [", 1)[0] for text in _changed_values(old, new, "comparisons")]
    returns = _changed_values(old, new, "returns")
    if returns and all(any(item in value for item in comparisons) for value in returns):
        return [item for item in result if item.kind != "return_change"]
    return result


# Builtins that compute in-process and reach nothing outside it: a changed `len()` or
# `isinstance()` is the symbol's own logic, not an interaction with a dependency. `open`, `print`,
# `input`, `exec`, `eval`, `compile`, `__import__`, and `breakpoint` reach out, and are left out.
PURE_BUILTINS = frozenset(
    {
        "abs", "all", "any", "ascii", "bin", "bool", "bytearray", "bytes", "callable", "chr",
        "complex", "delattr", "dict", "dir", "divmod", "enumerate", "filter", "float", "format",
        "frozenset", "getattr", "hasattr", "hash", "hex", "id", "int", "isinstance", "issubclass",
        "iter", "len", "list", "map", "max", "min", "next", "object", "oct", "ord", "pow", "range",
        "repr", "reversed", "round", "set", "setattr", "slice", "sorted", "str", "sum", "super",
        "tuple", "type", "vars", "zip",
    }
)  # fmt: skip


def own_call_names(*revisions: Sequence[SymbolSnapshot]) -> frozenset[str]:
    """The names a call reaches a module's own code by, in either revision.

    A function or class the module defines is called by its name, and a method one of its classes
    defines by `self.<method>`, `cls.<method>`, or `<Class>.<method>`. A method inherited from
    another module's class is not among them, and neither is an attribute that holds a callable:
    `self.callback()` calls whatever was stored there.
    """
    names: set[str] = set()
    for symbols in revisions:
        for item in symbols:
            if item.kind in {"method", "async_method"}:
                method = item.qualified_name.rsplit(".", 1)[1]
                names.update((item.qualified_name, f"self.{method}", f"cls.{method}"))
            elif item.kind != "module":
                names.add(item.qualified_name.rsplit(".", 1)[-1])
    return frozenset(names)


def root_package(path: str) -> str | None:
    """The top-level package a file belongs to, read from its path without a `src` or `lib`
    directory: `requests` for `src/requests/models.py`, and none for a file outside a directory.
    """
    parts = PurePosixPath(path).parts
    if parts and parts[0] in {"src", "lib"}:
        parts = parts[1:]
    return parts[0] if len(parts) > 1 else None


def first_party_names(path: str, *revisions: Sequence[SymbolSnapshot]) -> frozenset[str]:
    """The names a module's imports bind to code of its own package, in either revision.

    An import is of the package's own code when it is relative, `from . import _types`, or names
    the top-level package the file belongs to, `from black.nodes import is_yield` in
    `src/black/linegen.py`. A name some import binds to other code is not one of them.
    """
    root = root_package(path)
    targets: dict[str, set[str]] = {}
    for symbols in revisions:
        for item in symbols:
            for name, imported in item.imported.items():
                targets.setdefault(name, set()).update(imported)
    return frozenset(
        name
        for name, imported in targets.items()
        if all(
            target.startswith(".") or (root is not None and target.split(".", 1)[0] == root)
            for target in imported
        )
    )


def is_internal_call(
    name: str, own_calls: frozenset[str], first_party: frozenset[str] = frozenset()
) -> bool:
    """Whether a call reaches only the caller's own code: its module's, a pure builtin, or what
    an import of its own package binds.

    `self.client.fetch` calls whatever `self` holds, which may be anything, and a name the module
    imports from another package is that package's code.
    """
    return name in own_calls or name in PURE_BUILTINS or name.split(".", 1)[0] in first_party


def _append_call_delta(
    result: list[StructuralDelta],
    path: str,
    old: SymbolSnapshot,
    new: SymbolSnapshot,
    hunk_id: str | None,
    own_calls: frozenset[str],
    first_party: frozenset[str],
) -> None:
    old_names = feature_texts(old, "calls")
    new_names = feature_texts(new, "calls")
    if old_names == new_names:
        return
    kind = "call_order_change" if sorted(old_names) == sorted(new_names) else "call_change"
    removed, added = _changed_sides(old_names, new_names)
    changed = sorted({item.split("(", 1)[0] for item in (*removed, *added)})
    flow = {"reaches_output": True} if changes_output(old, new, "calls") else {}
    result.append(
        make_structural_delta(
            path,
            old,
            new,
            hunk_id,
            kind,
            "; ".join(old_names),
            "; ".join(new_names),
            probe=join_probes(old, new, "calls"),
            old_calls=[item.split("(", 1)[0] for item in old_names],
            new_calls=[item.split("(", 1)[0] for item in new_names],
            # Every call that changed reaches only this package's own code: the call rule tells
            # it from a dependency by this alone.
            internal_calls=bool(changed)
            and all(is_internal_call(name, own_calls, first_party) for name in changed),
            **changed_value_metadata(removed, added),
            **flow,
        )
    )


def _maybe_drop_return_when_call_explained(
    result: list[StructuralDelta], old: SymbolSnapshot, new: SymbolSnapshot
) -> list[StructuralDelta]:
    result_kinds = {item.kind for item in result}
    if "return_change" not in result_kinds or "call_change" not in result_kinds:
        return result
    return_values = [
        text.lstrip() for text in (*feature_texts(old, "returns"), *feature_texts(new, "returns"))
    ]
    if return_values and all(
        "(" in value and not value.startswith(("{", "[", "(")) for value in return_values
    ):
        return [item for item in result if item.kind != "return_change"]
    return result


_Written = TypeVar("_Written", bound=Hashable)


def _changed_writes(
    old: SymbolSnapshot,
    new: SymbolSnapshot,
    record: Callable[[SymbolSnapshot], tuple[_Written, ...]],
) -> list[_Written]:
    """What a per-assignment record, such as `assigned`, holds for each assignment that differs
    between the two sides.

    A module's assignment delta shows every module-level assignment, so only this tells which
    changed: `CODE_ROOTS` when that tuple alone gained an entry. A snapshot built without the
    record holds none.
    """
    sides: list[Counter[tuple[str, _Written]]] = []
    for snapshot in (old, new):
        values = snapshot.features["assignments"]
        written = record(snapshot)
        if len(written) != len(values):
            return []
        sides.append(Counter(zip((text for text, _ in values), written, strict=True)))
    old_side, new_side = sides
    changed = (old_side - new_side) + (new_side - old_side)
    return [value for _, value in changed]


def _changed_names(
    old: SymbolSnapshot,
    new: SymbolSnapshot,
    record: Callable[[SymbolSnapshot], tuple[frozenset[str], ...]],
) -> list[str]:
    return sorted({name for names in _changed_writes(old, new, record) for name in names})


def _changed_assignment_targets(old: SymbolSnapshot, new: SymbolSnapshot) -> list[str]:
    """The variables written by the assignments that differ between the two sides."""
    return _changed_names(old, new, lambda snapshot: snapshot.assigned)


def _assignment_metadata(old: SymbolSnapshot, new: SymbolSnapshot) -> dict[str, Any]:
    """What the changed assignments write, for the category rules: the variables, the last name
    each target writes, and whether any sets an attribute or an item of `self` or `cls`."""
    metadata: dict[str, Any] = {
        "assigned_names": _changed_assignment_targets(old, new),
        "stored_names": _changed_names(old, new, lambda snapshot: snapshot.stored),
    }
    if any(_changed_writes(old, new, lambda snapshot: snapshot.on_instance)):
        metadata["writes_instance"] = True
    return metadata


def _changed_lines(snapshot: SymbolSnapshot, other: SymbolSnapshot, feature: str) -> list[int]:
    """The lines of the values of `feature` that `snapshot` holds more times than `other`."""
    remaining = Counter(feature_texts(other, feature))
    lines: list[int] = []
    for text, line in snapshot.features[feature]:
        if remaining[text]:
            remaining[text] -= 1
        else:
            lines.append(line)
    return lines


def changes_output(old: SymbolSnapshot, new: SymbolSnapshot, feature: str) -> bool:
    """Whether a value of `feature` that one side holds and the other does not sits on a line
    that feeds what that side's function returns or yields (see `extract.output_lines`)."""
    return any(line in old.output_lines for line in _changed_lines(old, new, feature)) or any(
        line in new.output_lines for line in _changed_lines(new, old, feature)
    )


def _append_feature_deltas(
    result: list[StructuralDelta],
    path: str,
    old: SymbolSnapshot,
    new: SymbolSnapshot,
    hunk_id: str | None,
) -> None:
    for feature, kind in FEATURE_DELTA_KINDS:
        old_values = feature_texts(old, feature)
        new_values = feature_texts(new, feature)
        if old_values == new_values:
            continue
        effective_kind = (
            "condition_order_change"
            if feature == "conditions" and sorted(old_values) == sorted(new_values)
            else kind
        )
        metadata = changed_value_metadata(*_changed_sides(old_values, new_values))
        if feature == "assignments":
            metadata.update(_assignment_metadata(old, new))
        if changes_output(old, new, feature):
            metadata["reaches_output"] = True
        result.append(
            make_structural_delta(
                path,
                old,
                new,
                hunk_id,
                effective_kind,
                join_feature_values(old.features[feature]),
                join_feature_values(new.features[feature]),
                probe=join_probes(old, new, feature),
                **metadata,
            )
        )


def is_reworded(old: SymbolSnapshot, new: SymbolSnapshot) -> bool:
    """Whether two bodies differ in nothing but the wording of the exception messages they raise.

    httpx's "an sync iterator" became "a sync iterator": the same exception, raised from the same
    place, with the same values in its message. A message that now interpolates a value it did
    not, as `f"Unknown scheme {url.scheme!r}"` for "Unknown scheme", is a change of what it says.
    """
    return (
        old.fingerprint != new.fingerprint
        and old.fingerprint_without_messages is not None
        and old.fingerprint_without_messages == new.fingerprint_without_messages
    )


def is_redescribed(old: SymbolSnapshot, new: SymbolSnapshot) -> bool:
    """Whether two bodies differ in nothing but the `description` texts of the schemas they hold.

    A tool's description that comes to say what else its call clears, or to ask for a yes before
    it runs, changes what an agent given the tool tells the user and when it calls it. The schema
    validates what it validated before, and the tool's handler is handed the same arguments.
    """
    return (
        old.fingerprint != new.fingerprint
        and old.fingerprint_without_descriptions is not None
        and old.fingerprint_without_descriptions == new.fingerprint_without_descriptions
    )


def _changed_raises(snapshot: SymbolSnapshot, other: SymbolSnapshot) -> str | None:
    """The raises of `snapshot` that `other` does not hold as many times, as a delta shows them."""
    changed = Counter(feature_texts(snapshot, "raises")) - Counter(feature_texts(other, "raises"))
    return "; ".join(changed.elements()) or None


def only_imports_changed(path: str, old: SymbolSnapshot, new: SymbolSnapshot) -> bool:
    """Whether a module's own code differs only in imports, each in step with the code using it.

    Every other statement must be the same. A name it newly imports must be read in the new file,
    and, where the old file read it too, as a builtin, a global, or an import, have been imported
    there from the same place, as an import moved out of an `if TYPE_CHECKING:` block was: a
    local variable of the same name is not a read. A name it stops importing must be mentioned
    nowhere in the new file, and a package's `__init__.py` must keep each public one, which
    importers of the package may read. A name imported from somewhere else, `json` from
    `orjson`, changes what the code calls.
    """
    if (
        old.fingerprint_without_imports is None
        or old.fingerprint_without_imports != new.fingerprint_without_imports
    ):
        return False
    package = PurePosixPath(path).name == "__init__.py"
    for name in old.imports.keys() | new.imports.keys():
        before, after = old.imports.get(name), new.imports.get(name)
        if before == after:
            continue
        if before is not None and after is not None:
            return False
        if after is not None:
            if name not in new.reads:
                # Imported for what loading it does, or to export it.
                return False
            if name in old.reads and old.imported.get(name) != frozenset(after):
                return False
        elif name in new.names or (package and not name.startswith("_")):
            return False
    return True


def only_parameter_typing_changed(old: SymbolSnapshot, new: SymbolSnapshot) -> bool:
    """Whether a callable differs only in its parameters' annotations, which nothing reads.

    Callers pass the same arguments under the same name, the body is the same once its docstring
    is set aside, the decorators, type parameters, return annotation, and type comment are the
    same, nothing on either side reads the annotations (see `extract.annotations_unread`), and
    each annotation that changed runs nothing on either side (see `extract.parameter_typing`). A
    changed return annotation is a declared result, an output contract.
    """
    if not (
        old.qualified_name == new.qualified_name
        and old.parameters is not None
        and old.parameters == new.parameters
        and old.signature != new.signature
        and old.fingerprint == new.fingerprint
        and old.decorators == new.decorators
        and old.typing_beyond_parameters == new.typing_beyond_parameters
        and old.annotations_unread
        and new.annotations_unread
    ):
        return False
    before = {name: (digest, inert) for name, digest, inert in old.parameter_typing}
    after = {name: (digest, inert) for name, digest, inert in new.parameter_typing}
    unannotated = ("", True)
    for name in before.keys() | after.keys():
        old_digest, old_inert = before.get(name, unannotated)
        new_digest, new_inert = after.get(name, unannotated)
        if old_digest != new_digest and not (old_inert and new_inert):
            return False
    return True


def only_typing_changed(old: SymbolSnapshot, new: SymbolSnapshot) -> bool:
    """Whether a module's or a plain class's own code differs only in what a type checker reads.

    Its statements are the same once annotations that run nothing are set aside, and a module's
    type variables that only annotations read (see `extract.without_typing`). A type variable may
    be added or redefined but not dropped, since another module may import it, and a renamed class
    is renamed for whoever names it.
    """
    return (
        old.qualified_name == new.qualified_name
        and old.fingerprint != new.fingerprint
        and old.signature == new.signature
        and old.decorators == new.decorators
        and old.fingerprint_without_typing is not None
        and old.fingerprint_without_typing == new.fingerprint_without_typing
        and old.type_variables <= new.type_variables
    )


def only_version_changed(old: SymbolSnapshot, new: SymbolSnapshot) -> bool:
    """Whether a module's own code differs only in the text of its `__version__` string."""
    return (
        old.fingerprint != new.fingerprint
        and old.fingerprint_without_version is not None
        and old.fingerprint_without_version == new.fingerprint_without_version
    )


def only_constants_changed(old: SymbolSnapshot, new: SymbolSnapshot) -> bool:
    """Whether a module's own code differs only in constants added to it, or private ones removed
    from it (see `extract.module_constants`).

    What reads them is a symbol of its own, and carries any change. A constant whose value
    changed, or a public one removed, which code importing the module may read, is a change of
    the module itself. So is a name added that the old file mentioned, or that names a builtin:
    code that has not changed read something else by it, and now reads the constant. And so is
    a private name removed that the new file still mentions: the code reading it now fails.
    """
    if (
        old.fingerprint == new.fingerprint
        or old.fingerprint_without_constants is None
        or new.fingerprint_without_constants is None
        or old.fingerprint_without_constants != new.fingerprint_without_constants
    ):
        return False
    shared = old.constants.keys() & new.constants.keys()
    if any(old.constants[name] != new.constants[name] for name in shared):
        return False
    added = new.constants.keys() - old.constants.keys()
    removed = old.constants.keys() - new.constants.keys()
    return (
        all(name.startswith("_") for name in removed)
        and not added & (old.names | BUILTIN_NAMES)
        and not removed & new.names
    )


def _refactor_without_behavior(
    path: str, old: SymbolSnapshot, new: SymbolSnapshot, hunk_id: str | None
) -> StructuralDelta | None:
    """The one delta of a symbol whose change only a type checker or a release reads, or that
    only adds constants or removes private ones, or None."""
    if only_parameter_typing_changed(old, new):
        return make_structural_delta(
            path,
            old,
            new,
            hunk_id,
            "structural_refactor",
            old.signature,
            new.signature,
            materiality=0.0,
            parameter_annotations=True,
        )
    if only_typing_changed(old, new):
        return make_structural_delta(
            path,
            old,
            new,
            hunk_id,
            "structural_refactor",
            old.qualified_name,
            new.qualified_name,
            materiality=0.0,
            typing_only=True,
        )
    if only_version_changed(old, new):
        return make_structural_delta(
            path,
            old,
            new,
            hunk_id,
            "structural_refactor",
            f"__version__ = {old.version!r}",
            f"__version__ = {new.version!r}",
            materiality=0.0,
            version_only=True,
        )
    if only_constants_changed(old, new):
        return make_structural_delta(
            path,
            old,
            new,
            hunk_id,
            "structural_refactor",
            ", ".join(sorted(old.constants.keys() - new.constants.keys())) or None,
            ", ".join(sorted(new.constants.keys() - old.constants.keys())) or None,
            materiality=0.0,
            constants_only=True,
        )
    return None


def _bindings(snapshot: SymbolSnapshot, other: SymbolSnapshot) -> str | None:
    """The imports of `snapshot` that bind a name `other` does not import, as `name = target`."""
    return (
        "; ".join(
            f"{name} = {target}"
            for name in sorted(snapshot.imports.keys() - other.imports.keys())
            for target in snapshot.imports[name]
        )
        or None
    )


def _delta_without_features(
    path: str,
    old: SymbolSnapshot,
    new: SymbolSnapshot,
    hunk_id: str | None,
    *,
    reworded: bool,
) -> StructuralDelta:
    """The one delta of a changed symbol whose signature, decorators, and features all match.

    A rename, or a body that is the same once formatting is set aside, is a refactor as material
    as the two symbols are different. A body that differs only in a message's wording, or a
    module whose imports alone changed in step with the code using them, is one with nothing
    material. Any other body is a change that cannot be named.
    """
    if old.qualified_name != new.qualified_name or old.fingerprint == new.fingerprint:
        return make_structural_delta(
            path,
            old,
            new,
            hunk_id,
            "structural_refactor",
            old.qualified_name,
            new.qualified_name,
            materiality=round(max(0.0, 1.0 - symbol_similarity(old, new)), 3),
        )
    if reworded:
        return make_structural_delta(
            path,
            old,
            new,
            hunk_id,
            "structural_refactor",
            _changed_raises(old, new) or old.qualified_name,
            _changed_raises(new, old) or new.qualified_name,
            materiality=0.0,
            reworded=True,
        )
    if old.kind == "module" and only_imports_changed(path, old, new):
        return make_structural_delta(
            path,
            old,
            new,
            hunk_id,
            "structural_refactor",
            _bindings(old, new) or old.qualified_name,
            _bindings(new, old) or new.qualified_name,
            materiality=0.0,
            imports_only=True,
        )
    return make_structural_delta(
        path, old, new, hunk_id, "unknown_structure", "body changed", "body changed"
    )


def compare_symbol(
    path: str,
    old: SymbolSnapshot | None,
    new: SymbolSnapshot | None,
    hunks: Sequence[SourceHunk],
    *,
    old_path: str | None = None,
    new_path: str | None = None,
    old_hunks: Sequence[SourceHunk] | None = None,
    new_hunks: Sequence[SourceHunk] | None = None,
    own_calls: frozenset[str] = frozenset(),
    first_party: frozenset[str] = frozenset(),
) -> list[StructuralDelta]:
    effective_old_path = old_path or path
    effective_new_path = new_path or path
    hunk_id = matching_hunk(
        old,
        new,
        old_hunks=old_hunks if old_hunks is not None else hunks,
        new_hunks=new_hunks if new_hunks is not None else hunks,
        old_path=effective_old_path,
        new_path=effective_new_path,
    )
    if hunk_id is None:
        return []
    if old is None:
        if new is None:
            return []
        if new.kind == "module" and new.docstring_only:
            # Top-level code that is only a docstring runs nothing when the module is imported,
            # like an empty `__init__.py`, whose diff has no hunk to report. Its definitions
            # are symbols of their own, and a removed module still breaks what imported it.
            return []
        # What its code returns and raises, and on which branch: what a test of it asserts.
        exits: dict[str, Any] = (
            {"exits": [list(item) for item in new.exits], "exits_total": new.exits_total}
            if new.exits
            else {}
        )
        return [
            make_structural_delta(
                path,
                old,
                new,
                hunk_id,
                "symbol_added",
                None,
                new.display or new.signature,
                ambiguous_match=new.match_ambiguous,
                **exits,
            )
        ]
    if new is None:
        return [
            make_structural_delta(
                path,
                old,
                new,
                hunk_id,
                "symbol_removed",
                old.display or old.signature,
                None,
                ambiguous_match=old.match_ambiguous,
            )
        ]
    refactor = _refactor_without_behavior(path, old, new, hunk_id)
    if refactor is not None:
        if effective_old_path != effective_new_path:
            refactor.metadata.update(old_path=effective_old_path, new_path=effective_new_path)
        return [refactor]
    result: list[StructuralDelta] = []
    if old.signature != new.signature:
        result.append(
            make_structural_delta(
                path,
                old,
                new,
                hunk_id,
                "signature_change",
                old.signature,
                new.signature,
                default_changed=old.default_map != new.default_map,
                old_defaults=old.default_map,
                new_defaults=new.default_map,
                # Only annotations, the return annotation, or type parameters differ: callers
                # pass what they passed before.
                annotations_only=old.parameters is not None and old.parameters == new.parameters,
            )
        )
    if old.decorators != new.decorators:
        result.append(
            make_structural_delta(
                path,
                old,
                new,
                hunk_id,
                "decorator_change",
                ", ".join(old.decorators),
                ", ".join(new.decorators),
                **changed_value_metadata(*_changed_sides(old.decorators, new.decorators)),
            )
        )
    reworded = is_reworded(old, new)
    if not reworded:
        # A reworded message changes the text of its raise and of the call that builds the
        # exception, and nothing either does.
        _append_feature_deltas(result, path, old, new, hunk_id)
        _append_call_delta(result, path, old, new, hunk_id, own_calls, first_party)
    result = suppress_redundant_deltas(result)
    result = _maybe_drop_return_when_comparison_explained(result, old, new)
    result = _maybe_drop_return_when_call_explained(result, old, new)
    if is_reordering(old.statement_order, new.statement_order) and not any(
        item.kind in ORDER_SENSITIVE_KINDS for item in result
    ):
        result.append(
            make_structural_delta(
                path,
                old,
                new,
                hunk_id,
                "statement_order_change",
                "; ".join(old.statement_order),
                "; ".join(new.statement_order),
            )
        )
    if not result:
        result.append(_delta_without_features(path, old, new, hunk_id, reworded=reworded))
    if is_redescribed(old, new):
        # What the descriptions say is for an agent given the tool, whose obligation is a test of
        # what it then tells the user and calls; no code computes with them.
        for item in result:
            item.metadata["described_only"] = True
    if effective_old_path != effective_new_path:
        for item in result:
            item.metadata.update(old_path=effective_old_path, new_path=effective_new_path)
    return result
