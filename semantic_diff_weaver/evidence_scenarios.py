"""Test-obligation scenarios written from a Python finding's own evidence.

The scenarios `taxonomy.py` keeps for each category name no symbol and no value, so they read the
same in every case. A blind review of 20 on the benchmark's development cases judged none worth
writing as proposed (`docs/benchmark.md`, "With a model"), while the evidence held what a test
needed: the default that changed, the call whose arguments did, the condition a branch now tests.
A finding's scenario quotes its evidence instead: what to run, what changed, and what to assert.
The category's scenarios remain for a finding no Python delta describes, such as one on an API
schema or a configuration file, or a change the parser could not read. A function that changed
only as the functions nested in it did has no line of its own to quote either, and its scenario
names them, whose findings say what changed. A callable added has no old code to quote, and its
scenario lists what its own code returns and raises, each on the branch that leads there. A schema
that changed only how it describes a tool changes nothing a test of the tool's code could tell
apart, and its scenario asks what an agent given the tool does with the new text.

Only what the evidence holds is quoted, already redacted and bounded, each fragment as a code
span. The renderer keeps it one, so a repository's text never becomes Markdown of its own.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from .ast_diff import StructuralDelta
from .models import BehaviorCategory, BehaviorChange, Evidence, ObligationType
from .semantic_candidates import NAMING_NOTHING, evidence_id, has_boundary_comparison
from .taxonomy import CATEGORY_PROFILES, Scenario
from .textutil import code_span

if TYPE_CHECKING:
    from .call_graph import CallGraph

# How much of one fragment of evidence a scenario quotes, how many values of each side, and how
# much of what two long versions of one value share it keeps around where they differ.
MAX_QUOTE_CHARS = 120
MAX_QUOTED_VALUES = 2
# How many of the things an added callable returns or raises its scenario names.
MAX_OUTCOMES = 3
SHARED_CONTEXT_CHARS = 30
# A second scenario of one finding, the former default passed explicitly or either side of a
# boundary, follows the first. Relevance weighs a fifth of a priority, so a drop of 10 lowers it by
# 2: a drop of 5 lowered it by exactly 1, which rounding half to even could undo, and the title
# then ordered the two.
SECOND_SCENARIO_RELEVANCE_DROP = 10

# What a call reaches the outside world through: the network, files, or another process. A
# changed call of one is the effect itself, so a test intercepts it and asserts what it was handed.
# Any other call computes a value, a builtin's or a library's, and spying on it pins the symbol's
# implementation: a second blind review judged five such obligations noise, a spy on `next`,
# `isinstance`, `str.lower`, `environ.get`, or a file's `close`, and the one on an HTTP request
# useful. Its change matters through what the symbol returns, raises, or leaves.
OUTSIDE_MODULES = (
    "aiohttp",
    "ftplib",
    "http.client",
    "httpx",
    "requests",
    "shutil",
    "smtplib",
    "socket",
    "sqlite3",
    "ssl",
    "subprocess",
    "tempfile",
    "urllib.request",
    "urllib3",
    "webbrowser",
)
OUTSIDE_FUNCTIONS = frozenset(
    {
        "input",
        "open",
        "os.chmod",
        "os.kill",
        "os.makedirs",
        "os.mkdir",
        "os.popen",
        "os.remove",
        "os.rename",
        "os.replace",
        "os.rmdir",
        "os.system",
        "os.unlink",
        "os.write",
    }
)
OUTSIDE_METHODS = frozenset(
    {
        "commit",
        "connect",
        "execute",
        "executemany",
        "fetch",
        "flush",
        "mkdir",
        "patch",
        "post",
        "put",
        "read_bytes",
        "read_text",
        "recv",
        "recv_into",
        "request",
        "rmdir",
        "rollback",
        "send",
        "sendall",
        "sendfile",
        "sendto",
        "touch",
        "unlink",
        "urlopen",
        "write",
        "write_bytes",
        "write_text",
        "writelines",
    }
)
# A method name many types share, a mapping's `get` among them, reaches outside only on an object
# whose name says it is a client of something.
SHARED_METHODS = frozenset({"delete", "get", "head", "options"})
CLIENT_NAMES = ("client", "conn", "cursor", "http", "session", "sock", "transport")

ORDER_KINDS = frozenset({"call_order_change", "condition_order_change", "statement_order_change"})
# The kinds of the deltas `ast_diff` makes of Python code. A parse it could not complete, or a body
# that changed in no way it can name, describes no change a scenario could quote.
PYTHON_KINDS = frozenset(
    {
        "signature_change",
        "decorator_change",
        "comparison_change",
        "condition_change",
        "raise_change",
        "exception_handler_change",
        "return_change",
        "assignment_change",
        "loop_change",
        "context_manager_change",
        "call_change",
        "symbol_added",
        "symbol_removed",
        "structural_refactor",
        "unknown_structure",
        *ORDER_KINDS,
    }
)

# The kinds of evidence that best say what a finding of each category is about, first to last.
# Any other kind follows, in the order of `_GENERAL`.
_PREFERRED: dict[BehaviorCategory, tuple[str, ...]] = {
    BehaviorCategory.DEFAULT_BEHAVIOR: ("signature_change",),
    BehaviorCategory.BOUNDARY: ("comparison_change", "condition_change"),
    BehaviorCategory.RETRY_TIMEOUT: (
        "comparison_change",
        "condition_change",
        "loop_change",
        "call_change",
    ),
    BehaviorCategory.ERROR_HANDLING: ("raise_change", "exception_handler_change"),
    BehaviorCategory.OUTPUT_CONTRACT: ("return_change", "signature_change"),
    BehaviorCategory.STATE_TRANSITION: ("assignment_change",),
    BehaviorCategory.AUTHORIZATION: (
        "condition_change",
        "comparison_change",
        "call_change",
        "decorator_change",
    ),
    BehaviorCategory.VALIDATION: ("condition_change", "comparison_change", "call_change"),
    BehaviorCategory.SIDE_EFFECT: ("call_change",),
    BehaviorCategory.DEPENDENCY_INTERACTION: ("call_change",),
    BehaviorCategory.ORDERING: (
        "condition_order_change",
        "call_order_change",
        "statement_order_change",
    ),
    BehaviorCategory.REFACTOR: ("structural_refactor",),
}
# A branch says most about what to test, then what a symbol produces, then what it calls.
_GENERAL: tuple[str, ...] = (
    "condition_change",
    "comparison_change",
    "loop_change",
    "return_change",
    "raise_change",
    "exception_handler_change",
    "call_change",
    "context_manager_change",
    "assignment_change",
    "signature_change",
    "decorator_change",
    "condition_order_change",
    "call_order_change",
    "statement_order_change",
    "symbol_added",
    "symbol_removed",
    "structural_refactor",
    "unknown_structure",
)
# A kind without a place here would fail `_primary` on the first analysis that reached it.
if set(_GENERAL) != PYTHON_KINDS:  # pragma: no cover - guards an edit, not a runtime input
    raise RuntimeError(f"Kinds out of step: {sorted(set(_GENERAL) ^ PYTHON_KINDS)}")


@dataclass(frozen=True)
class Change:
    """What one Python delta changed, as the scenarios of its evidence quote it."""

    kind: str
    # The values it changed on each side, at most `MAX_CHANGED_VALUES` of each, and how many.
    removed: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    removed_total: int = 0
    added_total: int = 0
    # Each parameter whose default changed: its name, and its default before and after, if any.
    defaults: tuple[tuple[str, str | None, str | None], ...] = ()
    # Whether only the signature's annotations changed, so callers pass what they passed.
    annotations_only: bool = False
    # Whether a changed comparison or condition compares a boundary.
    boundary: bool = False
    # Whether every call that changed reaches the project's own code.
    internal: bool = False
    # Whether an added or removed symbol had a near-equal match it could not choose between.
    ambiguous: bool = False
    # The variables a changed assignment writes to.
    assigned_names: tuple[str, ...] = ()
    # As extraction labels the symbol: `function`, `method`, `class`, `module`, and so on.
    symbol_kind: str | None = None
    # Whether its symbol changed nothing but the `description` texts of the schemas it holds.
    described_only: bool = False
    # An added callable's exits, as `ast_diff` records them: each a kind, `return` or `raise`, the
    # value, and the branch leading there as a word and a test; and how many it has.
    exits: tuple[tuple[str, str, str, str], ...] = ()
    exits_total: int = 0

    @property
    def removed_only(self) -> bool:
        return bool(self.removed) and not self.added


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _count(value: object, at_least: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return max(value, at_least)
    return at_least


def _without_operators(comparison: str) -> str:
    """A comparison as code shows it: `extract` records `x < 5` as `x < 5 [Lt]`."""
    text, separator, operators = comparison.rpartition(" [")
    return text if separator and operators.endswith("]") else comparison


def _defaults(delta: StructuralDelta) -> tuple[tuple[str, str | None, str | None], ...]:
    before, after = delta.metadata.get("old_defaults"), delta.metadata.get("new_defaults")
    if not isinstance(before, dict) or not isinstance(after, dict):
        return ()
    names = [*before, *(name for name in after if name not in before)]
    return tuple(
        (name, before.get(name), after.get(name))
        for name in names
        if isinstance(name, str) and before.get(name) != after.get(name)
    )


def describe_change(delta: StructuralDelta) -> Change | None:
    """What a Python delta changed, or None for a delta of another kind."""
    if delta.kind not in PYTHON_KINDS:
        return None
    metadata = delta.metadata
    removed, added = _strings(metadata.get("removed")), _strings(metadata.get("added"))
    if delta.kind == "comparison_change":
        removed = tuple(_without_operators(item) for item in removed)
        added = tuple(_without_operators(item) for item in added)
    if delta.kind == "unknown_structure" and not (removed or added):
        # Without the lines that changed there is nothing to quote: the category speaks.
        return None
    defaults_changed = delta.kind == "signature_change" and metadata.get("default_changed")
    return Change(
        kind=delta.kind,
        removed=removed,
        added=added,
        removed_total=_count(metadata.get("removed_total"), len(removed)),
        added_total=_count(metadata.get("added_total"), len(added)),
        defaults=_defaults(delta) if defaults_changed else (),
        annotations_only=bool(metadata.get("annotations_only")),
        boundary=delta.kind in {"comparison_change", "condition_change"}
        and has_boundary_comparison(delta),
        internal=bool(metadata.get("internal_calls")),
        ambiguous=bool(metadata.get("ambiguous_match")),
        assigned_names=_strings(metadata.get("assigned_names")),
        symbol_kind=delta.symbol_kind,
        described_only=bool(metadata.get("described_only")),
        exits=(exits := _exits(metadata.get("exits"))),
        exits_total=_count(metadata.get("exits_total"), len(exits)),
    )


def _exits(value: object) -> tuple[tuple[str, str, str, str], ...]:
    """The exits a delta's metadata records, each four strings; anything else is dropped."""
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        (item[0], item[1], item[2], item[3])
        for item in value
        if isinstance(item, (list, tuple))
        and len(item) == 4
        and all(isinstance(part, str) for part in item)
    )


def leaves_the_process(call: str) -> bool:
    """Whether a call, as extraction records it, `session.post(url, data=body)`, reaches the
    network, a file, or another process, by its name alone."""
    callee = call.split("(", 1)[0]
    if callee in OUTSIDE_FUNCTIONS or any(
        callee == module or callee.startswith(f"{module}.") for module in OUTSIDE_MODULES
    ):
        return True
    receiver, _, method = callee.rpartition(".")
    if not receiver:
        return False
    if method in OUTSIDE_METHODS:
        return True
    owner = receiver.rpartition(".")[2].casefold()
    return method in SHARED_METHODS and any(name in owner for name in CLIENT_NAMES)


def changes_by_evidence(deltas: Sequence[StructuralDelta]) -> dict[str, Change]:
    """What each Python delta changed, by the ID of the evidence `build_candidates` makes of it."""
    return {
        evidence_id(position): change
        for position, delta in enumerate(deltas, start=1)
        if (change := describe_change(delta)) is not None
    }


def _flat(text: str | None) -> str:
    return " ".join((text or "").split())


def _quote(text: str | None) -> str:
    """A fragment of evidence as a code span, its whitespace collapsed and its length bounded."""
    shown = _flat(text)
    if len(shown) > MAX_QUOTE_CHARS:
        shown = shown[: MAX_QUOTE_CHARS - 1].rstrip() + "…"
    return code_span(shown or "∅")


def _quote_pair(before: str, after: str) -> tuple[str, str]:
    """Two versions of one value, each cut around where they differ when either is too long.

    Cut at the end alone, a long `__all__` that gained a name read the same on both sides.
    """
    old, new = _flat(before), _flat(after)
    if max(len(old), len(new)) <= MAX_QUOTE_CHARS:
        return _quote(old), _quote(new)
    shortest = min(len(old), len(new))
    prefix = 0
    while prefix < shortest and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while suffix < shortest - prefix and old[-1 - suffix] == new[-1 - suffix]:
        suffix += 1
    start = max(0, prefix - SHARED_CONTEXT_CHARS)
    kept_suffix = max(0, suffix - SHARED_CONTEXT_CHARS)

    def around(text: str) -> str:
        end = len(text) - kept_suffix
        return f"{'…' if start else ''}{text[start:end]}{'…' if kept_suffix else ''}"

    return _quote(around(old)), _quote(around(new))


def _series(items: Sequence[str]) -> str:
    if len(items) <= 2:
        return " and ".join(items)
    return f"{', '.join(items[:-1])}, and {items[-1]}"


def _quotes(values: Sequence[str], total: int) -> str:
    """Up to `MAX_QUOTED_VALUES` distinct values, and how many more there are."""
    distinct = list(dict.fromkeys(values))
    shown = [_quote(value) for value in distinct[:MAX_QUOTED_VALUES]]
    more = max(0, total - len(values)) + len(distinct) - len(shown)
    if more > 0:
        shown.append(f"{more} more")
    return _series(shown)


def _subject(evidence: Evidence) -> str:
    symbol = evidence.symbol or "<module>"
    if symbol == "<module>":
        return f"the top-level code of {_quote(evidence.path)}"
    if symbol.startswith("<"):
        return _quote(evidence.path)
    return _quote(symbol)


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def _runs(subject: str, evidence: Evidence, change: Change) -> str:
    """How a test reaches the code of the symbol: a class's body runs as code uses the class."""
    if (evidence.symbol or "<module>") == "<module>":
        return f"{subject} runs as the module is imported"
    if change.symbol_kind == "class":
        return f"Code that uses {subject} runs"
    return f"{subject} runs"


def _called(subject: str, evidence: Evidence) -> str:
    symbol = evidence.symbol or ""
    owner, _, name = symbol.rpartition(".")
    if name == "__init__" and owner:
        return f"{_quote(owner)} is instantiated"
    return f"{subject} is called"


@dataclass(frozen=True)
class _Verb:
    """How a scenario says what a symbol does now, what it did, and what it holds."""

    now: str
    before: str
    noun: str


_VERBS = {
    "comparison_change": _Verb("compares", "compared", "comparisons"),
    "condition_change": _Verb("tests", "tested", "conditions"),
    "raise_change": _Verb("raises", "raised", "raised exceptions"),
    "exception_handler_change": _Verb("handles", "handled", "exception handlers"),
    "return_change": _Verb("returns", "returned", "returned values"),
    "assignment_change": _Verb("assigns", "assigned", "assignments"),
    "loop_change": _Verb("runs the loop", "ran the loop", "loops"),
    "context_manager_change": _Verb("enters", "entered", "with blocks"),
    "call_change": _Verb("calls", "called", "calls"),
    "decorator_change": _Verb("is decorated with", "was decorated with", "decorators"),
}


def _what_changed(subject: str, evidence: Evidence, change: Change) -> str:
    """`subject` now does what the change added, where it did what the change removed."""
    verb = _VERBS[change.kind]
    if (change.removed_total, change.added_total) == (1, 1) and change.removed and change.added:
        removed, added = _quote_pair(change.removed[0], change.added[0])
    else:
        added = _quotes(change.added, change.added_total)
        removed = _quotes(change.removed, change.removed_total)
    if change.kind == "decorator_change":
        if change.added and change.removed:
            return f"{subject} is now decorated with {added} where it was decorated with {removed}"
        if change.added:
            return f"{subject} is now also decorated with {added}"
        if change.removed:
            return f"{subject} is no longer decorated with {removed}"
    elif change.added and change.removed:
        return f"{subject} now {verb.now} {added} where it {verb.before} {removed}"
    elif change.added:
        return f"{subject} now also {verb.now} {added}"
    elif change.removed:
        return f"{subject} no longer {verb.now} {removed}"
    # The same values in another order, or a delta that recorded none.
    return f"{subject} changed its {verb.noun}, now {_quote(evidence.new)}"


def _scenario(
    kind: ObligationType, title: str, given: str, when: str, then: str, relevance: int
) -> Scenario:
    return Scenario(
        kind, _sentence(title), _sentence(given), _sentence(when), _sentence(then), relevance
    )


@dataclass(frozen=True)
class _Finding:
    """What every scenario of one finding draws on."""

    category: BehaviorCategory
    evidence: Evidence
    change: Change
    subject: str
    # What changed, as `_what_changed` says it, for the kinds it can say.
    changed: str
    relevance: int
    # The symbols whose code reads a module-level variable a changed assignment writes.
    readers: tuple[str, ...] = ()
    # The symbols whose code calls an added symbol.
    callers: tuple[str, ...] = ()

    @property
    def runs(self) -> str:
        return _runs(self.subject, self.evidence, self.change)

    @property
    def called(self) -> str:
        return _called(self.subject, self.evidence)

    def scenario(
        self,
        kind: ObligationType,
        title: str,
        given: str,
        when: str,
        then: str,
        *,
        second: bool = False,
    ) -> Scenario:
        drop = SECOND_SCENARIO_RELEVANCE_DROP if second else 0
        return _scenario(kind, title, given, when, then, max(0, self.relevance - drop))


def _default_scenarios(found: _Finding) -> tuple[Scenario, ...]:
    name, before, after = found.change.defaults[0]
    parameter, subject = _quote(name), found.subject
    others = len(found.change.defaults) - 1
    also = f"; {others} more parameter default(s) changed too" if others else ""
    if before is not None and after is not None:
        old, new = _quote_pair(before, after)
        given = f"{parameter} is omitted, so its new default {new} applies where {old} did"
        then = f"The result is the one the default {new} gives, not {old}"
    elif after is not None:
        given = f"{parameter} is omitted, which its new default {_quote(after)} allows"
        then = f"The result is the one the default {_quote(after)} gives"
    else:
        given = f"{parameter} is omitted, which its former default {_quote(before)} allowed"
        then = f"The call now fails explicitly where it applied {_quote(before)}"
    omitted = found.scenario(
        ObligationType.REGRESSION,
        f"Verify omitted-input behavior of {subject} without {parameter}",
        given + also,
        found.called,
        then,
    )
    if before is None:
        return (omitted,)
    explicit = found.scenario(
        ObligationType.POSITIVE,
        f"Verify {subject} with {parameter} passed as the former default {_quote(before)}",
        f"{parameter} is passed explicitly as {_quote(before)}, what callers got by omitting it",
        found.called,
        f"The result is the one {_quote(before)} gave before the change",
        second=True,
    )
    return (omitted, explicit)


def _signature_scenario(found: _Finding) -> Scenario:
    old, new = _quote_pair(found.evidence.old or "", found.evidence.new or "")
    if found.change.annotations_only:
        return found.scenario(
            ObligationType.REVIEW,
            f"Check the consumer-visible contract of the annotations of {found.subject}",
            f"The declared types of {found.subject} changed from {old} to {new}, while callers "
            "pass the same arguments",
            "A type checker, or code that reads annotations at runtime, reads the new signature",
            "Callers, and whatever reads the annotations, accept the new declared contract",
        )
    return found.scenario(
        ObligationType.NEGATIVE,
        f"Check former contract behavior of {found.subject}",
        f"A caller calls {found.subject} as its former signature {old} allowed; it is now {new}",
        "That call runs",
        "It still works, or fails with an explicit error",
    )


def _boundary_scenarios(found: _Finding) -> tuple[Scenario, ...]:
    retry = found.category is BehaviorCategory.RETRY_TIMEOUT
    exact = (
        f"Exercise the exact retry or timeout limit of {found.subject}"
        if retry
        else f"Exercise the exact changed boundary in {found.subject}"
    )
    if found.change.removed_only:
        return (
            found.scenario(
                ObligationType.BOUNDARY,
                exact,
                f"{found.changed}; inputs sit on each side of that former boundary",
                f"{found.runs} with each",
                "Each gets the outcome intended now that nothing tells them apart, which is "
                "asserted",
            ),
        )
    return (
        found.scenario(
            ObligationType.BOUNDARY,
            exact,
            f"{found.changed}; an input sits exactly at that boundary",
            found.runs,
            "The outcome at the boundary, which the change may have flipped, is asserted",
        ),
        found.scenario(
            ObligationType.BOUNDARY,
            f"Exercise either side of the changed boundary in {found.subject}",
            f"{found.changed}; inputs sit just below and just above that boundary",
            f"{found.runs} with each",
            "The outcome on each side is asserted",
            second=True,
        ),
    )


def _condition_scenario(found: _Finding) -> Scenario:
    gone = found.change.removed_only
    satisfy = "satisfied the removed condition" if gone else "satisfies the condition"
    outcome = (
        "Each gets the outcome intended now that the condition is gone, which is asserted"
        if gone
        else None
    )
    if found.category is BehaviorCategory.AUTHORIZATION:
        return found.scenario(
            ObligationType.NEGATIVE,
            f"Verify an allowed and a denied principal at the changed guard of {found.subject}",
            found.changed,
            f"A principal that {satisfy.replace('condition', 'guard')}, and one that does not, "
            "attempt the operation",
            outcome
            or "The allowed one gets its outcome, and the denied one is refused without the "
            "protected side effect",
        )
    if found.category is BehaviorCategory.VALIDATION:
        return found.scenario(
            ObligationType.NEGATIVE,
            "Verify newly accepted and newly rejected input at the changed validation of "
            f"{found.subject}",
            found.changed,
            f"An input that {satisfy}, and one that does not, are validated",
            outcome or "The first is accepted and the second rejected observably",
        )
    if found.category is BehaviorCategory.RETRY_TIMEOUT:
        return found.scenario(
            ObligationType.BOUNDARY,
            f"Exercise the exact retry or timeout limit of the changed predicate of "
            f"{found.subject}",
            found.changed,
            "A failure the predicate now judges differently occurs, again and again",
            "The number of attempts, and when they stop, are asserted",
        )
    if gone:
        return found.scenario(
            ObligationType.REGRESSION,
            f"Exercise the cases the removed condition told apart in {found.subject}",
            found.changed,
            f"{found.runs} once with input that made the condition true and once with input "
            "that made it false",
            "Each gets the outcome intended now that the condition is gone, which is asserted",
        )
    return found.scenario(
        ObligationType.REGRESSION,
        f"Exercise both outcomes of the changed condition in {found.subject}",
        found.changed,
        f"{found.runs} once with input that makes the condition true and once with input that "
        "makes it false",
        "The outcome of each branch is asserted",
    )


def _call_scenario(found: _Finding) -> Scenario:
    observed = f"{found.runs}, with the call observed through a stub or a spy"
    if found.category is BehaviorCategory.SIDE_EFFECT:
        return found.scenario(
            ObligationType.INTERACTION,
            f"Verify the side-effect occurrence of the changed call in {found.subject}",
            found.changed,
            f"{found.runs} with the external effect observed",
            "The write, event, warning, or notification happens exactly as intended, and not "
            "when it should not",
        )
    if found.category is BehaviorCategory.AUTHORIZATION:
        return found.scenario(
            ObligationType.NEGATIVE,
            f"Verify an allowed and a denied principal through the changed call in {found.subject}",
            found.changed,
            "A principal the call authorizes, and one it does not, attempt the operation",
            "The allowed one gets its outcome, and the denied one is refused without the "
            "protected side effect",
        )
    if found.category is BehaviorCategory.VALIDATION:
        return found.scenario(
            ObligationType.NEGATIVE,
            f"Verify newly accepted and newly rejected input through the changed call in "
            f"{found.subject}",
            found.changed,
            "An input the validator accepts, and one it rejects, pass through it",
            "The first is accepted and the second rejected observably",
        )
    if found.category is BehaviorCategory.RETRY_TIMEOUT:
        return found.scenario(
            ObligationType.INTERACTION,
            f"Exercise the exact retry or timeout limit through the changed call in "
            f"{found.subject}",
            found.changed,
            "The callee fails until the limit is reached",
            "The attempts, the waits, and the final outcome are asserted",
        )
    outside = _outside(found.change)
    if outside.removed_only:
        return found.scenario(
            ObligationType.INTERACTION,
            f"Assert what {found.subject} does without its former call",
            _what_changed(found.subject, found.evidence, outside),
            observed,
            "The call no longer happens, and what takes its place is asserted",
        )
    if outside.added and not outside.removed:
        return found.scenario(
            ObligationType.INTERACTION,
            f"Assert the arguments of the new call in {found.subject}",
            _what_changed(found.subject, found.evidence, outside),
            observed,
            "The call happens when intended, with the arguments intended",
        )
    if outside.added:
        return found.scenario(
            ObligationType.INTERACTION,
            f"Assert the arguments of the changed call in {found.subject}",
            _what_changed(found.subject, found.evidence, outside),
            observed,
            "The arguments it passes, and what it does with the result, match the intended change",
        )
    # No changed call leaves the process: what the symbol makes of the call is what to check.
    own = ", its own project's code" if found.change.internal else ""
    # A module's top-level code takes no input: it runs as the module is imported.
    module = (found.evidence.symbol or "<module>") == "<module>"
    return found.scenario(
        ObligationType.REGRESSION,
        f"Assert the outcome of {found.subject} that depends on its changed call",
        f"{found.changed}{own}",
        found.runs if module else f"{found.runs} with input that reaches the changed call",
        "What it returns, raises, or leaves is asserted, rather than the call itself",
    )


def _outside(change: Change) -> Change:
    """The change narrowed to the calls it changed that leave the process."""
    added = tuple(value for value in change.added if leaves_the_process(value))
    removed = tuple(value for value in change.removed if leaves_the_process(value))
    return replace(
        change, added=added, removed=removed, added_total=len(added), removed_total=len(removed)
    )


def _assignment_scenario(found: _Finding) -> Scenario:
    if found.category is BehaviorCategory.STATE_TRANSITION:
        return found.scenario(
            ObligationType.STATE,
            f"Verify the allowed transition {found.subject} now makes",
            found.changed,
            f"{found.runs} from a state that permits the transition",
            # Not "keeps it stable": a handler that counts what it is handed moves on each call.
            "The resulting state is asserted, and so is the state a repeated call leaves",
        )
    module = (found.evidence.symbol or "<module>") == "<module>"
    if module and found.change.assigned_names and found.readers:
        names = _quotes(found.change.assigned_names, len(found.change.assigned_names))
        readers = _quotes(found.readers, len(found.readers))
        verb = "does" if len(found.readers) == 1 else "do"
        return found.scenario(
            ObligationType.REGRESSION,
            f"Clarify and characterize what {readers} {verb} with the changed {names}",
            f"{found.changed}, which {readers} {'reads' if len(found.readers) == 1 else 'read'}",
            f"{_quote(found.readers[0])} is called with input the changed value decides",
            "Its result under the new value is asserted",
        )
    if (module or found.change.symbol_kind == "class") and found.change.assigned_names:
        names = _quotes(found.change.assigned_names, len(found.change.assigned_names))
        return found.scenario(
            ObligationType.REVIEW,
            f"Clarify and characterize the code that reads {names}",
            found.changed,
            "Code that reads the new value runs",
            "Its outcome with the new value is asserted",
        )
    return found.scenario(
        ObligationType.REGRESSION,
        f"Assert what {found.subject} computes from its changed assignment",
        found.changed,
        found.runs,
        "The value it computes from the assignment, as returned or stored, is asserted",
    )


def _order_scenario(found: _Finding) -> Scenario:
    before, after = _quote_pair(found.evidence.old or "", found.evidence.new or "")
    if found.change.kind == "condition_order_change":
        return found.scenario(
            ObligationType.REGRESSION,
            f"Exercise competing conditions in {found.subject}",
            f"{found.subject} now tests {after} in that order, where it tested {before}",
            "An input satisfies more than one of them",
            "The condition that now comes first decides the outcome, and that is asserted",
        )
    if found.change.kind == "call_order_change":
        return found.scenario(
            ObligationType.INTERACTION,
            f"Verify the sequence of the reordered calls in {found.subject}",
            f"{found.subject} now calls {after} in that order, where it called {before}",
            f"{found.runs} with the callees observed",
            "The calls occur in the new order, and the outcome that depends on it is asserted",
        )
    return found.scenario(
        ObligationType.REGRESSION,
        f"Verify the outcome of the reordered statements in {found.subject}",
        f"{found.subject} now runs {after} in that order, where it ran {before}",
        found.runs,
        "The outcome that depends on the order is asserted",
    )


@dataclass(frozen=True)
class _Outcome:
    """One thing an added callable returns or raises, and the branches that lead there: none
    when its code reaches it with no branch."""

    kind: str
    value: str
    branches: tuple[tuple[str, str], ...]


def outcomes_of(change: Change) -> list[_Outcome]:
    """What an added callable returns or raises, each distinct value once, in the order its code
    first reaches them and those it reaches with no branch last.

    Empty when that says nothing a test could tell from a call that returned at all: every exit a
    `return None`, or a stub's `raise NotImplementedError`.
    """
    grouped: dict[tuple[str, str], list[tuple[str, str]]] = {}
    plain: set[tuple[str, str]] = set()
    for kind, value, branch, test in change.exits:
        branches = grouped.setdefault((kind, value), [])
        if branch:
            branches.append((branch, test))
        else:
            plain.add((kind, value))
    outcomes = [
        _Outcome(kind, value, () if (kind, value) in plain else tuple(branches))
        for (kind, value), branches in grouped.items()
    ]
    outcomes.sort(key=lambda item: not item.branches)
    if all(item.kind == "return" and item.value == "None" for item in outcomes):
        return []
    if all(
        item.kind == "raise" and item.value.startswith("NotImplementedError") for item in outcomes
    ):
        return []
    return outcomes


def _branch(branch: str, test: str) -> str:
    if branch == "if":
        return f"when {_quote(test)}"
    if branch == "else":
        return f"unless {_quote(test)}"
    if branch == "except":
        return f"when {_quote(test)} is raised" if test else "when anything is raised"
    return f"in the case {_quote(test)}"


def _outcome_text(outcome: _Outcome, alone: bool) -> str:
    if outcome.kind == "raise":
        what = f"raises {_quote(outcome.value)}" if outcome.value else "raises again what it caught"
    else:
        what = f"returns {_quote(outcome.value)}"
    if not outcome.branches:
        return what if alone else f"{what} otherwise"
    phrases = list(dict.fromkeys(_branch(*item) for item in outcome.branches))
    shown = phrases[:MAX_QUOTED_VALUES]
    if more := len(phrases) - len(shown):
        shown.append(f"in {more} more case{'s' if more > 1 else ''}")
    return f"{what} {_series(shown)}"


def _outcomes_text(outcomes: Sequence[_Outcome], change: Change) -> str:
    """`it returns `None` when `x < 0`, or returns `x` otherwise`: at most `MAX_OUTCOMES`, the one
    reached with no branch kept, and how many exits there are when not all of them are named."""
    shown = list(outcomes)
    if len(shown) > MAX_OUTCOMES:
        shown = [*outcomes[: MAX_OUTCOMES - 1], outcomes[-1]]
    texts = [_outcome_text(item, len(outcomes) == 1) for item in shown]
    # Each outcome lists its branches with commas, so a comma comes before the `or` of the next.
    joined = f"{', '.join(texts[:-1])}, or {texts[-1]}" if len(texts) > 1 else texts[0]
    if len(shown) < len(outcomes) or change.exits_total > len(change.exits):
        return f"among its {change.exits_total} exits, it {joined}"
    return f"it {joined}"


def _lifecycle_scenario(found: _Finding) -> Scenario:
    added = found.change.kind == "symbol_added"
    shown = _quote(found.evidence.new if added else found.evidence.old)
    if found.change.ambiguous:
        return found.scenario(
            ObligationType.REVIEW,
            f"Clarify and characterize the contract of {found.subject}",
            f"{found.subject} was {'added' if added else 'removed'} without a reliable match to "
            f"another symbol: {shown}",
            "The cases its callers rely on are reviewed",
            "Their expected outcomes are documented and tested",
        )
    # What its own code returns and raises, and on which branch: what a test of it asserts. A
    # field test judged noise every obligation that said only "with a representative input".
    outcomes = outcomes_of(found.change) if added else []
    many = len(outcomes) > 1
    if added and found.callers and is_private(found.evidence.symbol or ""):
        # A private helper is tested through the code that calls it, whose input a test chooses.
        callers = _quotes(found.callers, len(found.callers))
        verb = "calls" if len(found.callers) == 1 else "call"
        caller = _quote(found.callers[0])
        if outcomes:
            return found.scenario(
                ObligationType.REGRESSION,
                f"Exercise {'each outcome of ' if many else ''}the new {found.subject} through "
                f"{callers}",
                f"{found.subject} is new: {shown}, and {callers} {verb} it; "
                f"{_outcomes_text(outcomes, found.change)}",
                f"{caller} runs with input that "
                f"{f'leads {found.subject} to each outcome' if many else f'reaches {found.subject}'}",
                f"What {caller} returns, raises, or leaves is asserted{' for each' if many else ''}",
            )
        return found.scenario(
            ObligationType.REGRESSION,
            f"Exercise the new {found.subject} through {callers}",
            f"{found.subject} is new: {shown}, and {callers} {verb} it",
            f"{caller} runs with input that reaches {found.subject}",
            f"What {caller} returns, raises, or leaves is asserted",
        )
    if added and outcomes:
        first = outcomes[0]
        if many:
            title = f"Assert each outcome of the new {found.subject}"
            then = "Each outcome is asserted for the input leading to it"
        elif first.kind == "raise":
            title = f"Assert what the new {found.subject} raises"
            then = "The exception it raises is asserted"
        else:
            title = f"Assert what the new {found.subject} returns"
            then = "What it returns is asserted exactly"
        if many:
            reached = "once for each outcome"
        elif first.branches:
            reached = "with input that reaches it"
        else:
            reached = "with a representative input"
        return found.scenario(
            ObligationType.POSITIVE,
            title,
            f"{found.subject} is new: {shown}; {_outcomes_text(outcomes, found.change)}",
            f"{found.called} {reached}",
            then,
        )
    if added:
        return found.scenario(
            ObligationType.POSITIVE,
            f"Characterize the new {found.subject}",
            f"{found.subject} is new: {shown}",
            f"{found.called} with a representative input",
            "Its result is asserted as its intended contract",
        )
    return found.scenario(
        ObligationType.NEGATIVE,
        f"Check what still depends on the removed {found.subject}",
        f"{found.subject} was removed: {shown}",
        "Code that called or imported it runs",
        "Nothing still depends on it, or its failure is the intended one",
    )


def _error_scenario(found: _Finding) -> Scenario:
    if found.change.kind == "raise_change" and found.change.removed_only and found.evidence.new:
        # A symbol that still raises something else: "what it does instead of raising" misled a
        # reviewer when the symbol went on raising a group of the same errors.
        return found.scenario(
            ObligationType.ERROR,
            f"Exercise the changed failure trigger in {found.subject}",
            f"{found.changed}; it still raises {_quote(found.evidence.new)}",
            "An input reaches the path the removed raise was on",
            "Which exception reaches the caller there, and what it carries, is asserted",
        )
    if found.change.kind == "raise_change":
        return found.scenario(
            ObligationType.ERROR,
            f"Exercise the changed failure trigger in {found.subject}",
            found.changed,
            "An input reaches that path",
            "What it does instead of raising is asserted"
            if found.change.removed_only
            else "The exception's type, and its message where callers read it, are asserted",
        )
    if found.change.removed_only:
        return found.scenario(
            ObligationType.ERROR,
            f"Verify what propagates now that {found.subject} no longer handles the exception",
            found.changed,
            "The code it guarded raises that exception",
            "What happens without the handler, the exception propagating or no longer arising, "
            "is asserted",
        )
    return found.scenario(
        ObligationType.ERROR,
        f"Verify recovery or propagation through the changed handler of {found.subject}",
        found.changed,
        "The code it guards raises that exception",
        "The error is recovered, wrapped, or propagated as intended, and that is asserted",
    )


def _loop_scenario(found: _Finding) -> Scenario:
    if found.category is BehaviorCategory.RETRY_TIMEOUT:
        return found.scenario(
            ObligationType.BOUNDARY,
            f"Exercise the exact retry or timeout limit of the changed loop in {found.subject}",
            found.changed,
            "The loop runs up to its stop condition",
            "The number of iterations, and the outcome at the limit, are asserted",
        )
    if found.change.removed_only:
        return found.scenario(
            ObligationType.REGRESSION,
            f"Assert what {found.subject} does without its former loop",
            found.changed,
            f"{found.runs} over no item, one item, and several",
            "The outcome of each is asserted",
        )
    return found.scenario(
        ObligationType.REGRESSION,
        f"Exercise the changed loop in {found.subject} with no item, one, and several",
        found.changed,
        f"{found.runs} over no item, one item, and several",
        "The outcome of each is asserted",
    )


def _decorator_scenario(found: _Finding) -> Scenario:
    if found.category is BehaviorCategory.AUTHORIZATION:
        return found.scenario(
            ObligationType.NEGATIVE,
            f"Verify an allowed and a denied principal at the changed decorators of "
            f"{found.subject}",
            found.changed,
            "A principal they admit, and one they do not, call it",
            "The allowed one gets its outcome, and the denied one is refused without the "
            "protected side effect",
        )
    return found.scenario(
        ObligationType.REGRESSION,
        f"Assert what the changed decorators of {found.subject} add",
        found.changed,
        f"{found.called} through its decorators",
        "The behavior they add, such as access, caching, or registration, is asserted",
    )


def _unnamed_scenario(found: _Finding) -> Scenario:
    """A change no structural rule names, told by the lines of the symbol's code that changed."""
    change = found.change
    if (change.removed_total, change.added_total) == (1, 1) and change.removed and change.added:
        removed, added = _quote_pair(change.removed[0], change.added[0])
    else:
        added = _quotes(change.added, change.added_total)
        removed = _quotes(change.removed, change.removed_total)
    unnamed = f"{found.subject} changed in a way no rule names"
    if change.added and change.removed:
        given = f"{unnamed}: it now reads {added} where it read {removed}"
    elif change.added:
        given = f"{unnamed}: it now also reads {added}"
    else:
        given = f"{unnamed}: it no longer reads {removed}"
    module = (found.evidence.symbol or "<module>") == "<module>"
    return found.scenario(
        ObligationType.REVIEW,
        f"Clarify and characterize what the changed lines of {found.subject} do",
        given,
        found.runs if module else f"{found.runs} with input that reaches the changed lines",
        "What the module defines or exposes is asserted"
        if module
        else "What it returns, raises, or leaves there is asserted",
    )


def _described_scenario(found: _Finding) -> Scenario:
    """What an agent does with a tool whose schema changed only how it describes the tool.

    No code computes with a description, so asserting what the tool's handler returns tests what
    it returned before: the schema's agent reads the new text before it calls the tool, and what
    it then tells the user, waits for, and calls is what changed.
    """
    names = found.change.assigned_names
    described = _quotes(names, len(names)) if names else found.subject
    return found.scenario(
        ObligationType.INTERACTION,
        f"Check that an agent follows the changed description in {described}",
        f"{found.changed}: only schema descriptions changed, which an agent given the tool reads "
        "before it calls it, and no code computes with",
        "An agent is given the tool under the new description, with a request the changed text "
        "speaks to",
        "What the agent tells the user, asks before it acts, and calls follows the new text",
    )


def _scenarios_of(found: _Finding) -> tuple[Scenario, ...]:
    kind, change = found.change.kind, found.change
    if change.described_only:
        return (_described_scenario(found),)
    if kind == "signature_change":
        return _default_scenarios(found) if change.defaults else (_signature_scenario(found),)
    if kind in {"comparison_change", "condition_change"}:
        if change.boundary and found.category in {
            BehaviorCategory.BOUNDARY,
            BehaviorCategory.RETRY_TIMEOUT,
        }:
            return _boundary_scenarios(found)
        return (_condition_scenario(found),)
    if kind == "structural_refactor":
        return (
            found.scenario(
                ObligationType.REGRESSION,
                f"Preserve characterized behavior of {found.subject}",
                f"Representative inputs that reached {found.subject} before the change",
                "The refactored code runs",
                "The outcomes match those before the change",
            ),
        )
    if kind == "return_change":
        return (
            found.scenario(
                ObligationType.REGRESSION,
                f"Assert the consumer-visible contract of what {found.subject} returns",
                found.changed,
                f"{found.called} with input that reaches the changed return",
                "The returned value, its type, fields, and shape included, is asserted exactly",
            ),
        )
    if kind == "context_manager_change":
        return (
            found.scenario(
                ObligationType.REGRESSION,
                f"Assert what {found.subject} acquires and releases in its changed with block",
                found.changed,
                f"{found.runs} once normally and once with the block raising",
                "What it acquires is released both times, and the outcome is asserted",
            ),
        )
    builders = {
        "call_change": _call_scenario,
        "assignment_change": _assignment_scenario,
        "raise_change": _error_scenario,
        "exception_handler_change": _error_scenario,
        "loop_change": _loop_scenario,
        "decorator_change": _decorator_scenario,
        "symbol_added": _lifecycle_scenario,
        "symbol_removed": _lifecycle_scenario,
        "unknown_structure": _unnamed_scenario,
        **dict.fromkeys(ORDER_KINDS, _order_scenario),
    }
    return (builders[kind](found),)


def _primary(
    behavior: BehaviorChange, changes: Mapping[str, Change]
) -> tuple[Evidence, Change] | None:
    """The evidence that best says what the finding is about, with what its delta changed.

    Evidence on the finding's own symbol comes first, then the kinds its category is about.
    """
    described = [(item, changes[item.id]) for item in behavior.evidence if item.id in changes]
    if not described:
        return None
    own = (behavior.evidence[0].path, behavior.evidence[0].symbol)
    order = (*_PREFERRED.get(behavior.category, ()), *_GENERAL)

    def rank(pair: tuple[Evidence, Change]) -> tuple[bool, int]:
        item, change = pair
        return (item.path, item.symbol) != own, order.index(change.kind)

    return min(described, key=rank)


@dataclass(frozen=True)
class Context:
    """What the call graph and the other findings say around a finding, for its scenario."""

    # The symbols whose code reads a module-level variable the finding assigns.
    readers: tuple[str, ...] = ()
    # The symbols whose code calls the symbol the finding adds.
    callers: tuple[str, ...] = ()
    # The findings' symbols, as (path, symbol), whose obligation speaks for this one when its
    # own would say little: the code calling a private symbol it adds, the code that stopped
    # calling one it removes, and the other symbols of its class.
    related: tuple[tuple[str, str], ...] = ()
    # For a function that changed only as the functions nested in it did, those of them whose
    # findings are in its category, in its module: they say what changed, and a test reaches
    # them only through it.
    nested: tuple[str, ...] = ()


NO_CONTEXT = Context()


def is_private(symbol: str) -> bool:
    """Whether a qualified name has a part only its own module uses: `_x`, not `__init__`."""
    return any(
        part.startswith("_") and not (part.startswith("__") and part.endswith("__"))
        for part in symbol.split(".")
    )


def evidence_scenarios(
    behavior: BehaviorChange, changes: Mapping[str, Change], context: Context | None = None
) -> tuple[Scenario, ...]:
    """The scenarios a finding's evidence supports: none when no Python delta describes it, but
    for a function that changed only as the functions nested in it did, which has one naming them.

    One scenario says what to run, what changed, and what to assert. A changed default has a
    second, for the former value passed explicitly, and a changed boundary one for either side.
    """
    context = context or NO_CONTEXT
    relevance = CATEGORY_PROFILES[behavior.category].scenarios[0].relevance
    picked = _primary(behavior, changes)
    if picked is None:
        return (_nested_scenario(behavior, context.nested, relevance),) if context.nested else ()
    evidence, change = picked
    scenarios = _scenarios_of(_found(behavior, evidence, change, relevance, context))
    beside = _beside_the_return(behavior, changes, evidence, change)
    if beside is None:
        return scenarios
    # What feeds the changed return names inputs its expression alone does not: a condition's
    # two outcomes. It was a finding and an obligation of its own until `SDW-RULE-OUTPUT-FLOW`
    # joined it to the return's.
    second = _found(behavior, *beside, max(0, relevance - SECOND_SCENARIO_RELEVANCE_DROP), context)
    return (*scenarios, *_scenarios_of(second)[:1])


def _found(
    behavior: BehaviorChange,
    evidence: Evidence,
    change: Change,
    relevance: int,
    context: Context,
) -> _Finding:
    subject = _subject(evidence)
    return _Finding(
        category=behavior.category,
        evidence=evidence,
        change=change,
        subject=subject,
        changed=_what_changed(subject, evidence, change) if change.kind in _VERBS else "",
        relevance=relevance,
        readers=context.readers,
        callers=context.callers,
    )


def _beside_the_return(
    behavior: BehaviorChange, changes: Mapping[str, Change], evidence: Evidence, change: Change
) -> tuple[Evidence, Change] | None:
    """In an output contract's finding written from its changed return, the change on the same
    symbol that feeds the value, in the order of `_GENERAL`, if there is one."""
    if behavior.category is not BehaviorCategory.OUTPUT_CONTRACT or change.kind != "return_change":
        return None
    own = (evidence.path, evidence.symbol)
    feeding = [
        (item, changes[item.id])
        for item in behavior.evidence
        if item.id in changes
        and (item.path, item.symbol) == own
        and changes[item.id].kind not in {"return_change", "signature_change"}
    ]
    if not feeding:
        return None
    return min(feeding, key=lambda pair: _GENERAL.index(pair[1].kind))


def _nested_scenario(behavior: BehaviorChange, nested: Sequence[str], relevance: int) -> Scenario:
    """A function that changed only as the functions nested in it did, which a test reaches only
    through it: their findings say what changed, and its scenario where a test reaches it.

    Its own code reads the same, so its evidence has nothing to quote, and its category's general
    scenarios named neither it nor them. The title names the function alone, as the nested
    functions' names could take it past its bound.
    """
    subject = _subject(behavior.evidence[0])
    names = _quotes(nested, len(nested))
    noun = "function" if len(nested) == 1 else "functions"
    return _scenario(
        ObligationType.REGRESSION,
        f"Assert the outcome of {subject} that depends on its changed nested {noun}",
        f"{subject} changed only as its nested {noun} {names} did",
        f"{subject} runs with input that reaches {names}",
        f"What changed in {names} is asserted in what {subject} returns, raises, or leaves",
        relevance,
    )


def primary_kind(behavior: BehaviorChange, changes: Mapping[str, Change]) -> str | None:
    """The kind of the delta a finding's scenario is written from, or None for none."""
    picked = _primary(behavior, changes)
    return picked[1].kind if picked is not None else None


WEAK_CALL_CATEGORIES = frozenset(
    {BehaviorCategory.DEPENDENCY_INTERACTION, BehaviorCategory.UNKNOWN}
)
LIFECYCLE_KINDS = frozenset({"symbol_added", "symbol_removed"})


def weakly_anchored(
    behavior: BehaviorChange, changes: Mapping[str, Change], context: Context | None = None
) -> bool:
    """Whether the finding's scenario names nothing a test could check beyond its symbol's result.

    A call that only computes a value, an assignment in a function, and a module-level one whose
    readers are unknown say where to look but not what to assert: the second blind review judged
    all six of the last two kinds noise. So, in the third, did a private symbol added or removed:
    only its own module uses it, and what to assert is what its callers do. A private callable
    added with something to return or raise is the exception: its own obligation lists that (see
    `outcomes_of`), which nothing its callers' say does. And a function that
    changed only as the functions nested in it did says where a test reaches them, while their
    findings say what changed. Another finding's scenario, on the same symbol or a related one,
    speaks better, and `generate_obligations` links the finding to it instead.
    """
    picked = _primary(behavior, changes)
    if picked is None:
        return bool((context or NO_CONTEXT).nested)
    evidence, change = picked
    if change.kind == "call_change":
        calls = (*change.added, *change.removed)
        return behavior.category in WEAK_CALL_CATEGORIES and not any(
            leaves_the_process(call) for call in calls
        )
    if change.kind == "assignment_change":
        module = (evidence.symbol or "<module>") == "<module>"
        readers = (context or NO_CONTEXT).readers
        return behavior.category is not BehaviorCategory.STATE_TRANSITION and not (
            module and change.assigned_names and readers
        )
    # A private callable added with something to assert keeps its own obligation, which lists
    # it: the code calling it says nothing of what it returns, as the field test's `_blend` did.
    return (
        change.kind in LIFECYCLE_KINDS
        and is_private(evidence.symbol or "")
        and not outcomes_of(change)
    )


def _node(behavior: BehaviorChange) -> tuple[str, str]:
    first = behavior.evidence[0]
    return first.path, first.symbol or "<module>"


def _callee(call: str) -> str:
    """A call's last name, as `extract` records it: `close` for `self.handler.close()`."""
    return call.split("(", 1)[0].rpartition(".")[2]


def _readers_of(
    behavior: BehaviorChange, changes: Mapping[str, Change], graph: CallGraph
) -> tuple[str, ...]:
    """The symbols whose code reads a module-level variable the finding's assignments write.

    The call graph knows them, in the variable's module and in the modules importing it that it
    indexes. Naming them tells a test what to call: top-level code runs as its module is
    imported, which tests nothing.
    """
    variables: set[tuple[str, str]] = set()
    for item in behavior.evidence:
        change = changes.get(item.id)
        if change is not None and change.kind == "assignment_change":
            if (item.symbol or "<module>") == "<module>":
                variables.update((item.path, name) for name in change.assigned_names)
    nodes = {node for variable in variables for node in graph.readers(*variable)}
    here = {path for path, _ in variables}
    ordered = sorted(nodes, key=lambda node: (node[0] not in here, node[0], node[1]))
    return tuple(dict.fromkeys(qualified for _, qualified in ordered))


def contexts_of(
    behaviors: Sequence[BehaviorChange], changes: Mapping[str, Change], graph: CallGraph | None
) -> dict[str, Context]:
    """By finding ID, what the call graph and the other findings say around each finding."""
    nodes = {behavior.id: _node(behavior) for behavior in behaviors}
    found: dict[str, Context] = {}
    for behavior in behaviors:
        path, symbol = nodes[behavior.id]
        kind = primary_kind(behavior, changes)
        readers = _readers_of(behavior, changes, graph) if graph is not None else ()
        callers: tuple[str, ...] = ()
        related: list[tuple[str, str]] = []
        if kind == "symbol_added" and graph is not None:
            users = sorted(graph.users((path, symbol)), key=lambda node: (node[0] != path, node))
            # The code an obligation says calls it spells its name: `users` reaches it from any
            # name that reads alike, which serves the candidate tests but no such claim.
            spelled = graph.calling((path, symbol))
            callers = tuple(dict.fromkeys(node[1] for node in users if node in spelled))
            related.extend(users)
        if kind == "symbol_removed":
            owner, _, name = symbol.rpartition(".")
            # A removed constructor was called by its class's name.
            name = owner.rpartition(".")[2] if name == "__init__" and owner else name
            for other in behaviors:
                if other is behavior or nodes[other.id][0] != path:
                    continue
                if any(
                    _callee(call) == name
                    for item in other.evidence
                    if (change := changes.get(item.id)) is not None and change.kind == "call_change"
                    for call in change.removed
                ):
                    related.append(nodes[other.id])
        if kind in LIFECYCLE_KINDS and is_private(symbol):
            owner = symbol.rpartition(".")[0]
            if owner:
                related.extend(
                    sorted(
                        node
                        for node in set(nodes.values())
                        if node[0] == path
                        and node != (path, symbol)
                        and (node[1] == owner or node[1].startswith(f"{owner}."))
                    )
                )
        nested = _nested_in(behavior, kind, behaviors, nodes)
        if readers or callers or related or nested:
            found[behavior.id] = Context(readers, callers, tuple(dict.fromkeys(related)), nested)
    return found


def _nested_in(
    behavior: BehaviorChange,
    kind: str | None,
    behaviors: Sequence[BehaviorChange],
    nodes: Mapping[str, tuple[str, str]],
) -> tuple[str, ...]:
    """The nested functions whose findings in its category a function's finding took, as
    `SDW-RULE-NESTED` makes one: a category over the unknown change "body changed" alone, with no
    line of the function's own code to quote."""
    if kind is not None or behavior.category in NAMING_NOTHING:
        return ()
    if any(item.kind != "unknown_structure" for item in behavior.evidence):
        return ()
    path, symbol = nodes[behavior.id]
    return tuple(
        sorted(
            {
                nodes[other.id][1]
                for other in behaviors
                if other.category is behavior.category
                and nodes[other.id][0] == path
                and nodes[other.id][1].startswith(f"{symbol}.")
            }
        )
    )
