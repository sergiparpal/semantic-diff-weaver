"""Semantic Diff Weaver public package."""

from .plugin import register

__all__ = ["register"]
# Kept in step with pyproject.toml and plugin.yaml by tests/unit/test_regressions.py.
__version__ = "0.3.0"
