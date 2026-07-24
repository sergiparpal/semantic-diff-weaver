"""Small text and duck-typing helpers shared across pipeline stages."""

from __future__ import annotations

import re
from typing import Any

_CANONICAL_PHRASE_RE = re.compile(r"[^a-z0-9]+")


def canonical_phrase(text: str) -> str:
    """Normalize free text for semantic equality checks."""
    return _CANONICAL_PHRASE_RE.sub(" ", text.casefold()).strip()


def getattr_or_key(value: Any, name: str, default: Any = None) -> Any:
    """Read either a mapping key or object attribute without caring which shape arrived."""
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)
