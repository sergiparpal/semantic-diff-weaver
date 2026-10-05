"""Category probes: the keyword rules read code, not the prose inside string literals.

The display text of a delta keeps string literals verbatim, and the keyword rules used to read
it, so `WeaverError(..., 'Retry with a narrower diff')` was reported as a retry change. Probes
are computed from the AST instead, where a literal is still distinguishable from code.
"""

from __future__ import annotations

import ast
from dataclasses import replace

import pytest

from semantic_diff_weaver.ast_diff import StructuralDelta, extract_symbols, limits
from semantic_diff_weaver.ast_diff.compare import join_probes
from semantic_diff_weaver.ast_diff.extract import probe_text
from semantic_diff_weaver.ast_diff.types import SymbolSnapshot
from semantic_diff_weaver.models import BehaviorCategory, LineRange, RulesConfig
from semantic_diff_weaver.semantic_candidates import classify_delta
from semantic_diff_weaver.service import analyze


def _words(source: str) -> set[str]:
    return set(probe_text(ast.parse(source, mode="eval").body).split())


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
    assert secret not in probe_text(ast.parse(f"connect(key='{secret}')", mode="eval").body)
    long_call = "run(" + ", ".join(f"argument_{index}" for index in range(500)) + ")"
    probe = probe_text(ast.parse(long_call, mode="eval").body)
    assert len(probe) <= limits.MAX_PROBE_CHARS_PER_FEATURE


def test_a_missing_node_contributes_nothing() -> None:
    assert probe_text(None, ast.parse("name", mode="eval").body) == "name"


def _calls(source: str) -> SymbolSnapshot:
    return {item.qualified_name: item for item in extract_symbols(source)}["run"]


def test_snapshots_record_one_probe_per_probed_value() -> None:
    snapshot = _calls("def run(x):\n    if x < 5:\n        notify(x, 'Retry soon')\n")
    for feature in limits.PROBED_FEATURES:
        assert len(snapshot.probes[feature]) == len(snapshot.features[feature])
    assert snapshot.probes["calls"] == ("notify x",)


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
    assert "step_00" in probe
    assert "authorize_user" not in probe


def test_a_long_old_side_cannot_crowd_out_the_new_side() -> None:
    snapshot = _calls("def run():\n    step()\n")
    old = replace(snapshot, probes={"calls": ("word " * 2_000,)})
    new = replace(snapshot, probes={"calls": ("new_side_marker",)})
    probe = join_probes(old, new, "calls")
    assert probe is not None
    assert probe.endswith("new_side_marker")
    assert len(probe) <= limits.MAX_PROBE_CHARS_PER_SIDE + len(" new_side_marker")


def _call_delta(probe: str | None) -> StructuralDelta:
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
    probe = "log_message log_message"
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
            "boundary_change",
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
