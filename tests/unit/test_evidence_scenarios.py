"""A Python finding's obligations quote what its evidence changed: what to run, what changed, and
what to assert. A finding no Python delta describes keeps its category's scenarios."""

from __future__ import annotations

import pytest

from semantic_diff_weaver.ast_diff.analyze import analyze_ast
from semantic_diff_weaver.ast_diff.limits import MAX_CHANGED_VALUES
from semantic_diff_weaver.ast_diff.types import StructuralDelta
from semantic_diff_weaver.evidence_scenarios import (
    MAX_QUOTE_CHARS,
    SECOND_SCENARIO_RELEVANCE_DROP,
    Change,
    Context,
    changes_by_evidence,
    contexts_of,
    describe_change,
    evidence_scenarios,
    is_private,
    leaves_the_process,
    weakly_anchored,
)
from semantic_diff_weaver.models import (
    BehaviorCategory,
    BehaviorChange,
    Evidence,
    ObligationType,
    Origin,
    Presentation,
    RiskLabel,
    ScoreExplanation,
    WeaverConfig,
)
from semantic_diff_weaver.obligations import generate_obligations
from semantic_diff_weaver.semantic_candidates import SemanticCandidate, build_candidates
from semantic_diff_weaver.semantic_interpreter import SuggestedScenario
from semantic_diff_weaver.source import SourceHunk, SourceRevisionPair
from semantic_diff_weaver.taxonomy import CATEGORY_PROFILES, Scenario
from semantic_diff_weaver.textutil import split_code_spans

WIDE_HUNK = SourceHunk(id="hunk-001", old_start=1, old_count=400, new_start=1, new_count=400)


def _behavior(candidate: SemanticCandidate, index: int = 1) -> BehaviorChange:
    return BehaviorChange(
        id=f"bc-{index:03d}",
        category=candidate.category,
        summary=candidate.summary,
        observable_impact=candidate.observable_impact,
        risk=RiskLabel.MEDIUM,
        risk_score=50,
        confidence=0.8,
        evidence=list(candidate.evidence),
        assumptions=list(candidate.assumptions),
        origin=Origin.DETERMINISTIC,
        score_explanation=ScoreExplanation(
            behavioral_impact=50,
            critical_path_weight=0,
            test_gap_weight=90,
            change_surface_weight=10,
        ),
    )


def _findings(old: str, new: str) -> tuple[list[BehaviorChange], dict[str, Change]]:
    deltas = analyze_ast(
        [
            SourceRevisionPair(
                path="m.py",
                old_path="m.py",
                new_path="m.py",
                hunks=(WIDE_HUNK,),
                old_text=old,
                new_text=new,
            )
        ]
    ).deltas
    candidates = build_candidates(deltas)
    return [
        _behavior(item, index) for index, item in enumerate(candidates, start=1)
    ], changes_by_evidence(deltas)


def _scenarios(old: str, new: str, category: BehaviorCategory) -> tuple[Scenario, ...]:
    behaviors, changes = _findings(old, new)
    (behavior,) = [item for item in behaviors if item.category is category]
    return evidence_scenarios(behavior, changes)


def _code(text: str) -> list[str]:
    return [part for part, code in split_code_spans(text) if code]


def test_a_changed_default_asks_for_the_omitted_argument_and_for_the_former_value() -> None:
    omitted, explicit = _scenarios(
        "def run(limit=2):\n    return limit\n",
        "def run(limit=3):\n    return limit\n",
        BehaviorCategory.DEFAULT_BEHAVIOR,
    )
    assert omitted.title == "Verify omitted-input behavior of `run` without `limit`"
    assert omitted.given == "`limit` is omitted, so its new default `3` applies where `2` did"
    assert omitted.when == "`run` is called"
    assert omitted.then == "The result is the one the default `3` gives, not `2`"
    assert omitted.type is ObligationType.REGRESSION
    assert explicit.title == "Verify `run` with `limit` passed as the former default `2`"
    assert explicit.type is ObligationType.POSITIVE
    # The second scenario of a finding follows its first.
    assert explicit.relevance == omitted.relevance - SECOND_SCENARIO_RELEVANCE_DROP


def test_a_default_that_did_not_exist_has_no_former_value_to_pass() -> None:
    (omitted,) = _scenarios(
        "def run(limit):\n    return limit\n",
        "def run(limit=3):\n    return limit\n",
        BehaviorCategory.DEFAULT_BEHAVIOR,
    )
    assert omitted.given == "`limit` is omitted, which its new default `3` allows"


def test_a_removed_default_now_makes_the_omitted_argument_fail() -> None:
    omitted, explicit = _scenarios(
        "def run(limit=3):\n    return limit\n",
        "def run(limit):\n    return limit\n",
        BehaviorCategory.DEFAULT_BEHAVIOR,
    )
    assert omitted.then == "The call now fails explicitly where it applied `3`"
    assert "`3`" in explicit.title


def test_a_constructor_is_reached_by_instantiating_its_class() -> None:
    omitted, _ = _scenarios(
        "class C:\n    def __init__(self, size=1):\n        self.size = size\n",
        "class C:\n    def __init__(self, size=2):\n        self.size = size\n",
        BehaviorCategory.DEFAULT_BEHAVIOR,
    )
    assert omitted.when == "`C` is instantiated"


def test_a_changed_boundary_asks_for_the_boundary_and_for_either_side() -> None:
    exact, sides = _scenarios(
        "def allowed(x):\n    return x < 5\n",
        "def allowed(x):\n    return x <= 5\n",
        BehaviorCategory.BOUNDARY,
    )
    assert exact.title == "Exercise the exact changed boundary in `allowed`"
    # The comparison is quoted as code shows it, without the operators extraction records.
    assert exact.given == (
        "`allowed` now compares `x <= 5` where it compared `x < 5`; an input sits exactly at "
        "that boundary"
    )
    assert sides.title == "Exercise either side of the changed boundary in `allowed`"
    assert sides.relevance == exact.relevance - SECOND_SCENARIO_RELEVANCE_DROP


def test_a_removed_boundary_has_no_boundary_left_to_sit_on() -> None:
    (only,) = _scenarios(
        "def f(x):\n    if x < 5:\n        x = 0\n    return x\n",
        "def f(x):\n    return x\n",
        BehaviorCategory.BOUNDARY,
    )
    assert only.given == (
        "`f` no longer compares `x < 5`; inputs sit on each side of that former boundary"
    )


def test_a_retry_limit_keeps_its_name() -> None:
    exact, _ = _scenarios(
        "def retry(attempts):\n    while attempts < 3:\n        attempts += 1\n"
        "    return attempts\n",
        "def retry(attempts):\n    while attempts <= 3:\n        attempts += 1\n"
        "    return attempts\n",
        BehaviorCategory.RETRY_TIMEOUT,
    )
    assert exact.title == "Exercise the exact retry or timeout limit of `retry`"


def test_a_changed_condition_asks_for_both_of_its_outcomes() -> None:
    (scenario,) = _scenarios(
        "def f(x):\n    if x.flag:\n        return 1\n    return 0\n",
        "def f(x):\n    if x.other:\n        return 1\n    return 0\n",
        BehaviorCategory.OUTPUT_CONTRACT,
    )
    assert scenario.title == "Exercise both outcomes of the changed condition in `f`"
    assert scenario.given == "`f` now tests `x.other` where it tested `x.flag`"
    assert scenario.when == (
        "`f` runs once with input that makes the condition true and once with input that makes "
        "it false"
    )


def test_a_removed_condition_asks_for_the_cases_it_told_apart() -> None:
    (scenario,) = _scenarios(
        "def f(x):\n    if x.flag:\n        x.go()\n    return 0\n",
        "def f(x):\n    x.go()\n    return 0\n",
        BehaviorCategory.UNKNOWN,
    )
    assert scenario.title == "Exercise the cases the removed condition told apart in `f`"
    assert scenario.given == "`f` no longer tests `x.flag`"


@pytest.mark.parametrize(
    ("old", "new", "category", "title"),
    [
        (
            "def allowed(user):\n    if user.is_admin:\n        return 1\n    return 0\n",
            "def allowed(user):\n    if user.is_owner:\n        return 1\n    return 0\n",
            BehaviorCategory.AUTHORIZATION,
            "Verify an allowed and a denied principal at the changed guard of `allowed`",
        ),
        (
            "def check(x):\n    if is_valid(x):\n        return 1\n    return 0\n",
            "def check(x):\n    if is_valid(x, strict=True):\n        return 1\n    return 0\n",
            BehaviorCategory.VALIDATION,
            "Verify newly accepted and newly rejected input at the changed validation of `check`",
        ),
    ],
)
def test_a_guard_names_who_or_what_it_lets_through(
    old: str, new: str, category: BehaviorCategory, title: str
) -> None:
    (scenario,) = _scenarios(old, new, category)
    assert scenario.title == title


def test_a_changed_dependency_call_asks_for_its_arguments() -> None:
    (scenario,) = _scenarios(
        "def fetch(client, x):\n    return client.fetch(x)\n",
        "def fetch(client, x):\n    return client.fetch(x, strict=True)\n",
        BehaviorCategory.DEPENDENCY_INTERACTION,
    )
    assert scenario.title == "Assert the arguments of the changed call in `fetch`"
    assert scenario.given == (
        "`fetch` now calls `client.fetch(x, strict=True)` where it called `client.fetch(x)`"
    )
    assert scenario.when == "`fetch` runs, with the call observed through a stub or a spy"


def test_a_removed_call_out_of_the_process_asks_what_takes_its_place() -> None:
    (scenario,) = _scenarios(
        "def f(sock):\n    sock.sendall(b'ping')\n    return 1\n",
        "def f(sock):\n    return 1\n",
        BehaviorCategory.DEPENDENCY_INTERACTION,
    )
    assert scenario.title == "Assert what `f` does without its former call"
    assert scenario.given == "`f` no longer calls `sock.sendall(b'ping')`"


def test_a_call_that_only_computes_a_value_asks_for_the_outcome_not_a_spy() -> None:
    (scenario,) = _scenarios(
        "def f(client):\n    client.ping()\n    return 1\n",
        "def f(client):\n    return 1\n",
        BehaviorCategory.DEPENDENCY_INTERACTION,
    )
    assert scenario.title == "Assert the outcome of `f` that depends on its changed call"
    assert scenario.given == "`f` no longer calls `client.ping()`"
    assert scenario.when == "`f` runs with input that reaches the changed call"
    assert scenario.then == (
        "What it returns, raises, or leaves is asserted, rather than the call itself"
    )


def test_a_side_effect_call_asks_whether_the_effect_happens() -> None:
    (scenario,) = _scenarios(
        "def finish(user):\n    return user\n",
        "def finish(user):\n    notify(user)\n    return user\n",
        BehaviorCategory.SIDE_EFFECT,
    )
    assert scenario.title == "Verify the side-effect occurrence of the changed call in `finish`"
    assert scenario.given == "`finish` now also calls `notify(user)`"


def test_a_call_to_the_project_s_own_code_asks_what_it_produces() -> None:
    change = Change(
        kind="call_change",
        removed=("helper(x)",),
        added=("helper(x, 1)",),
        removed_total=1,
        added_total=1,
        internal=True,
    )
    (scenario,) = evidence_scenarios(
        _manual(BehaviorCategory.UNKNOWN, "call_change"), {"ev-001": change}
    )
    assert scenario.title == "Assert the outcome of `f` that depends on its changed call"
    assert scenario.given.endswith(", its own project's code")


@pytest.mark.parametrize(
    ("old", "new", "title", "then"),
    [
        (
            "def run(x):\n    raise ValueError()\n",
            "def run(x):\n    raise TypeError()\n",
            "Exercise the changed failure trigger in `run`",
            "The exception's type, and its message where callers read it, are asserted",
        ),
        (
            "def run(x):\n    if x:\n        raise ValueError()\n    return x\n",
            "def run(x):\n    if x:\n        return None\n    return x\n",
            "Exercise the changed failure trigger in `run`",
            "What it does instead of raising is asserted",
        ),
        (
            "def f(g):\n    return g()\n",
            "def f(g):\n    try:\n        return g()\n    except KeyError:\n        return None\n",
            "Verify recovery or propagation through the changed handler of `f`",
            "The error is recovered, wrapped, or propagated as intended, and that is asserted",
        ),
        (
            "def f(g):\n    try:\n        return g()\n    except KeyError:\n        return None\n",
            "def f(g):\n    return g()\n",
            "Verify what propagates now that `f` no longer handles the exception",
            "What happens without the handler, the exception propagating or no longer arising, "
            "is asserted",
        ),
    ],
)
def test_an_error_path_asks_for_the_exception_or_what_replaced_it(
    old: str, new: str, title: str, then: str
) -> None:
    (scenario,) = _scenarios(old, new, BehaviorCategory.ERROR_HANDLING)
    assert scenario.title == title
    assert scenario.then == then
    assert scenario.type is ObligationType.ERROR


def test_a_changed_return_quotes_both_values() -> None:
    (scenario,) = _scenarios(
        "def result():\n    return {'old': 1}\n",
        "def result():\n    return {'new': 1}\n",
        BehaviorCategory.OUTPUT_CONTRACT,
    )
    assert scenario.title == "Assert the consumer-visible contract of what `result` returns"
    assert scenario.given == "`result` now returns `{'new': 1}` where it returned `{'old': 1}`"


def test_a_value_removed_twice_is_quoted_once() -> None:
    scenario, beside = _scenarios(
        "def f(x):\n    if x:\n        return None\n    return None\n",
        "def f(x):\n    return x\n",
        BehaviorCategory.OUTPUT_CONTRACT,
    )
    assert scenario.given == "`f` now returns `x` where it returned `None`"
    # The removed condition fed the return, and joins its finding with its own scenario.
    assert beside.title == "Exercise the cases the removed condition told apart in `f`"
    assert beside.relevance == scenario.relevance - 10


@pytest.mark.parametrize(
    ("old", "new", "category", "title"),
    [
        (
            "def move(order):\n    order.state = 'old'\n",
            "def move(order):\n    order.state = 'new'\n",
            BehaviorCategory.STATE_TRANSITION,
            "Verify the allowed transition `move` now makes",
        ),
        (
            "ROOTS = ('core', 'tools')\n",
            "ROOTS = ('core', 'tools', 'reports')\n",
            BehaviorCategory.UNKNOWN,
            "Clarify and characterize the code that reads `ROOTS`",
        ),
        (
            "class Preview:\n    a = 1\n",
            "class Preview:\n    a = 1\n    b = 2\n",
            BehaviorCategory.UNKNOWN,
            "Clarify and characterize the code that reads `b`",
        ),
        (
            "def f():\n    x = 1\n    return x\n",
            "def f():\n    x = 2\n    return x\n",
            BehaviorCategory.OUTPUT_CONTRACT,
            "Assert what `f` computes from its changed assignment",
        ),
    ],
)
def test_an_assignment_asks_for_what_reads_or_computes_from_it(
    old: str, new: str, category: BehaviorCategory, title: str
) -> None:
    (scenario,) = _scenarios(old, new, category)
    assert scenario.title == title


def test_a_module_s_own_code_runs_as_the_module_is_imported() -> None:
    (scenario,) = _scenarios("setup(1)\n", "setup(2)\n", BehaviorCategory.DEPENDENCY_INTERACTION)
    assert scenario.given == (
        "The top-level code of `m.py` now calls `setup(2)` where it called `setup(1)`"
    )
    # It takes no input to reach the call with.
    assert scenario.when == "The top-level code of `m.py` runs as the module is imported"
    (spied,) = _scenarios(
        "requests.get('https://example.invalid/a')\n",
        "requests.get('https://example.invalid/b')\n",
        BehaviorCategory.DEPENDENCY_INTERACTION,
    )
    assert spied.when == (
        "The top-level code of `m.py` runs as the module is imported, with the call observed "
        "through a stub or a spy"
    )


@pytest.mark.parametrize(
    ("old", "new", "category", "title"),
    [
        (
            "def f(items):\n    for x in items:\n        print(x)\n",
            "def f(items):\n    for x in reversed(items):\n        print(x)\n",
            BehaviorCategory.UNKNOWN,
            "Exercise the changed loop in `f` with no item, one, and several",
        ),
        (
            "def f(p):\n    with open(p) as h:\n        return h.read()\n",
            "def f(p):\n    with open(p, 'rb') as h:\n        return h.read()\n",
            BehaviorCategory.OUTPUT_CONTRACT,
            "Assert what `f` acquires and releases in its changed with block",
        ),
        (
            "@cache\ndef f():\n    return 1\n",
            "@lru_cache\ndef f():\n    return 1\n",
            BehaviorCategory.UNKNOWN,
            "Assert what the changed decorators of `f` add",
        ),
        (
            "def f(x):\n    if a(x):\n        return 1\n"
            "    if b(x):\n        return 2\n    return 3\n",
            "def f(x):\n    if b(x):\n        return 2\n"
            "    if a(x):\n        return 1\n    return 3\n",
            BehaviorCategory.ORDERING,
            "Exercise competing conditions in `f`",
        ),
        (
            "def run():\n    first()\n    second()\n",
            "def run():\n    second()\n    first()\n",
            BehaviorCategory.ORDERING,
            "Verify the sequence of the reordered calls in `run`",
        ),
        (
            "def f():\n    return 1\n",
            "def f():\n    return 1\n\ndef g(x):\n    return x\n",
            BehaviorCategory.UNKNOWN,
            "Characterize the new `g`",
        ),
        (
            "def f():\n    return 1\n\ndef g(x):\n    return x\n",
            "def f():\n    return 1\n",
            BehaviorCategory.UNKNOWN,
            "Check what still depends on the removed `g`",
        ),
        (
            "def f(a):\n    return a\n",
            "def f(a, b):\n    return a\n",
            BehaviorCategory.OUTPUT_CONTRACT,
            "Check former contract behavior of `f`",
        ),
        (
            "def render(value: int) -> int:\n    return value\n",
            "def render(value: int) -> str:\n    return value\n",
            BehaviorCategory.OUTPUT_CONTRACT,
            "Check the consumer-visible contract of the annotations of `render`",
        ),
    ],
)
def test_each_kind_of_change_names_its_own_scenario(
    old: str, new: str, category: BehaviorCategory, title: str
) -> None:
    (scenario,) = _scenarios(old, new, category)
    assert scenario.title == title


def _manual(category: BehaviorCategory, kind: str, *, symbol: str = "f") -> BehaviorChange:
    candidate = SemanticCandidate(
        path="m.py",
        symbol=symbol,
        category=category,
        summary="Changed.",
        observable_impact="Something differs.",
        evidence=(
            Evidence(id="ev-001", path="m.py", symbol=symbol, kind=kind, old="a; b", new="b; a"),
        ),
        confidence_baseline=0.5,
    )
    return _behavior(candidate)


@pytest.mark.parametrize(
    ("change", "title"),
    [
        (
            Change(kind="statement_order_change"),
            "Verify the outcome of the reordered statements in `f`",
        ),
        (Change(kind="structural_refactor"), "Preserve characterized behavior of `f`"),
        (
            Change(kind="symbol_added", ambiguous=True),
            "Clarify and characterize the contract of `f`",
        ),
        (
            Change(kind="loop_change", removed=("for x in xs",), removed_total=1),
            "Assert what `f` does without its former loop",
        ),
    ],
)
def test_scenarios_of_rarer_changes(change: Change, title: str) -> None:
    (scenario,) = evidence_scenarios(
        _manual(BehaviorCategory.UNKNOWN, change.kind), {"ev-001": change}
    )
    assert scenario.title == title


@pytest.mark.parametrize(
    ("category", "kind", "title"),
    [
        (
            BehaviorCategory.AUTHORIZATION,
            "call_change",
            "Verify an allowed and a denied principal through the changed call in `f`",
        ),
        (
            BehaviorCategory.VALIDATION,
            "call_change",
            "Verify newly accepted and newly rejected input through the changed call in `f`",
        ),
        (
            BehaviorCategory.RETRY_TIMEOUT,
            "call_change",
            "Exercise the exact retry or timeout limit through the changed call in `f`",
        ),
        (
            BehaviorCategory.RETRY_TIMEOUT,
            "condition_change",
            "Exercise the exact retry or timeout limit of the changed predicate of `f`",
        ),
        (
            BehaviorCategory.RETRY_TIMEOUT,
            "loop_change",
            "Exercise the exact retry or timeout limit of the changed loop in `f`",
        ),
        (
            BehaviorCategory.AUTHORIZATION,
            "decorator_change",
            "Verify an allowed and a denied principal at the changed decorators of `f`",
        ),
    ],
)
def test_the_category_words_a_scenario_of_the_kind_it_found(
    category: BehaviorCategory, kind: str, title: str
) -> None:
    change = Change(kind=kind, removed=("a",), added=("b",), removed_total=1, added_total=1)
    (scenario,) = evidence_scenarios(_manual(category, kind), {"ev-001": change})
    assert scenario.title == title


def test_a_removed_guard_asks_how_its_cases_fare_now() -> None:
    change = Change(kind="condition_change", removed=("user.is_admin",), removed_total=1)
    (scenario,) = evidence_scenarios(
        _manual(BehaviorCategory.AUTHORIZATION, "condition_change"), {"ev-001": change}
    )
    assert scenario.when == (
        "A principal that satisfied the removed guard, and one that does not, attempt the operation"
    )
    assert scenario.then == (
        "Each gets the outcome intended now that the condition is gone, which is asserted"
    )


def test_a_change_of_order_alone_quotes_what_the_code_now_holds() -> None:
    change = Change(kind="return_change")
    (scenario,) = evidence_scenarios(
        _manual(BehaviorCategory.OUTPUT_CONTRACT, "return_change"), {"ev-001": change}
    )
    assert scenario.given == "`f` changed its returned values, now `b; a`"


def test_a_long_value_is_cut_and_marked() -> None:
    long = "x" * (MAX_QUOTE_CHARS * 2)
    change = Change(kind="return_change", added=(long,), added_total=1)
    (scenario,) = evidence_scenarios(
        _manual(BehaviorCategory.OUTPUT_CONTRACT, "return_change"), {"ev-001": change}
    )
    (quoted,) = _code(scenario.given)[1:]
    assert len(quoted) == MAX_QUOTE_CHARS
    assert quoted.endswith("…")


def test_two_long_versions_of_one_value_are_cut_around_where_they_differ() -> None:
    names = [f"'name_{index:02d}'" for index in range(30)]
    before = f"__all__ = [{', '.join(names)}]"
    grown = [*names[:20], "'added'", *names[20:]]
    after = f"__all__ = [{', '.join(grown)}]"
    change = Change(
        kind="assignment_change",
        removed=(before,),
        added=(after,),
        removed_total=1,
        added_total=1,
        assigned_names=("__all__",),
    )
    behavior = _manual(BehaviorCategory.UNKNOWN, "assignment_change", symbol="<module>")
    (scenario,) = evidence_scenarios(behavior, {"ev-001": change})
    _, new, old = _code(scenario.given)
    # Cut at the end alone, both would read `__all__ = ['name_00', ...` up to the same point.
    assert "'added'" in new
    assert "'added'" not in old
    assert new.startswith("…") and old.startswith("…")


def test_many_values_name_the_first_two_and_count_the_rest() -> None:
    added = tuple(f"call_{index}()" for index in range(MAX_CHANGED_VALUES))
    change = Change(kind="call_change", added=added, added_total=7)
    (scenario,) = evidence_scenarios(
        _manual(BehaviorCategory.DEPENDENCY_INTERACTION, "call_change"), {"ev-001": change}
    )
    assert scenario.given == "`f` now also calls `call_0()`, `call_1()`, and 5 more"


def test_a_quote_holding_backticks_stays_one_code_span() -> None:
    value = "render('`` and `')"
    change = Change(kind="call_change", added=(value,), added_total=1)
    (scenario,) = evidence_scenarios(
        _manual(BehaviorCategory.DEPENDENCY_INTERACTION, "call_change"), {"ev-001": change}
    )
    assert _code(scenario.given) == ["f", value]


def test_the_evidence_on_the_finding_s_own_symbol_says_what_it_is_about() -> None:
    behavior = _manual(BehaviorCategory.UNKNOWN, "assignment_change").model_copy(
        update={
            "evidence": [
                Evidence(id="ev-001", path="m.py", symbol="f", kind="assignment_change"),
                Evidence(id="ev-002", path="m.py", symbol="g", kind="condition_change"),
                Evidence(id="ev-003", path="m.py", symbol="f", kind="call_change"),
            ]
        }
    )
    changes = {
        "ev-001": Change(kind="assignment_change", added=("x = 1",), added_total=1),
        "ev-002": Change(kind="condition_change", added=("ready",), added_total=1),
        "ev-003": Change(kind="call_change", added=("go()",), added_total=1),
    }
    (scenario,) = evidence_scenarios(behavior, changes)
    # `g`'s condition would say most, but the finding is about `f`, whose call says more than its
    # assignment.
    assert scenario.title == "Assert the outcome of `f` that depends on its changed call"


def test_a_finding_no_python_delta_describes_has_no_evidence_scenario() -> None:
    behavior = _manual(BehaviorCategory.VALIDATION, "schema_required_change")
    assert evidence_scenarios(behavior, {}) == ()
    obligations, _ = generate_obligations([behavior], {}, False, WeaverConfig().rules)
    # It keeps its category's scenarios, as an API schema's or a configuration file's finding does.
    assert [item.title for item in obligations] == sorted(
        item.title for item in CATEGORY_PROFILES[BehaviorCategory.VALIDATION].scenarios
    )


def test_obligations_quote_the_evidence_when_a_python_delta_describes_it() -> None:
    behaviors, changes = _findings(
        "def run(limit=2):\n    return limit\n",
        "def run(limit=3):\n    return limit\n",
    )
    obligations, omitted = generate_obligations(
        behaviors, {}, False, WeaverConfig().rules, changes=changes
    )
    assert omitted == 0
    assert [item.title for item in obligations] == [
        "Verify omitted-input behavior of `run` without `limit`",
        "Verify `run` with `limit` passed as the former default `2`",
    ]
    assert obligations[0].priority > obligations[1].priority


def test_only_python_deltas_are_described() -> None:
    def delta(kind: str, **metadata: object) -> StructuralDelta:
        return StructuralDelta(
            path="m.py",
            symbol="f",
            kind=kind,
            old=None,
            new=None,
            old_lines=None,
            new_lines=None,
            hunk_id=None,
            metadata=dict(metadata),
        )

    assert describe_change(delta("config_value_change")) is None
    assert describe_change(delta("parse_incomplete")) is None
    described = describe_change(
        delta(
            "comparison_change",
            removed=["x < 5 [Lt]"],
            added=["x <= 5 [LtE]", 3],
            removed_total=True,
        )
    )
    assert described is not None
    assert described.removed == ("x < 5",)
    assert described.added == ("x <= 5",)
    # A count that is no count falls back to the values recorded.
    assert described.removed_total == 1


def test_changes_are_found_by_the_evidence_ids_their_findings_cite() -> None:
    behaviors, changes = _findings(
        "def f(x):\n    if x.flag:\n        return 1\n    return 0\n",
        "def f(x):\n    if x.other:\n        return 1\n    return 0\n",
    )
    cited = {item.id for behavior in behaviors for item in behavior.evidence}
    assert set(changes) == cited
    assert all(
        changes[item.id].kind == item.kind for behavior in behaviors for item in behavior.evidence
    )


@pytest.mark.parametrize(
    ("change", "given"),
    [
        (
            Change(kind="decorator_change", added=("login_required",), added_total=1),
            "`f` is now also decorated with `login_required`",
        ),
        (
            Change(kind="decorator_change", removed=("cache",), removed_total=1),
            "`f` is no longer decorated with `cache`",
        ),
        (Change(kind="decorator_change"), "`f` changed its decorators, now `b; a`"),
    ],
)
def test_a_decorator_change_says_which_decorators_came_and_went(change: Change, given: str) -> None:
    (scenario,) = evidence_scenarios(
        _manual(BehaviorCategory.UNKNOWN, "decorator_change"), {"ev-001": change}
    )
    assert scenario.given == given


def test_code_the_parser_could_not_name_is_found_by_its_file() -> None:
    change = Change(kind="call_change", added=("go()",), added_total=1)
    behavior = _manual(BehaviorCategory.DEPENDENCY_INTERACTION, "call_change", symbol="<unparsed>")
    (scenario,) = evidence_scenarios(behavior, {"ev-001": change})
    assert scenario.title == "Assert the outcome of `m.py` that depends on its changed call"


def test_a_class_body_runs_as_code_uses_the_class() -> None:
    change = Change(
        kind="call_change", added=("field(default=1)",), added_total=1, symbol_kind="class"
    )
    behavior = _manual(BehaviorCategory.DEPENDENCY_INTERACTION, "call_change", symbol="Config")
    (scenario,) = evidence_scenarios(behavior, {"ev-001": change})
    assert scenario.when == "Code that uses `Config` runs with input that reaches the changed call"


def test_a_signature_whose_defaults_were_not_recorded_is_read_as_a_signature() -> None:
    delta = StructuralDelta(
        path="m.py",
        symbol="f",
        kind="signature_change",
        old="(a=1)",
        new="(a=2)",
        old_lines=None,
        new_lines=None,
        hunk_id=None,
        metadata={"default_changed": True},
    )
    described = describe_change(delta)
    assert described is not None
    assert described.defaults == ()
    behavior = _manual(BehaviorCategory.DEFAULT_BEHAVIOR, "signature_change")
    (scenario,) = evidence_scenarios(behavior, {"ev-001": described})
    assert scenario.title == "Check former contract behavior of `f`"


@pytest.mark.parametrize(
    ("call", "outside"),
    [
        ("session.post(self.url, data=body)", True),
        ("subprocess.call([str(cmd_path), f.name])", True),
        ("open(path, 'rb')", True),
        ("os.remove(path)", True),
        ("self.wfile.write(b'0\\r\\n\\r\\n')", True),
        ("self.client.get(url)", True),
        ("requests.get(url)", True),
        ("conn.execute(query)", True),
        ("urllib.request.urlopen(url)", True),
        # What only computes a value, or reads the process's own state.
        ("environ.get('wsgi.file_wrapper', _FileWrapper)", False),
        ("os.environ.get('LESS', '')", False),
        ("cache.get(key)", False),
        ("next(_id_generator)", False),
        ("isinstance(nested, SchemaMeta)", False),
        ("name.lower()", False),
        ("f.close()", False),
        ("datetime.now(UTC)", False),
        ("re.compile('/|;')", False),
        ("<dynamic>.join(cmd_params)", False),
        ("run(x)", False),
    ],
)
def test_a_call_leaves_the_process_by_what_it_names(call: str, outside: bool) -> None:
    assert leaves_the_process(call) is outside


def test_a_new_call_out_of_the_process_asks_for_its_arguments() -> None:
    change = Change(kind="call_change", added=("sock.sendall(payload)",), added_total=1)
    (scenario,) = evidence_scenarios(
        _manual(BehaviorCategory.DEPENDENCY_INTERACTION, "call_change"), {"ev-001": change}
    )
    assert scenario.title == "Assert the arguments of the new call in `f`"
    assert scenario.then == "The call happens when intended, with the arguments intended"


def test_only_the_calls_out_of_the_process_are_quoted_for_a_spy() -> None:
    change = Change(
        kind="call_change",
        removed=("session.post(url, data=raw)", "raw.encode('utf-8')"),
        added=("session.post(url, data=payload)", "Payload(raw)"),
        removed_total=2,
        added_total=2,
    )
    (scenario,) = evidence_scenarios(
        _manual(BehaviorCategory.DEPENDENCY_INTERACTION, "call_change"), {"ev-001": change}
    )
    assert scenario.title == "Assert the arguments of the changed call in `f`"
    assert scenario.given == (
        "`f` now calls `session.post(url, data=payload)` where it called "
        "`session.post(url, data=raw)`"
    )


@pytest.mark.parametrize(
    ("category", "change", "symbol", "readers", "weak"),
    [
        (
            BehaviorCategory.DEPENDENCY_INTERACTION,
            Change(kind="call_change", added=("next(counter)",), added_total=1),
            "f",
            (),
            True,
        ),
        (
            BehaviorCategory.DEPENDENCY_INTERACTION,
            Change(kind="call_change", added=("session.post(url)",), added_total=1),
            "f",
            (),
            False,
        ),
        # A side effect is what a test observes, whatever the call's name.
        (
            BehaviorCategory.SIDE_EFFECT,
            Change(kind="call_change", added=("notify(user)",), added_total=1),
            "f",
            (),
            False,
        ),
        (
            BehaviorCategory.UNKNOWN,
            Change(kind="assignment_change", added=("x = 1",), added_total=1),
            "f",
            (),
            True,
        ),
        (
            BehaviorCategory.STATE_TRANSITION,
            Change(kind="assignment_change", added=("self.state = 'open'",), added_total=1),
            "f",
            (),
            False,
        ),
        (
            BehaviorCategory.UNKNOWN,
            Change(
                kind="assignment_change",
                added=("ROOTS = ('a',)",),
                added_total=1,
                assigned_names=("ROOTS",),
            ),
            "<module>",
            ("select",),
            False,
        ),
        (
            BehaviorCategory.UNKNOWN,
            Change(
                kind="assignment_change",
                added=("ROOTS = ('a',)",),
                added_total=1,
                assigned_names=("ROOTS",),
            ),
            "<module>",
            (),
            True,
        ),
        (
            BehaviorCategory.UNKNOWN,
            Change(kind="condition_change", added=("ready",), added_total=1),
            "f",
            (),
            False,
        ),
    ],
)
def test_a_finding_is_weakly_anchored_when_it_names_nothing_to_assert(
    category: BehaviorCategory,
    change: Change,
    symbol: str,
    readers: tuple[str, ...],
    weak: bool,
) -> None:
    behavior = _manual(category, change.kind, symbol=symbol)
    assert weakly_anchored(behavior, {"ev-001": change}, Context(readers=readers)) is weak
    assert weakly_anchored(behavior, {}) is False


def test_a_module_variable_with_known_readers_names_what_to_call() -> None:
    change = Change(
        kind="assignment_change",
        removed=("ROOTS = ('core',)",),
        added=("ROOTS = ('core', 'tools')",),
        removed_total=1,
        added_total=1,
        assigned_names=("ROOTS",),
    )
    behavior = _manual(BehaviorCategory.UNKNOWN, "assignment_change", symbol="<module>")
    (one,) = evidence_scenarios(behavior, {"ev-001": change}, Context(readers=("select",)))
    assert one.title == "Clarify and characterize what `select` does with the changed `ROOTS`"
    assert one.given.endswith(", which `select` reads")
    assert one.when == "`select` is called with input the changed value decides"
    (two,) = evidence_scenarios(
        behavior, {"ev-001": change}, Context(readers=("select", "Index.build"))
    )
    assert two.title == (
        "Clarify and characterize what `select` and `Index.build` do with the changed `ROOTS`"
    )
    assert two.given.endswith(", which `select` and `Index.build` read")


def _evidence_behavior(
    index: int, category: BehaviorCategory, kind: str, *, symbol: str = "f"
) -> BehaviorChange:
    behavior = _manual(category, kind, symbol=symbol)
    return behavior.model_copy(
        update={
            "id": f"bc-{index:03d}",
            "evidence": [Evidence(id=f"ev-{index:03d}", path="m.py", symbol=symbol, kind=kind)],
        }
    )


def test_a_weak_finding_joins_the_obligation_of_a_stronger_one_on_its_symbol() -> None:
    strong = _evidence_behavior(1, BehaviorCategory.UNKNOWN, "condition_change")
    weak = _evidence_behavior(2, BehaviorCategory.DEPENDENCY_INTERACTION, "call_change")
    elsewhere = _evidence_behavior(3, BehaviorCategory.UNKNOWN, "assignment_change", symbol="g")
    changes = {
        "ev-001": Change(kind="condition_change", added=("ready",), added_total=1),
        "ev-002": Change(kind="call_change", added=("next(counter)",), added_total=1),
        "ev-003": Change(kind="assignment_change", added=("x = 1",), added_total=1),
    }
    obligations, omitted = generate_obligations(
        [strong, weak, elsewhere], {}, False, WeaverConfig().rules, changes=changes
    )
    assert omitted == 0
    by_title = {item.title: item.behavior_change_ids for item in obligations}
    assert by_title == {
        "Exercise both outcomes of the changed condition in `f`": ["bc-001", "bc-002"],
        # Nothing stronger speaks for `g`, so its own scenario stays.
        "Assert what `g` computes from its changed assignment": ["bc-003"],
    }


def test_a_weak_finding_keeps_its_own_obligation_when_coverage_differs() -> None:
    strong = _evidence_behavior(1, BehaviorCategory.UNKNOWN, "condition_change")
    weak = _evidence_behavior(2, BehaviorCategory.DEPENDENCY_INTERACTION, "call_change")
    changes = {
        "ev-001": Change(kind="condition_change", added=("ready",), added_total=1),
        "ev-002": Change(kind="call_change", added=("next(counter)",), added_total=1),
    }
    obligations, _ = generate_obligations(
        [strong, weak],
        {},
        False,
        WeaverConfig().rules,
        coverage_states={"bc-001": "covered"},
        changes=changes,
    )
    assert sorted(item.behavior_change_ids for item in obligations) == [["bc-001"], ["bc-002"]]


def test_a_review_question_is_never_folded_away() -> None:
    strong = _evidence_behavior(1, BehaviorCategory.UNKNOWN, "condition_change")
    weak = _evidence_behavior(2, BehaviorCategory.DEPENDENCY_INTERACTION, "call_change").model_copy(
        update={"presentation": Presentation.REVIEW_QUESTION}
    )
    changes = {
        "ev-001": Change(kind="condition_change", added=("ready",), added_total=1),
        "ev-002": Change(kind="call_change", added=("next(counter)",), added_total=1),
    }
    obligations, _ = generate_obligations(
        [strong, weak], {}, False, WeaverConfig().rules, changes=changes
    )
    titles = {item.title for item in obligations if item.behavior_change_ids == ["bc-002"]}
    assert "Resolve the high-risk review question" in titles


def test_a_folded_finding_keeps_the_model_s_obligations_for_it() -> None:
    strong = _evidence_behavior(1, BehaviorCategory.UNKNOWN, "condition_change")
    weak = _evidence_behavior(2, BehaviorCategory.DEPENDENCY_INTERACTION, "call_change")
    changes = {
        "ev-001": Change(kind="condition_change", added=("ready",), added_total=1),
        "ev-002": Change(kind="call_change", added=("next(counter)",), added_total=1),
    }
    suggestion = SuggestedScenario(
        evidence_ids=("ev-002",),
        type=ObligationType.POSITIVE,
        title="Two styles get distinct ids",
        given="Two styles with the same link",
        when="Both are created",
        then="Their ids differ",
    )
    obligations, _ = generate_obligations(
        [strong, weak], {}, False, WeaverConfig().rules, [suggestion], changes=changes
    )
    model = [item for item in obligations if item.title == "Two styles get distinct ids"]
    assert [item.behavior_change_ids for item in model] == [["bc-002"]]
    assert model[0].origin is Origin.LLM_SUPPORTED


def test_without_a_stronger_finding_an_assignment_joins_its_symbol_s_call() -> None:
    call = _evidence_behavior(1, BehaviorCategory.DEPENDENCY_INTERACTION, "call_change")
    assignment = _evidence_behavior(2, BehaviorCategory.UNKNOWN, "assignment_change")
    changes = {
        "ev-001": Change(kind="call_change", added=("next(counter)",), added_total=1),
        "ev-002": Change(kind="assignment_change", added=("x = 1",), added_total=1),
    }
    obligations, _ = generate_obligations(
        [call, assignment], {}, False, WeaverConfig().rules, changes=changes
    )
    assert [(item.title, item.behavior_change_ids) for item in obligations] == [
        ("Assert the outcome of `f` that depends on its changed call", ["bc-001", "bc-002"])
    ]


@pytest.mark.parametrize(
    ("symbol", "change", "given", "when"),
    [
        (
            "f",
            Change(
                kind="unknown_structure",
                removed=("del x[0]",),
                added=("del x[1]",),
                removed_total=1,
                added_total=1,
            ),
            "`f` changed in a way no rule names: it now reads `del x[1]` where it read `del x[0]`",
            "`f` runs with input that reaches the changed lines",
        ),
        (
            "f",
            Change(kind="unknown_structure", added=("yield item",), added_total=1),
            "`f` changed in a way no rule names: it now also reads `yield item`",
            "`f` runs with input that reaches the changed lines",
        ),
        (
            "<module>",
            Change(kind="unknown_structure", removed=('"FunctionAuth",',), removed_total=1),
            "The top-level code of `m.py` changed in a way no rule names: it no longer reads "
            '`"FunctionAuth",`',
            "The top-level code of `m.py` runs as the module is imported",
        ),
    ],
)
def test_a_change_no_rule_names_quotes_its_changed_lines(
    symbol: str, change: Change, given: str, when: str
) -> None:
    (scenario,) = evidence_scenarios(
        _manual(BehaviorCategory.UNKNOWN, "unknown_structure", symbol=symbol), {"ev-001": change}
    )
    assert scenario.title.startswith("Clarify and characterize what the changed lines of ")
    assert scenario.given == given
    assert scenario.when == when


def test_a_change_no_rule_names_without_its_lines_keeps_its_category_s_scenario() -> None:
    delta = StructuralDelta(
        path="m.py",
        symbol="f",
        kind="unknown_structure",
        old="body changed",
        new="body changed",
        old_lines=None,
        new_lines=None,
        hunk_id=None,
    )
    assert describe_change(delta) is None


def test_a_removed_raise_beside_another_names_the_one_left() -> None:
    change = Change(kind="raise_change", removed=("self.errors[0]",), removed_total=1)
    behavior = _manual(BehaviorCategory.ERROR_HANDLING, "raise_change").model_copy(
        update={
            "evidence": [
                Evidence(
                    id="ev-001",
                    path="m.py",
                    symbol="f",
                    kind="raise_change",
                    old="self.errors[0]; BaseExceptionGroup(message, self.errors)",
                    new="BaseExceptionGroup(message, self.errors)",
                )
            ]
        }
    )
    (scenario,) = evidence_scenarios(behavior, {"ev-001": change})
    assert scenario.given == (
        "`f` no longer raises `self.errors[0]`; it still raises "
        "`BaseExceptionGroup(message, self.errors)`"
    )
    assert scenario.then == (
        "Which exception reaches the caller there, and what it carries, is asserted"
    )


def test_a_new_private_helper_is_exercised_through_its_callers() -> None:
    change = Change(kind="symbol_added")
    behavior = _manual(BehaviorCategory.UNKNOWN, "symbol_added", symbol="_resolve")
    (scenario,) = evidence_scenarios(behavior, {"ev-001": change}, Context(callers=("pager",)))
    assert scenario.title == "Exercise the new `_resolve` through `pager`"
    assert scenario.given == "`_resolve` is new: `b; a`, and `pager` calls it"
    assert scenario.when == "`pager` runs with input that reaches `_resolve`"
    # A public one is still characterized on its own, whoever calls it.
    public = _manual(BehaviorCategory.UNKNOWN, "symbol_added", symbol="resolve")
    (own,) = evidence_scenarios(public, {"ev-001": change}, Context(callers=("pager",)))
    assert own.title == "Characterize the new `resolve`"


@pytest.mark.parametrize(
    ("symbol", "private"),
    [
        ("_x", True),
        ("Cls._helper", True),
        ("_Cls.__init__", True),
        ("Cls.__init__", False),
        ("Cls.method", False),
        ("<module>", False),
    ],
)
def test_a_name_is_private_when_one_of_its_parts_is(symbol: str, private: bool) -> None:
    assert is_private(symbol) is private


@pytest.mark.parametrize(
    ("kind", "symbol", "weak"),
    [
        ("symbol_added", "_helper", True),
        ("symbol_removed", "_Old.__init__", True),
        ("symbol_added", "helper", False),
        ("symbol_removed", "Old", False),
    ],
)
def test_a_private_symbol_added_or_removed_is_weakly_anchored(
    kind: str, symbol: str, weak: bool
) -> None:
    behavior = _manual(BehaviorCategory.UNKNOWN, kind, symbol=symbol)
    assert weakly_anchored(behavior, {"ev-001": Change(kind=kind)}) is weak


class _Graph:
    """What `contexts_of` asks of a call graph."""

    def __init__(self, readers=None, users=None) -> None:
        self._readers = readers or {}
        self._users = users or {}

    def readers(self, path: str, variable: str):
        return frozenset(self._readers.get((path, variable), ()))

    def users(self, node):
        return frozenset(self._users.get(node, ()))


def test_the_context_of_a_finding_names_its_readers_callers_and_related_symbols() -> None:
    assignment = _evidence_behavior(
        1, BehaviorCategory.UNKNOWN, "assignment_change", symbol="<module>"
    )
    helper = _evidence_behavior(2, BehaviorCategory.UNKNOWN, "symbol_added", symbol="_Box._fill")
    caller = _evidence_behavior(3, BehaviorCategory.UNKNOWN, "condition_change", symbol="Box.open")
    removed = _evidence_behavior(
        4, BehaviorCategory.UNKNOWN, "symbol_removed", symbol="_Handler.__init__"
    )
    stopped = _evidence_behavior(
        5, BehaviorCategory.DEPENDENCY_INTERACTION, "call_change", symbol="log"
    )
    sibling = _evidence_behavior(
        6, BehaviorCategory.UNKNOWN, "condition_change", symbol="_Box.size"
    )
    changes = {
        "ev-001": Change(kind="assignment_change", assigned_names=("ROOTS",)),
        "ev-002": Change(kind="symbol_added"),
        "ev-003": Change(kind="condition_change", added=("ok",), added_total=1),
        "ev-004": Change(kind="symbol_removed"),
        "ev-005": Change(kind="call_change", removed=("_Handler(stream)",), removed_total=1),
        "ev-006": Change(kind="condition_change", added=("full",), added_total=1),
    }
    graph = _Graph(
        readers={("m.py", "ROOTS"): {("m.py", "select"), ("other.py", "pick")}},
        users={("m.py", "_Box._fill"): {("m.py", "Box.open")}},
    )
    contexts = contexts_of(
        [assignment, helper, caller, removed, stopped, sibling],
        changes,
        graph,  # type: ignore[arg-type]
    )
    assert contexts["bc-001"].readers == ("select", "pick")
    assert contexts["bc-002"].callers == ("Box.open",)
    # Its caller first, then the other symbols of its class.
    assert contexts["bc-002"].related == (("m.py", "Box.open"), ("m.py", "_Box.size"))
    # A removed constructor's class was called by its name: `log` stopped calling it.
    assert contexts["bc-004"].related == (("m.py", "log"),)
    assert "bc-003" not in contexts
    # Without a call graph, nothing is known to call it.
    assert contexts_of([helper], changes, None) == {}


def test_a_private_helper_joins_the_obligation_of_the_code_that_calls_it() -> None:
    helper = _evidence_behavior(1, BehaviorCategory.UNKNOWN, "symbol_added", symbol="_fill")
    caller = _evidence_behavior(2, BehaviorCategory.UNKNOWN, "condition_change", symbol="open")
    changes = {
        "ev-001": Change(kind="symbol_added"),
        "ev-002": Change(kind="condition_change", added=("ok",), added_total=1),
    }
    contexts = {"bc-001": Context(callers=("open",), related=(("m.py", "open"),))}
    obligations, _ = generate_obligations(
        [helper, caller], {}, False, WeaverConfig().rules, changes=changes, contexts=contexts
    )
    assert [(item.title, item.behavior_change_ids) for item in obligations] == [
        ("Exercise both outcomes of the changed condition in `open`", ["bc-001", "bc-002"])
    ]


def test_a_private_helper_with_only_weak_relatives_joins_any_of_their_obligations() -> None:
    helper = _evidence_behavior(1, BehaviorCategory.UNKNOWN, "symbol_added", symbol="_fill")
    caller = _evidence_behavior(
        2, BehaviorCategory.DEPENDENCY_INTERACTION, "call_change", symbol="open"
    )
    changes = {
        "ev-001": Change(kind="symbol_added"),
        "ev-002": Change(kind="call_change", added=("_fill(box)",), added_total=1),
    }
    contexts = {"bc-001": Context(related=(("m.py", "open"),))}
    obligations, _ = generate_obligations(
        [helper, caller], {}, False, WeaverConfig().rules, changes=changes, contexts=contexts
    )
    assert [item.behavior_change_ids for item in obligations] == [["bc-001", "bc-002"]]


def test_links_are_followed_to_the_end_and_a_circle_keeps_its_own() -> None:
    first = _evidence_behavior(1, BehaviorCategory.UNKNOWN, "symbol_removed", symbol="_A")
    second = _evidence_behavior(2, BehaviorCategory.UNKNOWN, "symbol_removed", symbol="_B")
    changes = {"ev-001": Change(kind="symbol_removed"), "ev-002": Change(kind="symbol_removed")}
    circle = {
        "bc-001": Context(related=(("m.py", "_B"),)),
        "bc-002": Context(related=(("m.py", "_A"),)),
    }
    obligations, _ = generate_obligations(
        [first, second], {}, False, WeaverConfig().rules, changes=changes, contexts=circle
    )
    assert sorted(item.behavior_change_ids for item in obligations) == [["bc-001"], ["bc-002"]]
    third = _evidence_behavior(3, BehaviorCategory.UNKNOWN, "condition_change", symbol="c")
    changes["ev-003"] = Change(kind="condition_change", added=("ok",), added_total=1)
    chain = {
        "bc-001": Context(related=(("m.py", "_B"),)),
        "bc-002": Context(related=(("m.py", "c"),)),
    }
    obligations, _ = generate_obligations(
        [first, second, third], {}, False, WeaverConfig().rules, changes=changes, contexts=chain
    )
    assert [item.behavior_change_ids for item in obligations] == [["bc-001", "bc-002", "bc-003"]]
