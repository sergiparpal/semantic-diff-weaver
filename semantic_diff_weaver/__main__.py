"""Support `python -m semantic_diff_weaver` alongside the console script."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
