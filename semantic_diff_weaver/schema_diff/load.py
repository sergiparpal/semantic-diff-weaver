"""Bounded reading of a YAML, JSON, or TOML document: an API schema or a configuration file.

The document is untrusted repository content. It is parsed with the standard library's JSON or
TOML decoder, or with a budgeted subclass of PyYAML's SafeLoader, which builds nothing but plain
data, and then copied into plain JSON-shaped values under a node and depth budget of its own: a
few YAML aliases can make a small file a large tree to walk. Nothing a document names — a `$ref`,
a URL, a file — is ever followed.
"""

from __future__ import annotations

import json
import tomllib
from typing import Any, ClassVar

import yaml

from ..config import BoundedSafeLoader

MAX_DOCUMENT_NODES = 200_000
MAX_DOCUMENT_DEPTH = 64
MAX_DOCUMENT_ALIASES = 100

# Plain JSON-shaped data: a dict with string keys, a list, a string, a number, a bool, or None.
JsonValue = Any


class DocumentError(ValueError):
    """A document that cannot be read within its budgets."""


class _DocumentLoader(BoundedSafeLoader):
    max_nodes: ClassVar[int] = MAX_DOCUMENT_NODES
    max_depth: ClassVar[int] = MAX_DOCUMENT_DEPTH
    max_aliases: ClassVar[int] = MAX_DOCUMENT_ALIASES
    noun: ClassVar[str] = "document"

    def budget_error(self, message: str) -> Exception:
        return DocumentError(message)


def _plain(value: Any, budget: list[int], depth: int) -> JsonValue:
    """The value as plain data, every mapping key a string, within the node and depth budgets."""
    budget[0] -= 1
    if budget[0] < 0:
        raise DocumentError("The document exceeds its node limit.")
    if depth > MAX_DOCUMENT_DEPTH:
        raise DocumentError("The document exceeds its depth limit.")
    if isinstance(value, dict):
        # YAML reads an unquoted response code such as `200:` as an integer key.
        return {str(key): _plain(item, budget, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item, budget, depth + 1) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    # A YAML or TOML timestamp, a YAML binary value, or a set is compared by its text.
    return str(value)


def load_document(path: str, text: str) -> JsonValue:
    """The document's data: JSON for a `.json` file, TOML for a `.toml` one, safe YAML for any
    other."""
    try:
        if path.endswith(".json"):
            loaded = json.loads(text)
        elif path.endswith(".toml"):
            loaded = tomllib.loads(text)
        else:
            # A bounded subclass of SafeLoader: budgets, and no constructor beyond the safe ones.
            loaded = yaml.load(text, Loader=_DocumentLoader)  # noqa: S506
        return _plain(loaded, [MAX_DOCUMENT_NODES], 0)
    except DocumentError:
        raise
    except (MemoryError, RecursionError, ValueError, yaml.YAMLError) as exc:
        raise DocumentError(
            "The document is not JSON, TOML, or YAML that reads within its budgets."
        ) from exc
