"""Static Python AST extraction, conservative symbol matching, and structural deltas."""

from __future__ import annotations

from .analyze import analyze_ast
from .compare import compare_symbol, make_structural_delta
from .extract import AstResourceLimit, FeatureVisitor, extract_symbols, unparse_redacted
from .limits import (
    AST_ANALYSIS_TIMEOUT_SECONDS,
    FEATURE_DELTA_KINDS,
    FEATURE_NAMES,
    MAX_AST_DEPTH,
    MAX_AST_NODES_PER_FILE,
    MAX_AST_SOURCE_BYTES_PER_VERSION,
    MAX_AST_SOURCE_BYTES_TOTAL,
    MAX_EXACT_GROUP_COMPARISONS,
    MAX_EXTRACTED_SYMBOLS_TOTAL,
    MAX_SIMILARITY_CANDIDATES,
    MAX_SYMBOLS_PER_FILE,
    SIMILARITY_ACCEPT_THRESHOLD,
    SIMILARITY_TIE_MARGIN,
)
from .match import match_cross_file_symbols, match_symbols, symbol_similarity
from .types import AstAnalysis, StructuralDelta, SymbolPair, SymbolSnapshot, reparameterize

__all__ = [
    "AST_ANALYSIS_TIMEOUT_SECONDS",
    "FEATURE_DELTA_KINDS",
    "FEATURE_NAMES",
    "MAX_AST_DEPTH",
    "MAX_AST_NODES_PER_FILE",
    "MAX_AST_SOURCE_BYTES_PER_VERSION",
    "MAX_AST_SOURCE_BYTES_TOTAL",
    "MAX_EXACT_GROUP_COMPARISONS",
    "MAX_EXTRACTED_SYMBOLS_TOTAL",
    "MAX_SIMILARITY_CANDIDATES",
    "MAX_SYMBOLS_PER_FILE",
    "SIMILARITY_ACCEPT_THRESHOLD",
    "SIMILARITY_TIE_MARGIN",
    "AstAnalysis",
    "AstResourceLimit",
    "FeatureVisitor",
    "StructuralDelta",
    "SymbolPair",
    "SymbolSnapshot",
    "analyze_ast",
    "compare_symbol",
    "extract_symbols",
    "make_structural_delta",
    "match_cross_file_symbols",
    "match_symbols",
    "reparameterize",
    "symbol_similarity",
    "unparse_redacted",
]
