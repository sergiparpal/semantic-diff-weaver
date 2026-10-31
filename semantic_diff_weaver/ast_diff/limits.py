"""Immutable AST analysis budgets and matching thresholds."""

from __future__ import annotations

from dataclasses import dataclass

MAX_AST_NODES_PER_FILE = 50_000
MAX_AST_DEPTH = 200
MAX_SYMBOLS_PER_FILE = 2_000
MAX_AST_SOURCE_BYTES_PER_VERSION = 1_000_000
MAX_AST_SOURCE_BYTES_TOTAL = 16 * 1024 * 1024
MAX_EXTRACTED_SYMBOLS_TOTAL = 4_000
AST_ANALYSIS_TIMEOUT_SECONDS = 10.0
MAX_SIMILARITY_CANDIDATES = 64
MAX_EXACT_GROUP_COMPARISONS = 4_096
SIMILARITY_ACCEPT_THRESHOLD = 0.82
SIMILARITY_TIE_MARGIN = 0.03

FEATURE_DELTA_KINDS: tuple[tuple[str, str], ...] = (
    ("comparisons", "comparison_change"),
    ("conditions", "condition_change"),
    ("raises", "raise_change"),
    ("handlers", "exception_handler_change"),
    ("returns", "return_change"),
    ("assignments", "assignment_change"),
    ("loops", "loop_change"),
    ("contexts", "context_manager_change"),
)

FEATURE_NAMES: tuple[str, ...] = (
    *(name for name, _ in FEATURE_DELTA_KINDS),
    "calls",
)

# The features whose deltas the category rules search for keywords, and so the only ones that
# record a probe (see `extract.category_probe`). The other kinds are classified by structure alone.
# An assignment's probe covers its targets only, not the value it assigns.
PROBED_FEATURES: tuple[str, ...] = ("comparisons", "conditions", "calls", "assignments")
# The probed features whose rules read the words of the values a delta changed, and not of every
# value it shows: an unchanged `row['valid']` made a `recorder is not None` added beside it a
# validation guard, and an unchanged `owner in module.symbols` an authorization one. Calls and
# assignments still read every value their delta shows.
CHANGED_VALUE_FEATURES: tuple[str, ...] = ("comparisons", "conditions")
MAX_PROBE_CHARS_PER_FEATURE = 500
# A safety bound only: a side's probe already covers no more values than its display text.
MAX_PROBE_CHARS_PER_SIDE = 3_000
# A delta's `old` and `new` display texts are each cut to this length.
MAX_DELTA_TEXT_CHARS = 1_500
# How much of an added or removed class or module its display text names.
MAX_DISPLAY_CLASS_FIELDS = 20
MAX_DISPLAY_MODULE_STATEMENTS = 5


@dataclass(frozen=True)
class AstBudget:
    """The safety budgets one AST analysis run is allowed to spend.

    Passed explicitly down the extraction call chain instead of being read from ambient module
    state, so a caller can bound a run without mutating process-wide globals. ``default`` reads
    the module constants at call time, which keeps them the single place to change a limit.
    """

    max_nodes_per_file: int
    max_depth: int
    max_symbols_per_file: int
    max_source_bytes_per_version: int
    max_source_bytes_total: int
    max_extracted_symbols_total: int
    analysis_timeout_seconds: float

    @classmethod
    def default(cls) -> AstBudget:
        return cls(
            max_nodes_per_file=MAX_AST_NODES_PER_FILE,
            max_depth=MAX_AST_DEPTH,
            max_symbols_per_file=MAX_SYMBOLS_PER_FILE,
            max_source_bytes_per_version=MAX_AST_SOURCE_BYTES_PER_VERSION,
            max_source_bytes_total=MAX_AST_SOURCE_BYTES_TOTAL,
            max_extracted_symbols_total=MAX_EXTRACTED_SYMBOLS_TOTAL,
            analysis_timeout_seconds=AST_ANALYSIS_TIMEOUT_SECONDS,
        )
