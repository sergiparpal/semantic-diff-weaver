"""Changed YAML and JSON documents, told apart as API schemas or not and compared if they are,
and the configuration files a repository names, compared key by key."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..ast_diff import StructuralDelta
from ..source import SourceRevisionPair
from .compare import compare_documents, document_kind
from .load import DocumentError, JsonValue, load_document
from .settings import compare_settings

# The key an API schema opens with and the version or meta-schema it gives, read from the text
# of a document that does not parse: one that names itself a schema is a schema left unread, not
# a file of no interest. Only blanks and a brace start the line, so the search stays linear.
SCHEMA_MARKER_RE = re.compile(
    r"""^[ \t{]*["']?(?:openapi["']?[ \t]*:[ \t]*["']?3|swagger["']?[ \t]*:[ \t]*["']?2"""
    r"""|\$schema["']?[ \t]*:[ \t]*["']?https?://(?:json-schema\.org/|spec\.openapis\.org/))""",
    re.MULTILINE,
)


@dataclass
class DocumentAnalysis:
    deltas: list[StructuralDelta] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # The documents compared, those that parsed and those that did not.
    analyzed_paths: list[str] = field(default_factory=list)
    changed_symbols: int = 0
    # Documents neither side of which is an API schema.
    not_schema: int = 0
    parse_incomplete: int = 0
    # Documents whose deltas ran past the per-document budget.
    truncated_documents: int = 0


def _side(path: str | None, text: str | None) -> tuple[JsonValue | None, bool]:
    """A side's data, or None when there is no such side; and whether it failed to read."""
    if path is None or text is None:
        return None, False
    try:
        return load_document(path, text), False
    except DocumentError:
        return None, True


def _parse_incomplete(path: str, kind: str) -> StructuralDelta:
    return StructuralDelta(
        path=path,
        symbol="<document>",
        kind=kind,
        old=None,
        new=None,
        old_lines=None,
        new_lines=None,
        hunk_id=None,
        parser_complete=False,
    )


def analyze_schemas(pairs: Sequence[SourceRevisionPair]) -> DocumentAnalysis:
    """Compare each changed document that either revision says is an API schema."""
    result = DocumentAnalysis()
    for pair in pairs:
        old, old_failed = _side(
            pair.old_path or pair.path, pair.old_text if pair.has_old_side else None
        )
        new, new_failed = _side(pair.new_path, pair.new_text)
        old_kind, new_kind = document_kind(old), document_kind(new)
        kind = new_kind or old_kind
        if old_failed or new_failed:
            marked = any(
                SCHEMA_MARKER_RE.search(text or "") for text in (pair.old_text, pair.new_text)
            )
            if kind is None and not marked:
                result.not_schema += 1
                continue
            result.parse_incomplete += 1
            result.analyzed_paths.append(pair.path)
            result.deltas.append(_parse_incomplete(pair.path, "document_parse_incomplete"))
            result.warnings.append(
                f"Could not parse the API schema {pair.path!r} within its budgets."
            )
            continue
        if kind is None:
            result.not_schema += 1
            continue
        deltas, truncated = compare_documents(
            pair.path,
            old if old_kind == kind else None,
            new if new_kind == kind else None,
            kind,
        )
        result.analyzed_paths.append(pair.path)
        result.deltas.extend(deltas)
        if truncated:
            result.truncated_documents += 1
            result.warnings.append(
                f"Only the first changes of the API schema {pair.path!r} were compared."
            )
    result.changed_symbols = len({(item.path, item.symbol) for item in result.deltas})
    return result


def analyze_settings(pairs: Sequence[SourceRevisionPair]) -> DocumentAnalysis:
    """Compare each changed configuration file the repository names, key by key.

    The repository asked for these, so one that does not parse within its budgets is a finding,
    whatever it holds.
    """
    result = DocumentAnalysis()
    for pair in pairs:
        old, old_failed = _side(
            pair.old_path or pair.path, pair.old_text if pair.has_old_side else None
        )
        new, new_failed = _side(pair.new_path, pair.new_text)
        result.analyzed_paths.append(pair.path)
        if old_failed or new_failed:
            result.parse_incomplete += 1
            result.deltas.append(_parse_incomplete(pair.path, "config_parse_incomplete"))
            result.warnings.append(
                f"Could not parse the configuration file {pair.path!r} within its budgets."
            )
            continue
        deltas, truncated = compare_settings(pair.path, old, new)
        result.deltas.extend(deltas)
        if truncated:
            result.truncated_documents += 1
            result.warnings.append(
                f"Only the first changes of the configuration file {pair.path!r} were compared."
            )
    result.changed_symbols = len({(item.path, item.symbol) for item in result.deltas})
    return result
