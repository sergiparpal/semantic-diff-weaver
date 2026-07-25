"""Static Python AST extraction, conservative symbol matching, and structural deltas.

Only the pipeline-facing surface is re-exported. Extraction internals, matching helpers, and
the safety thresholds stay in their defining modules; a caller that needs to vary budgets
passes an :class:`AstBudget` rather than reaching through this facade to patch a constant.
"""

from __future__ import annotations

from .analyze import analyze_ast
from .extract import AstResourceLimit, extract_symbols
from .limits import AstBudget
from .types import AstAnalysis, StructuralDelta, SymbolPair, SymbolSnapshot

__all__ = [
    "AstAnalysis",
    "AstBudget",
    "AstResourceLimit",
    "StructuralDelta",
    "SymbolPair",
    "SymbolSnapshot",
    "analyze_ast",
    "extract_symbols",
]
