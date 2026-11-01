"""Semantic Diff Weaver public package."""

from .plugin import register

__all__ = ["register"]
# Kept in step with pyproject.toml, plugin.yaml, and .claude-plugin/plugin.json by
# tests/unit/test_regressions.py.
__version__ = "0.8.2"
