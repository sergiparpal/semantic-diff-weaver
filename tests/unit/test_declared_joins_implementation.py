"""A finding on a method that only declares what its class's implementations define joins the
finding on an implementation of it that the same change touches.

The Action's field test on meal-manager had five findings on a repository `Protocol`'s new or
changed members, whose bodies are only `...`. Each asked to characterize a declaration, with no
code of its own to run. Each stood beside the finding on its JSON implementation's method of the
same name, which said what to test.
"""

from __future__ import annotations

import ast

import pytest

from semantic_diff_weaver.ast_diff import extract_symbols
from semantic_diff_weaver.ast_diff.extract import declares_only
from semantic_diff_weaver.evidence_scenarios import Change, evidence_scenarios
from semantic_diff_weaver.models import (
    BehaviorCategory,
    BehaviorChange,
    Evidence,
    Origin,
    RiskLabel,
    ScoreExplanation,
)
from semantic_diff_weaver.service import analyze

PROTOCOL_OLD = (
    "from typing import Protocol\n\n\n"
    "class HistoryRepository(Protocol):\n"
    "    def load(self) -> list: ...\n"
)
PROTOCOL_NEW = (
    "from typing import Protocol\n\n\n"
    "class HistoryRepository(Protocol):\n"
    "    def load(self) -> list: ...\n"
    "    def damaged(self) -> bool: ...\n"
)
JSON_OLD = (
    "class JsonHistoryRepository:\n"
    "    def __init__(self, events):\n"
    "        self.events = events\n\n"
    "    def load(self) -> list:\n"
    "        return list(self.events)\n"
)
JSON_NEW = JSON_OLD + (
    "\n    def damaged(self) -> bool:\n"
    "        if self.events is None:\n"
    "            return True\n"
    "        return False\n"
)


def _analysis(repo_factory, old: dict[str, str], new: dict[str, str]) -> dict:
    repo, base, head = repo_factory(old, new)
    return analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}
    )


def _symbols(analysis: dict) -> list[str]:
    return [item["evidence"][0]["symbol"] for item in analysis["behavior_changes"]]


def test_a_protocol_member_joins_the_finding_on_its_implementation(repo_factory) -> None:
    analysis = _analysis(
        repo_factory,
        {"repos/base.py": PROTOCOL_OLD, "repos/json_history.py": JSON_OLD},
        {"repos/base.py": PROTOCOL_NEW, "repos/json_history.py": JSON_NEW},
    )
    assert _symbols(analysis) == ["JsonHistoryRepository.damaged"]
    (finding,) = analysis["behavior_changes"]
    # The implementation's evidence first, the declaration's after it.
    assert [(item["symbol"], item["kind"]) for item in finding["evidence"]] == [
        ("JsonHistoryRepository.damaged", "symbol_added"),
        ("HistoryRepository.damaged", "symbol_added"),
    ]
    # The declaration's own obligation went with it: the implementation's says what to test.
    titles = [item["title"] for item in analysis["test_obligations"]]
    assert titles == ["Assert each outcome of the new `JsonHistoryRepository.damaged`"]
    assert all(item["behavior_change_ids"] == ["bc-001"] for item in analysis["test_obligations"])


def test_a_declaration_with_no_implementation_in_the_change_asks_for_one(repo_factory) -> None:
    analysis = _analysis(
        repo_factory, {"repos/base.py": PROTOCOL_OLD}, {"repos/base.py": PROTOCOL_NEW}
    )
    assert _symbols(analysis) == ["HistoryRepository.damaged"]
    (obligation,) = analysis["test_obligations"]
    assert obligation["title"] == (
        "Exercise `HistoryRepository.damaged` through each class that implements it"
    )
    assert obligation["given"] == (
        "`HistoryRepository.damaged` is new: `(self) -> bool`, which `HistoryRepository` only "
        "declares, with no code of its own to run"
    )
    assert obligation["when"] == (
        "Each class that implements `HistoryRepository` is called through "
        "`HistoryRepository.damaged`"
    )


def test_an_abstract_method_joins_the_subclass_that_names_it_among_its_bases(
    repo_factory,
) -> None:
    old = {
        "grants/base.py": (
            "import abc\n\n\nclass BaseGrant(abc.ABC):\n"
            "    @abc.abstractmethod\n    def validate(self, request):\n"
            "        raise NotImplementedError\n"
        ),
        "grants/code.py": (
            "from grants.base import BaseGrant\n\n\nclass AuthorizationCode(BaseGrant):\n"
            "    def validate(self, request):\n        return request.code is not None\n"
        ),
    }
    new = {
        "grants/base.py": old["grants/base.py"].replace(
            "def validate(self, request):", "def validate(self, request, strict=False):"
        ),
        "grants/code.py": old["grants/code.py"].replace(
            "def validate(self, request):", "def validate(self, request, strict=False):"
        ),
    }
    analysis = _analysis(repo_factory, old, new)
    assert _symbols(analysis) == ["AuthorizationCode.validate"]
    assert {item["symbol"] for item in analysis["behavior_changes"][0]["evidence"]} == {
        "AuthorizationCode.validate",
        "BaseGrant.validate",
    }


def test_a_method_of_the_same_name_on_an_unrelated_class_is_no_implementation(
    repo_factory,
) -> None:
    other_old = "class Clock:\n    def now(self):\n        return 0\n"
    other_new = other_old + "\n    def damaged(self) -> bool:\n        return False\n"
    analysis = _analysis(
        repo_factory,
        {"repos/base.py": PROTOCOL_OLD, "app/clock.py": other_old},
        {"repos/base.py": PROTOCOL_NEW, "app/clock.py": other_new},
    )
    assert sorted(_symbols(analysis)) == ["Clock.damaged", "HistoryRepository.damaged"]


def _method(source: str, name: str) -> tuple[ast.FunctionDef, ast.ClassDef]:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    return item, node
    raise AssertionError(name)


@pytest.mark.parametrize(
    ("source", "name", "declared"),
    [
        ("class P(Protocol):\n    def f(self) -> int: ...\n", "f", True),
        ("class P(typing.Protocol):\n    def f(self):\n        'Says what f does.'\n", "f", True),
        ("class P(Protocol[T]):\n    def f(self):\n        pass\n", "f", True),
        (
            "class B(abc.ABC):\n    @abc.abstractmethod\n    def f(self):\n"
            "        raise NotImplementedError('f')\n",
            "f",
            True,
        ),
        ("class B:\n    @abstractmethod\n    def f(self): ...\n", "f", True),
        # A default a Protocol gives, or an abstract method's code a subclass calls, runs.
        ("class P(Protocol):\n    def f(self):\n        return 1\n", "f", False),
        ("class B(ABC):\n    @abstractmethod\n    def f(self):\n        return []\n", "f", False),
        # A plain class's do-nothing hook: doing nothing is its default behavior.
        ("class Grant:\n    def revoke(self, code):\n        pass\n", "revoke", False),
        ("class Base:\n    def run(self):\n        raise NotImplementedError\n", "run", False),
    ],
)
def test_declares_only_reads_a_protocol_member_or_an_abstract_method_with_no_code(
    source: str, name: str, declared: bool
) -> None:
    assert declares_only(*_method(source, name)) is declared


def test_extraction_records_a_declaration_and_its_class_s_bases() -> None:
    symbols = {item.qualified_name: item for item in extract_symbols(PROTOCOL_NEW + JSON_NEW)}
    assert symbols["HistoryRepository.damaged"].declared_only is True
    assert symbols["HistoryRepository.damaged"].owner_bases == ("Protocol",)
    assert symbols["JsonHistoryRepository.damaged"].declared_only is False
    assert symbols["JsonHistoryRepository.damaged"].owner_bases == ()


def test_a_removed_declaration_stays_beside_its_removed_implementation(repo_factory) -> None:
    # What still calls a removed method is asked about either way: nothing joins.
    analysis = _analysis(
        repo_factory,
        {"repos/base.py": PROTOCOL_NEW, "repos/json_history.py": JSON_NEW},
        {"repos/base.py": PROTOCOL_OLD, "repos/json_history.py": JSON_OLD},
    )
    assert sorted(_symbols(analysis)) == [
        "HistoryRepository.damaged",
        "JsonHistoryRepository.damaged",
    ]


def test_an_implementation_may_share_the_declaring_class_s_name_in_another_module(
    repo_factory,
) -> None:
    implementation_old = JSON_OLD.replace("JsonHistoryRepository", "HistoryRepository")
    implementation_new = JSON_NEW.replace("JsonHistoryRepository", "HistoryRepository")
    analysis = _analysis(
        repo_factory,
        {"repos/base.py": PROTOCOL_OLD, "repos/json_history.py": implementation_old},
        {"repos/base.py": PROTOCOL_NEW, "repos/json_history.py": implementation_new},
    )
    (finding,) = analysis["behavior_changes"]
    assert [(item["path"], item["symbol"]) for item in finding["evidence"]] == [
        ("repos/json_history.py", "HistoryRepository.damaged"),
        ("repos/base.py", "HistoryRepository.damaged"),
    ]


def test_a_declaration_that_changed_otherwise_says_it_changed() -> None:
    behavior = BehaviorChange(
        id="bc-001",
        category=BehaviorCategory.ERROR_HANDLING,
        summary="Changed.",
        observable_impact="Something differs.",
        risk=RiskLabel.MEDIUM,
        risk_score=50,
        confidence=0.8,
        evidence=[
            Evidence(
                id="ev-001",
                path="grants/base.py",
                symbol="BaseGrant.validate",
                kind="raise_change",
                old="NotImplementedError",
                new="NotImplementedError('validate')",
            )
        ],
        origin=Origin.DETERMINISTIC,
        score_explanation=ScoreExplanation(
            behavioral_impact=50,
            critical_path_weight=0,
            test_gap_weight=90,
            change_surface_weight=10,
        ),
    )
    change = Change(
        kind="raise_change", added=("NotImplementedError('validate')",), declared_only=True
    )
    (scenario,) = evidence_scenarios(behavior, {"ev-001": change})
    assert scenario.title == "Exercise `BaseGrant.validate` through each class that implements it"
    assert scenario.given == (
        "`BaseGrant.validate` changed, which `BaseGrant` only declares, with no code of its own "
        "to run"
    )
