"""Small text, bounded-read, and duck-typing helpers shared across pipeline stages."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .errors import WeaverError

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


def _bounded_file(path: Path, limit: int, noun: str, error: Callable[[str], WeaverError]) -> Path:
    """The regular file a caller named, resolved, if it holds at most `limit` bytes."""
    try:
        resolved = path.resolve(strict=True)
        size = resolved.stat().st_size
    except OSError as exc:
        raise error(f"The {noun} is inaccessible.") from exc
    if not resolved.is_file():
        raise error(f"The {noun} is not a regular file.")
    if size > limit:
        raise error(f"The {noun} is {size} bytes, above the {limit}-byte limit.")
    return resolved


def read_bounded_text(
    path: Path, limit: int, noun: str, error: Callable[[str], WeaverError]
) -> str:
    """Read a file a caller named as untrusted input: strict UTF-8, at most `limit` bytes.

    `noun` names the file in each message, as "coverage report" does, and `error` turns a message
    into the stable error the input's own module raises.
    """
    resolved = _bounded_file(path, limit, noun, error)
    try:
        # One character past the limit, so a file that grows between stat and read is caught.
        with resolved.open("r", encoding="utf-8", errors="strict") as handle:
            text = handle.read(limit + 1)
    except (OSError, UnicodeDecodeError) as exc:
        raise error(f"The {noun} is not readable UTF-8 text.") from exc
    if len(text.encode("utf-8", errors="ignore")) > limit:
        raise error(f"The {noun} exceeded the size limit while being read.")
    return text


def read_bounded_bytes(
    path: Path, limit: int, noun: str, error: Callable[[str], WeaverError]
) -> bytes:
    """Read a binary file a caller named as untrusted input: at most `limit` bytes, as
    `read_bounded_text` reads a text one."""
    resolved = _bounded_file(path, limit, noun, error)
    try:
        # One byte past the limit, so a file that grows between stat and read is caught.
        with resolved.open("rb") as handle:
            data = handle.read(limit + 1)
    except OSError as exc:
        raise error(f"The {noun} is not readable.") from exc
    if len(data) > limit:
        raise error(f"The {noun} exceeded the size limit while being read.")
    return data
