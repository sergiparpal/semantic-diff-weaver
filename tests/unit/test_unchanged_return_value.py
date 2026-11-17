"""A return change whose returned values did not change gives way to its symbol's other changes.

A blind review of 40 obligations on the fifth sample's cases judged noise each "Assert the
consumer-visible contract of what X returns" asked of such a return: pycasbin#398's
`if not rule_added: return rule_added`, which became `return False` as the method moved its write
after the adapter's, and authlib#1026's `return token`, which became `return await
client.fetch_token(url, **kwargs)` as the configured parameters moved into the client. What
changed is beside the return, and the symbol's other deltas say so. `docs/decisions.md` records
the measurements on the benchmark's development cases.
"""

from __future__ import annotations

import pytest

from semantic_diff_weaver.ast_diff.analyze import analyze_ast
from semantic_diff_weaver.ast_diff.types import StructuralDelta
from semantic_diff_weaver.service import analyze
from semantic_diff_weaver.source import SourceHunk, SourceRevisionPair

WIDE_HUNK = SourceHunk(id="hunk-001", old_start=1, old_count=400, new_start=1, new_count=400)

FETCH_OLD = """\
def fetch(client, url, extra, **kwargs):
    params = {}
    if extra:
        params.update(extra)
    params.update(kwargs)
    token = client.fetch_token(url, **params)
    return token
"""
FETCH_NEW = """\
def fetch(client, url, extra, **kwargs):
    return client.fetch_token(url, **kwargs)
"""
AWAITED_OLD = """\
async def fetch(client, url, **kwargs):
    params = dict(kwargs)
    token = await client.fetch_token(url, **params)
    return token
"""
AWAITED_NEW = """\
async def fetch(client, url, **kwargs):
    return await client.fetch_token(url, **kwargs)
"""
ADD_OLD = """\
class Enforcer:
    def add(self, rule):
        added = self.model.add_policy(rule)
        if not added:
            return added
        if self.adapter:
            self.adapter.add_policy(rule)
        return added
"""
ADD_NEW = """\
class Enforcer:
    def add(self, rule):
        if self.model.has_policy(rule):
            return False
        if self.adapter:
            self.adapter.add_policy(rule)
        added = self.model.add_policy(rule)
        return added
"""


def _deltas(old: str, new: str, symbol: str) -> list[StructuralDelta]:
    pair = SourceRevisionPair(
        path="m.py",
        old_path="m.py",
        new_path="m.py",
        hunks=(WIDE_HUNK,),
        old_text=old,
        new_text=new,
    )
    return [item for item in analyze_ast([pair]).deltas if item.symbol == symbol]


def _kinds(old: str, new: str, symbol: str) -> set[str]:
    return {item.kind for item in _deltas(old, new, symbol)}


@pytest.mark.parametrize(
    ("old", "new", "symbol", "kinds"),
    [
        pytest.param(
            FETCH_OLD,
            FETCH_NEW,
            "fetch",
            {"assignment_change", "call_change", "condition_change"},
            id="a-call-returned-directly",
        ),
        pytest.param(
            AWAITED_OLD,
            AWAITED_NEW,
            "fetch",
            {"assignment_change", "call_change"},
            id="an-awaited-call-returned-directly",
        ),
        pytest.param(
            ADD_OLD,
            ADD_NEW,
            "Enforcer.add",
            {"call_change", "condition_change"},
            id="a-falsy-local-become-false",
        ),
        pytest.param(
            "def f(c, u):\n    s = c.size(u)\n    t = c.fetch(u, s)\n    if not s:\n"
            "        return s\n    return t\n",
            "def f(c, u):\n    s = c.size(u)\n    if not s:\n        return s\n"
            "    return c.fetch(u, s + 1)\n",
            "f",
            {"assignment_change", "call_change"},
            id="the-second-local-the-exits-read",
        ),
    ],
)
def test_a_return_whose_value_did_not_change_gives_way(
    old: str, new: str, symbol: str, kinds: set[str]
) -> None:
    # What did change stays: the call's arguments, and the checks and writes around them.
    assert _kinds(old, new, symbol) == kinds


@pytest.mark.parametrize(
    ("old", "new"),
    [
        pytest.param(
            "def f(x):\n    if not x:\n        return x\n    return 1\n",
            "def f(x):\n    if not x:\n        return False\n    return 1\n",
            id="the-symbol-s-only-change",
        ),
        pytest.param(
            "def f(c, u, b):\n    t = c.fetch_token(u, a=1)\n    if b:\n        c.log(b)\n"
            "    return t\n",
            "def f(c, u, b):\n    if b:\n        c.log(b, 1)\n    return c.get_token(u)\n",
            id="another-callee",
        ),
        pytest.param(
            "def f(c, u, b):\n    t = c.fetch(u)\n    if b:\n        t = c.fetch(u, b)\n"
            "    return t\n",
            "def f(c, u, b):\n    if b:\n        c.log(b)\n    return c.fetch(u)\n",
            id="a-local-assigned-twice",
        ),
        pytest.param(
            "def f(c, u, b):\n    t = c.fetch(u)\n    t.update(b)\n    if b:\n        c.log(b)\n"
            "    return t\n",
            "def f(c, u, b):\n    if b:\n        c.log(b, 1)\n    return c.fetch(u)\n",
            id="a-local-changed-in-place",
        ),
        pytest.param(
            "def f(m, r):\n    added = m.add(r)\n    if added is None:\n        return added\n"
            "    m.save(r)\n    return added\n",
            "def f(m, r):\n    added = m.add(r)\n    if added is None:\n        return False\n"
            "    m.save(r, True)\n    return added\n",
            id="a-guard-other-than-not",
        ),
        pytest.param(
            "def f(m, r):\n    added = m.add(r)\n    if not added:\n        return added\n"
            "    m.save(r)\n    return added\n",
            "def f(m, r):\n    added = m.add(r)\n    if not added:\n        return 'none'\n"
            "    m.save(r, True)\n    return added\n",
            id="a-truthy-literal",
        ),
        pytest.param(
            "def f(g, x):\n    y = g(x)\n    return y\n",
            "def f(g, x):\n    y = g(x)\n    if x:\n        return g(x)\n    return y\n",
            id="a-return-added",
        ),
        pytest.param(
            "def f(c, x):\n    if x:\n        c.log(x)\n    return x\n",
            "def f(c, x):\n    if x:\n        c.log(x, 1)\n    return c.fetch(x)\n",
            id="a-parameter-returned",
        ),
        pytest.param(
            "def f(c, xs):\n    for t in xs:\n        c.log(t)\n    return t\n",
            "def f(c, xs):\n    for t in xs:\n        c.log(t, 1)\n    return c.fetch(xs)\n",
            id="a-local-a-loop-assigns",
        ),
        pytest.param(
            "def f(c, u, b):\n    t = b + c.fetch(u)\n    if b:\n        c.log(b)\n    return t\n",
            "def f(c, u, b):\n    if b:\n        c.log(b, 1)\n    return b + c.fetch(u, 1)\n",
            id="more-than-a-call",
        ),
        pytest.param(
            "def f(c, u, b):\n    t = c.fetch(u)\n    if b:\n        c.log(b)\n    return t\n",
            "def f(c, u, b):\n    if b:\n        c.log(b, 1)\n        return None\n"
            "    return c.fetch(u)\n",
            id="more-returns-than-before",
        ),
    ],
)
def test_a_return_whose_value_may_have_changed_stays(old: str, new: str) -> None:
    assert "return_change" in _kinds(old, new, "f")


def test_the_obligation_follows_the_other_changes(repo_factory) -> None:
    """The return contract's obligation goes, and every finding keeps one: on the benchmark's
    development cases, each dependency finding joined its symbol's condition obligation."""
    repo, base, head = repo_factory({"app.py": ADD_OLD}, {"app.py": ADD_NEW})
    analysis = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}
    )
    titles = [item["title"] for item in analysis["test_obligations"]]
    assert not any("consumer-visible contract" in title for title in titles)
    assert "Exercise both outcomes of the changed condition in `Enforcer.add`" in titles
    covered = {
        finding for item in analysis["test_obligations"] for finding in item["behavior_change_ids"]
    }
    assert covered == {item["id"] for item in analysis["behavior_changes"]}
