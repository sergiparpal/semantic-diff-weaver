"""Category probes: the category rules read code, not the text of string literals.

The display text of a delta keeps string literals verbatim and is redacted and cut, and the
rules used to read it: `WeaverError(..., 'Retry with a narrower diff')` was a retry change, a
`'<'` inside a literal was a comparison, and redaction could erase `==` from `token == expected`.
Probes are computed from the AST instead, where a literal is still distinguishable from code.
"""

from __future__ import annotations

import ast
from dataclasses import replace

import pytest

from semantic_diff_weaver.ast_diff import StructuralDelta, extract_symbols, limits
from semantic_diff_weaver.ast_diff.compare import join_probes
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
    snapshot = _calls(f"def run():\n{steps}\n    authorize_user()\n")
    probe = join_probes(snapshot, snapshot, "calls")
    assert probe is not None
    assert "step_00" in probe.words
    assert "authorize_user" not in probe.words


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
    snapshot = _calls("def run():\n    step()\n")
    old = replace(snapshot, probes={"calls": (CategoryProbe("word " * 2_000, compares=False),)})
    new = replace(snapshot, probes={"calls": (CategoryProbe("new_side_marker", compares=False),)})
    probe = join_probes(old, new, "calls")
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
