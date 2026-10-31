"""Category probes: the category rules read code, not the text of string literals.

The display text of a delta keeps string literals verbatim and is redacted and cut, and the
rules used to read it: `WeaverError(..., 'Retry with a narrower diff')` was a retry change, a
`'<'` inside a literal was a comparison, and redaction could erase `==` from `token == expected`.
Probes are computed from the AST instead, where a literal is still distinguishable from code.
"""

from __future__ import annotations

import ast
import textwrap
from dataclasses import replace

import pytest

from semantic_diff_weaver.ast_diff import StructuralDelta, extract_symbols, limits
from semantic_diff_weaver.ast_diff.compare import is_internal_call, join_probes, own_call_names
from semantic_diff_weaver.ast_diff.extract import category_probe
from semantic_diff_weaver.ast_diff.types import CategoryProbe, SymbolSnapshot
from semantic_diff_weaver.models import BehaviorCategory, LineRange, RulesConfig
from semantic_diff_weaver.semantic_candidates import classify_delta
from semantic_diff_weaver.service import analyze


def _probe(source: str) -> CategoryProbe:
    return category_probe(ast.parse(source, mode="eval").body)


def _words(source: str) -> set[str]:
    return set(_probe(source).words.split())


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("log_message('Please retry later')", {"log_message"}, id="prose-literal"),
        pytest.param(
            "headers.get('Authorization')",
            {"headers", "get", "Authorization"},
            id="identifier-like-literal",
        ),
        pytest.param(
            "log_line(f'Retrying {job_id} of {total}')",
            {"log_line", "job_id", "total"},
            id="f-string-prose-parts",
        ),
        pytest.param(
            "config.get(f'retry_{name}')", {"config", "get", "retry_", "name"}, id="f-string-key"
        ),
        pytest.param(
            "client.call(timeout=5, **extra)",
            {"client", "call", "timeout", "extra"},
            id="keyword-names",
        ),
        pytest.param(
            "sorted(items, key=lambda entry: entry.weight)",
            {"sorted", "items", "key", "entry", "weight"},
            id="lambda-parameters",
        ),
        pytest.param("limit(3, b'raw bytes', None)", {"limit"}, id="non-string-constants"),
    ],
)
def test_a_probe_keeps_code_and_key_like_literals_and_drops_prose(
    source: str, expected: set[str]
) -> None:
    assert _words(source) == expected


def test_a_probe_is_redacted_and_bounded() -> None:
    secret = "ghp_" + "a" * 36
    assert secret not in _probe(f"connect(key='{secret}')").words
    long_call = "run(" + ", ".join(f"argument_{index}" for index in range(500)) + ")"
    assert len(_probe(long_call).words) <= limits.MAX_PROBE_CHARS_PER_FEATURE


def test_a_missing_node_contributes_nothing() -> None:
    probe = category_probe(None, ast.parse("name", mode="eval").body)
    assert probe == CategoryProbe(words="name", compares=False)


@pytest.mark.parametrize(
    ("source", "compares"),
    [
        pytest.param("attempts < 3", True, id="ordering"),
        pytest.param("total >= limit", True, id="ordering-or-equal"),
        pytest.param("0 <= index < size", True, id="chained-ordering"),
        pytest.param("count == 0", True, id="equality-with-a-number"),
        pytest.param("offset != -1", True, id="inequality-with-a-signed-number"),
        pytest.param("index == len(items)", True, id="equality-with-a-length"),
        pytest.param("token == 0", True, id="equality-redaction-would-erase"),
        pytest.param("first == second == 0", True, id="chained-equality-with-a-number"),
        pytest.param("check(size > 3)", True, id="nested-comparison"),
        pytest.param("state == expected", False, id="equality-between-values"),
        pytest.param("method == 'GET'", False, id="equality-with-a-string"),
        pytest.param("kind != token.STAR", False, id="inequality-with-a-constant-name"),
        pytest.param("flag == True", False, id="equality-with-a-bool"),
        pytest.param("'<' in text", False, id="operator-inside-a-literal"),
        pytest.param("'a != b' in text", False, id="operators-inside-a-literal"),
        pytest.param("mask & (1 << bit)", False, id="shift-is-not-a-comparison"),
        pytest.param("name in allowed_names", False, id="membership"),
        pytest.param("owner is None", False, id="identity"),
    ],
)
def test_a_probe_reads_comparisons_from_operators_not_characters(
    source: str, compares: bool
) -> None:
    """A boundary has a just-below, an exact, and a just-above case to test.

    An ordering does, and so does an equality with a number or a length. An equality between
    other values picks one case out of many, as `method == 'GET'` does, and orders nothing.
    """
    assert _probe(source).compares is compares


def _calls(source: str) -> SymbolSnapshot:
    return {item.qualified_name: item for item in extract_symbols(source)}["run"]


def test_snapshots_record_one_probe_per_probed_value() -> None:
    snapshot = _calls("def run(x):\n    if x < 5:\n        notify(x, 'Retry soon')\n")
    for feature in limits.PROBED_FEATURES:
        assert len(snapshot.probes[feature]) == len(snapshot.features[feature])
    assert snapshot.probes["calls"] == (CategoryProbe(words="notify x", compares=False),)
    assert snapshot.probes["conditions"] == (CategoryProbe(words="x", compares=True),)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("    if value > limit:\n        return 1\n    return 0\n", id="if"),
        pytest.param("    return 1 if value > limit else 0\n", id="if-expression"),
        pytest.param("    assert value > limit, 'value must exceed the limit'\n", id="assert"),
        pytest.param("    while value > limit:\n        value -= 1\n", id="while"),
    ],
)
def test_every_condition_site_records_its_probe(body: str) -> None:
    """`if`, conditional expressions, `assert`, and `while` each record a condition.

    Each must record its probe beside it. Values and probes are paired by position, so a site
    that recorded only the value would turn any analysis changing such a condition into an
    `internal_error` — the whole analysis, not just that symbol.
    """
    snapshot = _calls(f"def run(value, limit):\n{body}")
    assert [text for text, _ in snapshot.features["conditions"]] == ["value > limit"]
    assert snapshot.probes["conditions"] == (CategoryProbe(words="value limit", compares=True),)


@pytest.mark.parametrize(
    ("body", "words"),
    [
        pytest.param("    order.status = compute(value)\n", {"order", "status"}, id="attribute"),
        pytest.param("    total: int = value\n", {"total"}, id="annotated"),
        pytest.param("    total += value\n", {"total"}, id="augmented"),
        pytest.param("    first = second = value\n", {"first", "second"}, id="chained"),
        pytest.param("    head, *tail = value\n", {"head", "tail"}, id="unpacked"),
        pytest.param("    cache[key] = value\n", {"cache", "key"}, id="subscript"),
    ],
)
def test_every_assignment_site_records_a_probe_of_its_targets(body: str, words: set[str]) -> None:
    """Assignments record their probe beside their value, read from what they assign to.

    Values and probes are paired by position, like conditions, so a site recording only the
    value would turn any analysis changing such an assignment into an `internal_error`.
    """
    snapshot = _calls(f"def run(order, cache, key, value):\n{body}")
    assert len(snapshot.probes["assignments"]) == len(snapshot.features["assignments"])
    (probe,) = snapshot.probes["assignments"]
    assert set(probe.words.split()) == words


def test_an_assignment_probe_leaves_out_the_value() -> None:
    """`get_state()` reads state and `'Invalid state'` is prose: neither is written to state."""
    snapshot = _calls("def run():\n    total = get_state('Invalid state')\n")
    assert snapshot.probes["assignments"] == (CategoryProbe(words="total", compares=False),)


def test_a_snapshot_without_probes_falls_back_to_display_text() -> None:
    snapshot = _calls("def run():\n    step()\n")
    assert join_probes(replace(snapshot, probes={}), snapshot, "calls") is None
    assert join_probes(snapshot, replace(snapshot, probes={}), "calls") is None


def test_a_probe_covers_only_the_values_the_display_text_shows() -> None:
    """Reaching past the display window found new keywords in calls the text never showed.

    On this repository's own history that turned `service.analyze`, whose display text stops
    long before its `RepositoryIdentity(...)` call, into an authorization change.
    """
    steps = "\n".join(f"    step_{index:02d}(argument_value_{index:02d})" for index in range(60))
    late = join_probes(
        _calls(f"def run():\n{steps}\n    authorize_user()\n"),
        _calls(f"def run():\n{steps}\n    authorize_admin()\n"),
        "calls",
    )
    assert late is not None
    assert "authorize" not in late.words
    early = join_probes(
        _calls(f"def run():\n    authorize_user()\n{steps}\n"),
        _calls(f"def run():\n    authorize_admin()\n{steps}\n"),
        "calls",
    )
    assert early is not None
    assert early.words.split() == ["authorize_user", "authorize_admin"]


def test_a_late_comparison_outside_the_display_text_does_not_count() -> None:
    """The same window applies to comparisons: a condition the text never showed is not read."""
    checks = "\n".join(
        f"    if flag_{index:02d}(argument_value_{index:02d}):\n        pass" for index in range(80)
    )
    snapshot = _calls(f"def run():\n{checks}\n    if late_value > 3:\n        pass\n")
    display = "; ".join(text for text, _ in snapshot.features["conditions"][:-1])
    assert len(display) > limits.MAX_DELTA_TEXT_CHARS
    probe = join_probes(snapshot, snapshot, "conditions")
    assert probe is not None
    assert "late_value" not in probe.words
    assert probe.compares is False
    early = _calls("def run():\n    if value > 3:\n        pass\n")
    joined = join_probes(early, snapshot, "conditions")
    assert joined is not None
    assert joined.compares is True


def test_a_long_old_side_cannot_crowd_out_the_new_side() -> None:
    # Assignments read every value shown, so their two sides are joined even when unchanged.
    snapshot = _calls("def run():\n    total = 1\n")
    words = {"assignments": (CategoryProbe("word " * 2_000, compares=False),)}
    old = replace(snapshot, probes=words)
    new = replace(
        snapshot, probes={"assignments": (CategoryProbe("new_side_marker", compares=False),)}
    )
    probe = join_probes(old, new, "assignments")
    assert probe is not None
    assert probe.words.endswith("new_side_marker")
    assert len(probe.words) <= limits.MAX_PROBE_CHARS_PER_SIDE + len(" new_side_marker")


def _call_delta(probe: CategoryProbe | None) -> StructuralDelta:
    return StructuralDelta(
        path="src/report.py",
        symbol="report",
        kind="call_change",
        old="log_message('Please retry later')",
        new="log_message('Please try again later')",
        old_lines=LineRange(start=1, end=2),
        new_lines=LineRange(start=1, end=2),
        hunk_id="src/report.py#hunk-001",
        probe=probe,
    )


def test_the_classifier_reads_the_probe_and_falls_back_to_the_display_text() -> None:
    rules = RulesConfig()
    assert classify_delta(_call_delta(None), rules)[0] is BehaviorCategory.RETRY_TIMEOUT
    probe = CategoryProbe(words="log_message log_message", compares=False)
    assert classify_delta(_call_delta(probe), rules)[0] is BehaviorCategory.DEPENDENCY_INTERACTION


def _categories(repo_factory, old: str, new: str) -> set[str]:
    repo, base, head = repo_factory({"app.py": old}, {"app.py": new})
    result = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}
    )
    return {item["category"] for item in result["behavior_changes"]}


@pytest.mark.parametrize(
    ("old", "new", "expected", "absent"),
    [
        pytest.param(
            "def report(path):\n    return log_message(path, 'Please retry later')\n",
            "def report(path):\n    return log_message(path, 'Please try again later')\n",
            "dependency_interaction_change",
            "retry_timeout_change",
            id="reworded-message-is-not-a-retry-change",
        ),
        pytest.param(
            "def tick(job_id):\n    log_line(f'Retry job {job_id}')\n",
            "def tick(job_id):\n    log_line(f'Retry job {job_id} soon')\n",
            "dependency_interaction_change",
            "retry_timeout_change",
            id="f-string-message-is-not-a-retry-change",
        ),
        pytest.param(
            "def check(kinds):\n    assert 'boundary' in kinds('def run(limit=2): pass')\n",
            "def check(kinds):\n    assert 'boundary' in kinds('def run(limit=3): pass')\n",
            # A membership test, which the comparison rule called a boundary until 2026-10-31.
            "unknown_semantic_change",
            "retry_timeout_change",
            id="code-quoted-in-a-test-string-is-not-code",
        ),
        pytest.param(
            "def window(config):\n    return config.get('retry_limit', 3)\n",
            "def window(config):\n    return config.get('retry_limit', 5)\n",
            "retry_timeout_change",
            None,
            id="a-key-like-literal-still-counts",
        ),
        pytest.param(
            "def guard(request):\n    if request.headers.get('Authorization'):\n"
            "        return True\n    return False\n",
            "def guard(request):\n    if request.headers.get('X-Authorization'):\n"
            "        return True\n    return False\n",
            "authorization_change",
            None,
            id="a-header-name-still-counts",
        ),
    ],
)
def test_classification_reads_code_not_prose(
    repo_factory, old: str, new: str, expected: str, absent: str | None
) -> None:
    categories = _categories(repo_factory, old, new)
    assert expected in categories
    if absent is not None:
        assert absent not in categories


@pytest.mark.parametrize(
    ("old", "new", "expected", "absent"),
    [
        pytest.param(
            "ROOTS = ('core', 'tools')\n",
            "ROOTS = ('core', 'tools', 'reports')\n",
            "unknown_semantic_change",
            "state_transition_change",
            id="a-module-constant-is-not-state",
        ),
        pytest.param(
            "def width(text):\n    margin = 2\n    return len(text) + margin\n",
            "def width(text):\n    margin = 4\n    return len(text) + margin\n",
            "unknown_semantic_change",
            "state_transition_change",
            id="a-local-variable-is-not-state",
        ),
        pytest.param(
            "def explain(code):\n    message = 'Invalid state transition'\n    return message\n",
            "def explain(code):\n    message = 'Invalid transition'\n    return message\n",
            "unknown_semantic_change",
            "state_transition_change",
            id="prose-in-the-value-is-not-state",
        ),
        pytest.param(
            "def pick(items):\n    chooser = RandomState(1)\n    return chooser.choice(items)\n",
            "def pick(items):\n    chooser = RandomState(2)\n    return chooser.choice(items)\n",
            "unknown_semantic_change",
            "state_transition_change",
            id="a-value-naming-state-is-not-state",
        ),
        pytest.param(
            "def close(order):\n    order.status = 'closing'\n",
            "def close(order):\n    order.status = 'closed'\n",
            "state_transition_change",
            "unknown_semantic_change",
            id="an-attribute-named-for-state",
        ),
        pytest.param(
            "def advance_state(machine):\n    step = 1\n    return machine.run(step)\n",
            "def advance_state(machine):\n    step = 2\n    return machine.run(step)\n",
            "state_transition_change",
            "unknown_semantic_change",
            id="a-symbol-named-for-state",
        ),
    ],
)
def test_an_assignment_is_a_state_change_only_when_named_for_state(
    repo_factory, old: str, new: str, expected: str, absent: str
) -> None:
    """A changed assignment used to be a state transition whatever it assigned.

    A module constant gaining an entry, `CODE_ROOTS` in a real project, became the top
    obligations to test an allowed, an invalid, and a repeated transition.
    """
    categories = _categories(repo_factory, old, new)
    assert expected in categories
    assert absent not in categories


def _condition_evidence(repo_factory, old: str, new: str) -> tuple[str, dict]:
    """The category of the condition change, and its evidence, for a one-file change."""
    repo, base, head = repo_factory({"app.py": old}, {"app.py": new})
    result = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}
    )
    matches = [
        (behavior["category"], evidence)
        for behavior in result["behavior_changes"]
        for evidence in behavior["evidence"]
        if evidence["kind"] == "condition_change"
    ]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.parametrize(
    ("old_test", "new_test", "expected"),
    [
        pytest.param(
            "'<' in text", "'<=' in text", "unknown_semantic_change", id="literal-operator"
        ),
        pytest.param(
            "mask & (1 << bit)", "mask & (1 << (bit + 1))", "unknown_semantic_change", id="shift"
        ),
        pytest.param("retries < 3", "retries <= 3", "retry_timeout_change", id="real-comparison"),
    ],
)
def test_the_condition_rule_reads_comparisons_from_operators(
    repo_factory, old_test: str, new_test: str, expected: str
) -> None:
    template = "def check(text, mask, bit, retries):\n    if {}:\n        return 1\n    return 0\n"
    category, _ = _condition_evidence(
        repo_factory, template.format(old_test), template.format(new_test)
    )
    assert category == expected


def test_a_comparison_redaction_erased_from_the_text_still_counts(repo_factory) -> None:
    """`token == 0` is displayed as `token =[REDACTED] 0`: no `==` left to find."""
    template = "def check(token):\n    if {}:\n        return 1\n    return 0\n"
    category, evidence = _condition_evidence(
        repo_factory, template.format("token == 0"), template.format("token == 1")
    )
    assert "==" not in evidence["old"]
    assert category == "boundary_change"


@pytest.mark.parametrize(
    ("old_test", "new_test", "expected"),
    [
        pytest.param(
            "token == expected",
            "token == expected.strip()",
            "unknown_semantic_change",
            id="equality-between-values",
        ),
        pytest.param(
            "request.method == 'GET'",
            "request.method != 'GET'",
            "unknown_semantic_change",
            id="equality-with-a-string",
        ),
        pytest.param(
            "user.role == 'admin'",
            "user.role != 'admin'",
            "authorization_change",
            id="an-equality-guard-is-read-for-its-names",
        ),
        pytest.param("count == 0", "count == 1", "boundary_change", id="equality-with-a-number"),
    ],
)
def test_an_equality_is_a_boundary_only_against_a_number(
    repo_factory, old_test: str, new_test: str, expected: str
) -> None:
    """Every `==` and `!=` used to be a boundary, whatever it compared.

    `user.role == 'admin'` was a boundary at impact 64, not an authorization guard at 92: the
    boundary check ran before the rules that read names, and the obligations it produced asked
    for values just below and just above `'admin'`. The redaction regression above was pinned
    on `token == expected`, a comparison of two values.
    """
    template = (
        "def check(token, expected, request, user, count):\n"
        "    if {}:\n        return 1\n    return 0\n"
    )
    category, _ = _condition_evidence(
        repo_factory, template.format(old_test), template.format(new_test)
    )
    assert category == expected


def _findings(repo_factory, old: str, new: str) -> list[dict]:
    repo, base, head = repo_factory({"app.py": old}, {"app.py": new})
    result = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}
    )
    return result["behavior_changes"]


@pytest.mark.parametrize(
    ("old", "new"),
    [
        pytest.param(
            "def close(parent):\n    if parent is None:\n        return 1\n    return 0\n",
            "def close(parent):\n    if parent is not None:\n        return 1\n    return 0\n",
            id="identity",
        ),
        pytest.param(
            "def is_star(ch):\n    return ch.type == STAR or ch.type == DOUBLESTAR\n",
            "def is_star(ch):\n    return ch.type in STARS\n",
            id="equality-rewritten-as-membership",
        ),
        pytest.param(
            "def pick(items, parent):\n    if len(items) > 3:\n        return 2\n"
            "    if parent is None:\n        return 1\n    return 0\n",
            "def pick(items, parent):\n    if len(items) > 3:\n        return 2\n"
            "    if parent is not None:\n        return 1\n    return 0\n",
            id="an-unchanged-boundary-beside-the-change",
        ),
        pytest.param(
            "def ask(row, recorder):\n    if row['valid']:\n        return 2\n"
            "    if recorder:\n        return 1\n    return 0\n",
            "def ask(row, recorder):\n    if row['valid']:\n        return 2\n"
            "    if recorder is not None:\n        return 1\n    return 0\n",
            id="an-unchanged-guard-word-beside-the-change",
        ),
    ],
)
def test_conditions_and_comparisons_are_read_from_what_changed(
    repo_factory, old: str, new: str
) -> None:
    """Every changed comparison used to be a boundary, `is None` and `in STARS` included.

    The condition rule had read operators since 2026-10-05; the comparison rule never did, so a
    changed `if parent is None` was an unknown condition and a boundary comparison at once. Now
    both are read alike and join one finding. Both also read only the values that changed: an
    unchanged `len(items) > 3` made the change beside it a boundary, and an unchanged
    `row['valid']` would make it a validation guard.
    """
    findings = _findings(repo_factory, old, new)
    assert [item["category"] for item in findings] == ["unknown_semantic_change"]
    kinds = {evidence["kind"] for evidence in findings[0]["evidence"]}
    assert "comparison_change" in kinds


def test_a_changed_ordering_is_still_a_boundary(repo_factory) -> None:
    findings = _findings(
        repo_factory,
        "def pick(size):\n    if size > 3:\n        return 1\n    return 0\n",
        "def pick(size):\n    if size >= 3:\n        return 1\n    return 0\n",
    )
    assert [item["category"] for item in findings] == ["boundary_change"]
    assert {evidence["kind"] for evidence in findings[0]["evidence"]} == {
        "comparison_change",
        "condition_change",
    }


def test_a_delta_compares_a_boundary_only_where_it_changed() -> None:
    """An unchanged `len(items) > 3` made a changed `parent is None` beside it a boundary.

    The words of a comparison or condition delta are those of the values it changed too; a call
    delta's are still those of every call it shows, as the display-window test above pins.
    """
    before = _calls(
        "def run(items, parent):\n    if len(items) > 3 or parent is None:\n        pass\n"
    )
    unchanged_boundary = _calls(
        "def run(items, parent):\n    if len(items) > 3:\n        pass\n"
        "    if parent is None:\n        pass\n"
    )
    changed_identity = _calls(
        "def run(items, parent):\n    if len(items) > 3:\n        pass\n"
        "    if parent is not None:\n        pass\n"
    )
    for feature in ("comparisons", "conditions"):
        probe = join_probes(unchanged_boundary, changed_identity, feature)
        assert probe is not None
        assert probe.compares is False
        assert probe.words.split() == ["parent", "parent"]
    rewritten = join_probes(before, unchanged_boundary, "conditions")
    assert rewritten is not None
    assert rewritten.compares is True


@pytest.mark.parametrize(
    ("source", "words"),
    [
        pytest.param(
            "def run(items):\n    for item in items:\n        pass\n", {"item", "items"}, id="for"
        ),
        pytest.param(
            "async def run(items):\n    async for item in items:\n        pass\n",
            {"item", "items"},
            id="async-for",
        ),
        pytest.param(
            "def run(value):\n    while value > 0:\n        value -= 1\n", {"value"}, id="while"
        ),
    ],
)
def test_every_loop_site_records_its_probe(source: str, words: set[str]) -> None:
    """Loops record a probe now that the loop rule reads retry words, paired by position."""
    snapshot = _calls(source)
    assert len(snapshot.probes["loops"]) == len(snapshot.features["loops"]) == 1
    assert set(snapshot.probes["loops"][0].words.split()) == words


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        pytest.param(
            "def total(order):\n    result = 0\n    for line in order.lines:\n"
            "        result += line.amount\n    return result\n",
            "def total(order):\n    result = 0\n    for line in order.paid_lines:\n"
            "        result += line.amount\n    return result\n",
            "unknown_semantic_change",
            id="a-loop-over-a-collection",
        ),
        pytest.param(
            "def read(lines):\n    for line in 'please retry later'.split():\n        pass\n",
            "def read(lines):\n    for line in 'please try again'.split():\n        pass\n",
            "unknown_semantic_change",
            id="prose-in-what-it-iterates",
        ),
        pytest.param(
            "def fit(text, limit):\n    while len(text) > limit:\n        text = text[:-1]\n"
            "    return text\n",
            "def fit(text, limit):\n    while len(text) >= limit:\n        text = text[:-1]\n"
            "    return text\n",
            "unknown_semantic_change",
            id="a-loop-bounded-by-a-limit",
        ),
        pytest.param(
            "def wait(client, deadline):\n    while time.monotonic() < deadline:\n"
            "        client.poll()\n",
            "def wait(client, deadline):\n    while time.monotonic() <= deadline:\n"
            "        client.poll()\n",
            "retry_timeout_change",
            id="a-loop-until-a-deadline",
        ),
        pytest.param(
            "def fetch(client, retries):\n    for attempt in range(retries):\n"
            "        client.get(attempt)\n",
            "def fetch(client, retries):\n    for attempt in range(retries + 1):\n"
            "        client.get(attempt)\n",
            "retry_timeout_change",
            id="a-loop-over-attempts",
        ),
    ],
)
def test_a_changed_loop_is_a_retry_only_when_its_names_say_so(
    repo_factory, old: str, new: str, expected: str
) -> None:
    """Every changed loop used to be a `retry_timeout_change`.

    That made a `for` over a command's parameters and a `while i > 0` that dropped words until
    a text fit into retries, with obligations to recover before a retry limit and to stop on a
    terminal failure.
    """
    repo, base, head = repo_factory({"app.py": old}, {"app.py": new})
    result = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}
    )
    loops = [
        behavior["category"]
        for behavior in result["behavior_changes"]
        for evidence in behavior["evidence"]
        if evidence["kind"] == "loop_change"
    ]
    assert loops == [expected]


OWN_CODE = """\
import requests
from .events import notify


def _starts_statement(leaf):
    def _first(node):
        return node

    return _first(leaf)


class Helper:
    def get_help_spec(self):
        return self.callback()

    @classmethod
    def build(cls):
        return cls()
"""


@pytest.mark.parametrize(
    ("name", "internal"),
    [
        pytest.param("self.get_help_spec", True, id="own-method"),
        pytest.param("cls.build", True, id="own-classmethod"),
        pytest.param("Helper.build", True, id="method-through-its-class"),
        pytest.param("_starts_statement", True, id="module-function"),
        pytest.param("_first", True, id="nested-function"),
        pytest.param("Helper", True, id="module-class"),
        pytest.param("len", True, id="pure-builtin"),
        pytest.param("open", False, id="builtin-that-reaches-out"),
        pytest.param("self.callback", False, id="attribute-holding-a-callable"),
        pytest.param("self.client.fetch", False, id="what-self-holds"),
        pytest.param("requests.get", False, id="imported-module"),
        pytest.param("notify", False, id="imported-name"),
        pytest.param("<dynamic>.fetch", False, id="dynamic-receiver"),
    ],
)
def test_a_call_is_internal_only_when_it_reaches_its_own_module(name: str, internal: bool) -> None:
    assert is_internal_call(name, own_call_names(extract_symbols(OWN_CODE))) is internal


def test_a_call_reaches_its_own_module_by_a_name_either_revision_defines() -> None:
    """A call to a helper the change adds, or to one it removes, is still the module's own."""
    old = extract_symbols("def _old():\n    return 1\n")
    new = extract_symbols("class Worker:\n    def _new(self):\n        return 2\n")
    assert own_call_names(old, new) == {"_old", "Worker", "Worker._new", "self._new", "cls._new"}


MODULE = "import requests\nimport warnings\n\n\ndef _clean(text):\n    return text.strip()\n\n\n"
# The methods the cases call: a call reaches its own module only through what one of its classes
# defines.
WORKER = (
    "class Worker:\n"
    "    def _old(self, x):\n        return x\n\n"
    "    def _new(self, x):\n        return x\n\n"
    "    def run(self, x):\n"
)


@pytest.mark.parametrize(
    ("old_body", "new_body", "expected"),
    [
        pytest.param(
            "    return self._old(x)\n",
            "    return self._new(x)\n",
            "unknown_semantic_change",
            id="own-method",
        ),
        pytest.param(
            "    return x.strip()\n",
            "    return _clean(x)\n",
            "dependency_interaction_change",
            id="a-string-method-is-still-a-call-on-an-object",
        ),
        pytest.param(
            "    return _clean(x)\n",
            "    return _clean(x, 10)\n",
            "unknown_semantic_change",
            id="module-function",
        ),
        pytest.param(
            "    return len(x)\n", "    return sum(x)\n", "unknown_semantic_change", id="builtin"
        ),
        pytest.param(
            "    return self.client.fetch(x)\n",
            "    return self.client.fetch(x, strict=True)\n",
            "dependency_interaction_change",
            id="what-self-holds",
        ),
        pytest.param(
            "    return self.callback(x)\n",
            "    return self.callback(x, strict=True)\n",
            "dependency_interaction_change",
            id="an-attribute-holding-a-callable",
        ),
        pytest.param(
            "    return self._old(x)\n",
            "    return self._new(requests.get(x))\n",
            "dependency_interaction_change",
            id="an-external-call-among-them",
        ),
        pytest.param(
            "    return x\n",
            "    warnings.warn('deprecated', DeprecationWarning)\n    return x\n",
            "side_effect_change",
            id="a-warning",
        ),
        pytest.param(
            "    self.save()\n    return self._old(x)\n",
            "    self.save()\n    return self._new(x)\n",
            "unknown_semantic_change",
            id="an-unchanged-side-effect-beside-the-change",
        ),
    ],
)
def test_the_call_rule_tells_the_module_s_own_code_from_a_dependency(
    repo_factory, old_body: str, new_body: str, expected: str
) -> None:
    """Every call no keyword explained was a dependency interaction, `self.get_help_spec()` too.

    Its obligations ask how the code handles a dependency's failures and unexpected responses,
    which a method of its own instance does not have.
    """
    old = f"{MODULE}{WORKER}{textwrap.indent(old_body, '    ')}"
    new = f"{MODULE}{WORKER}{textwrap.indent(new_body, '    ')}"
    calls = [
        item["category"]
        for item in _findings(repo_factory, old, new)
        for evidence in item["evidence"]
        if evidence["kind"] == "call_change"
    ]
    assert calls == [expected]


def test_an_internal_call_joins_an_unknown_condition_without_a_second_assumption(
    repo_factory,
) -> None:
    """Each distinct assumption costs a finding confidence, and a third one took six findings of
    a truncated range on adaptive-policy-compilation below the reporting threshold."""
    old = f"{WORKER}        if x is None:\n            return self._old(x)\n"
    new = f"{WORKER}        if x is not None:\n            return self._new(x)\n"
    [finding] = [
        item
        for item in _findings(repo_factory, old, new)
        if item["category"] == "unknown_semantic_change"
    ]
    assert {"call_change", "condition_change"} <= {item["kind"] for item in finding["evidence"]}
    assert finding["assumptions"] == [
        "The surrounding runtime contract is unavailable to static analysis."
    ]


@pytest.mark.parametrize(
    ("destination", "expected"),
    [
        pytest.param(
            "def _new(x):\n    return x\n", "unknown_semantic_change", id="defined-where-it-moved"
        ),
        pytest.param(
            "from .helpers import _new\n", "dependency_interaction_change", id="imported-there"
        ),
    ],
)
def test_a_moved_symbol_reaches_what_either_of_its_modules_defines(
    repo_factory, destination: str, expected: str
) -> None:
    """`run` moves from `a.py` to `b.py` and calls `_new` instead of `_old`: `a.py` defines the
    old callee and `b.py` the new one, so neither module's names alone cover both."""
    helpers = "def _old(x):\n    return x\n"
    run = "def run(x):\n    value = x + 1\n    total = value * 2\n    return {}(total)\n"
    repo, base, head = repo_factory(
        {"a.py": f"{helpers}\n\n{run.format('_old')}", "b.py": destination},
        {"a.py": helpers, "b.py": f"{destination}\n\n{run.format('_new')}"},
    )
    result = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}
    )
    calls = [
        (item["category"], evidence["path"], evidence["symbol"])
        for item in result["behavior_changes"]
        for evidence in item["evidence"]
        if evidence["kind"] == "call_change"
    ]
    assert calls == [(expected, "b.py", "run")]
