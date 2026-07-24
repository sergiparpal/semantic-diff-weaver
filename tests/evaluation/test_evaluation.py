from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from hermes_semantic_diff_weaver.path_policy import ALLOWED_ROOTS_ENV
from hermes_semantic_diff_weaver.service import analyze

# One Git repository and one analysis per corpus case still costs hundreds of child
# processes, which dominate the runtime on Windows runners.
pytestmark = pytest.mark.timeout(120)


def _load_cases() -> list[dict[str, object]]:
    fixture = Path(__file__).parents[1] / "fixtures" / "evaluation_cases.json"
    return json.loads(fixture.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def corpus_envelopes(
    module_repo_factory: Callable[..., tuple[Path, str, str]],
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, dict[str, object]]:
    """Analyze every corpus case once and share the envelopes across this module."""
    envelopes: dict[str, dict[str, object]] = {}
    with pytest.MonkeyPatch.context() as patch:
        # Module-scoped setup runs before the autouse per-test authorization fixture.
        patch.setenv(ALLOWED_ROOTS_ENV, str(tmp_path_factory.getbasetemp().resolve()))
        for case in _load_cases():
            old_files, new_files, remove = _files(case)
            repo, base, head = module_repo_factory(old_files, new_files, remove=remove)
            envelopes[str(case["name"])] = analyze(
                {
                    "repo_path": str(repo),
                    "base_ref": base,
                    "head_ref": head,
                    "output_format": "both",
                }
            )
    return envelopes


def test_fixture_labels_match_reviewed_golden() -> None:
    golden_path = Path(__file__).parents[1] / "fixtures" / "golden" / "evaluation_expected.json"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    labels = {case["name"]: case["expected"] for case in _load_cases()}
    assert labels == golden


def _normalize_analysis(result: dict[str, object]) -> dict[str, object]:
    normalized = json.loads(json.dumps(result, sort_keys=True))
    normalized["analysis_id"] = "<analysis-id>"
    normalized["repository"] = {
        "path": ".",
        "base_ref": "<base-ref>",
        "head_ref": "<head-ref>",
        "base_commit": "<base-commit>",
        "head_commit": "<head-commit>",
    }
    return normalized


def test_canonical_outputs_match_reviewed_goldens(
    corpus_envelopes: dict[str, dict[str, object]],
) -> None:
    golden_path = Path(__file__).parents[1] / "fixtures" / "golden" / "canonical_outputs.json"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    actual = {
        name: _normalize_analysis(envelope["analysis"])
        for name, envelope in corpus_envelopes.items()
    }
    assert actual == golden


def _files(case: dict[str, object]) -> tuple[dict[str, str], dict[str, str], tuple[str, ...]]:
    if case.get("generator") == "oversized_critical":
        config = (
            "version: 1\n"
            "critical_paths:\n  - pattern: 'critical.py'\n    weight: 100\n"
            "rules:\n  max_changed_files: 1\n  max_diff_lines: 2\n"
        )
        return (
            {
                "critical.py": "def critical(x):\n    return x < 1\n",
                "other.py": "def other(x):\n    return x < 1\n",
                ".semantic-diff-weaver.yaml": config,
            },
            {
                "critical.py": "def critical(x):\n    return x <= 1\n",
                "other.py": "def other(x):\n    return x <= 1\n",
                ".semantic-diff-weaver.yaml": config,
            },
            (),
        )
    return (
        dict(case["old_files"]),
        dict(case["new_files"]),
        tuple(case.get("remove", [])),
    )


def test_material_metrics_evidence_anchors_and_obligation_concepts(
    corpus_envelopes: dict[str, dict[str, object]],
) -> None:
    true_positive = 0
    predicted_total = 0
    expected_total = 0
    anchor_total = 0
    anchors_matched = 0
    concepts_total = 0
    concepts_matched = 0
    fabricated = 0
    unknowns = 0
    for case in _load_cases():
        envelope = corpus_envelopes[str(case["name"])]
        result = envelope["analysis"]
        predicted = {item["category"] for item in result["behavior_changes"]}
        expected = set(case["expected"])
        true_positive += len(predicted & expected)
        predicted_total += len(predicted)
        expected_total += len(expected)
        unknowns += int("unknown_semantic_change" in predicted)

        evidence = [
            item for behavior in result["behavior_changes"] for item in behavior["evidence"]
        ]
        registry: dict[str, dict[str, object]] = {}
        for item in evidence:
            previous = registry.setdefault(item["id"], item)
            fabricated += int(previous != item or not item["id"].startswith("ev-"))
        for anchor in case["anchors"]:
            anchor_total += 1
            anchors_matched += int(
                any(
                    item["path"] == anchor["path"]
                    and item["symbol"] == anchor["symbol"]
                    and item["kind"] == anchor["kind"]
                    for item in evidence
                )
            )

        obligation_text = " ".join(
            f"{item['title']} {item['given']} {item['when']} {item['then']}".casefold()
            for item in result["test_obligations"]
        )
        for concept in case["obligation_concepts"]:
            concepts_total += 1
            concepts_matched += int(concept.casefold() in obligation_text)
        linked = {
            behavior_id
            for obligation in result["test_obligations"]
            for behavior_id in obligation["behavior_change_ids"]
        }
        assert all(
            behavior["id"] in linked
            for behavior in result["behavior_changes"]
            if behavior["risk"] in {"high", "critical"}
        )
        assert all(
            candidate["verified"] is False
            for obligation in result["test_obligations"]
            for candidate in obligation["candidate_existing_tests"]
        )
        assert "do not verify runtime coverage" in envelope["markdown"]
        if omission := case.get("expected_omission"):
            assert any(item["reason"] == omission for item in result["scope"]["omitted"])
        if limitation := case.get("expected_limitation"):
            assert any(limitation in item for item in result["limitations"])

    precision = true_positive / predicted_total
    recall = true_positive / expected_total
    evidence_correctness = anchors_matched / anchor_total
    obligation_concept_match = concepts_matched / concepts_total
    assert precision >= 0.80
    assert recall >= 0.70
    assert evidence_correctness == 1.0
    assert obligation_concept_match == 1.0
    assert fabricated == 0
    assert unknowns >= 1
