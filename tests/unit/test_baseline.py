"""An earlier analysis, read as untrusted data and compared finding by finding."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from semantic_diff_weaver.baseline import compare_with_baseline, load_baseline
from semantic_diff_weaver.errors import ErrorCode, WeaverError
from semantic_diff_weaver.models import (
    BehaviorCategory,
    BehaviorChange,
    Evidence,
    LineRange,
    Origin,
    RiskLabel,
    ScoreExplanation,
)

LIMIT = 1_000_000


def _evidence(symbol: str, new: str, *, line: int = 1, kind: str = "comparison_change") -> dict:
    return {
        "id": "ev-001",
        "path": "src/app.py",
        "symbol": symbol,
        "kind": kind,
        "old": "x < 5",
        "new": new,
        "new_lines": {"start": line, "end": line},
    }


def _baseline(*behaviors: dict[str, Any], schema_version: str = "1.1") -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "analysis_id": "sdw_earlier",
        "repository": {"head_commit": "a" * 40},
        "behavior_changes": list(behaviors),
    }


def _behavior(symbol: str, new: str, category: str = "boundary_change", **evidence: Any) -> dict:
    return {
        "id": "bc-001",
        "category": category,
        "risk": "medium",
        "summary": "A comparison boundary appears to have changed.",
        "evidence": [_evidence(symbol, new, **evidence)],
    }


def _write(tmp_path: Path, document: Any) -> Path:
    path = tmp_path / "baseline.json"
    path.write_text(document if isinstance(document, str) else json.dumps(document), "utf-8")
    return path


def _current(number: int, symbol: str, new: str, category: str = "boundary_change", **kw: Any):
    raw = _evidence(symbol, new, **kw)
    return BehaviorChange(
        id=f"bc-{number:03d}",
        category=BehaviorCategory(category),
        summary="A comparison boundary appears to have changed.",
        observable_impact="Inputs at the boundary may now be routed differently.",
        risk=RiskLabel.MEDIUM,
        risk_score=50,
        confidence=0.9,
        evidence=[
            Evidence(
                id="ev-001",
                path=raw["path"],
                symbol=raw["symbol"],
                kind=raw["kind"],
                old=raw["old"],
                new=raw["new"],
                new_lines=LineRange(**raw["new_lines"]),
            )
        ],
        origin=Origin.DETERMINISTIC,
        score_explanation=ScoreExplanation(
            behavioral_impact=64,
            critical_path_weight=10,
            test_gap_weight=90,
            change_surface_weight=30,
        ),
    )


@pytest.mark.parametrize("wrapped", [True, False], ids=["both-envelope", "json-analysis"])
def test_both_json_outputs_are_read(tmp_path: Path, wrapped: bool) -> None:
    analysis = _baseline(_behavior("allowed", "x <= 5"))
    document = {"success": True, "analysis": analysis, "markdown": "brief"} if wrapped else analysis
    loaded = load_baseline(_write(tmp_path, document), LIMIT)
    assert (loaded.analysis_id, len(loaded.behavior_changes)) == ("sdw_earlier", 1)


@pytest.mark.parametrize(
    ("document", "message"),
    [
        pytest.param({"success": True, "markdown": "brief"}, "holds no analysis", id="markdown"),
        pytest.param("{not json", "not valid JSON", id="not-json"),
        pytest.param([1, 2], "holds no analysis", id="not-an-object"),
        pytest.param({"behavior_changes": []}, "expected shape", id="missing-fields"),
        pytest.param(_baseline(schema_version="2.0"), "only 1.x", id="another-major"),
        pytest.param(
            _baseline({**_behavior("f", "x"), "category": "invented"}),
            "expected shape",
            id="unknown-category",
        ),
        pytest.param(
            _baseline(
                {**_behavior("f", "x"), "evidence": [{**_evidence("f", "x"), "path": "../up.py"}]}
            ),
            "expected shape",
            id="path-outside-the-repository",
        ),
    ],
)
def test_an_unusable_baseline_is_a_baseline_error(
    tmp_path: Path, document: Any, message: str
) -> None:
    with pytest.raises(WeaverError, match=message) as caught:
        load_baseline(_write(tmp_path, document), LIMIT)
    assert caught.value.code is ErrorCode.BASELINE_UNREADABLE


def test_an_oversized_or_unreadable_baseline_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, _baseline(_behavior("allowed", "x <= 5")))
    assert path.stat().st_size > 100
    with pytest.raises(WeaverError, match="above the 100-byte limit"):
        load_baseline(path, 100)
    with pytest.raises(WeaverError, match="not a regular file"):
        load_baseline(tmp_path, LIMIT)
    with pytest.raises(WeaverError, match="inaccessible"):
        load_baseline(tmp_path / "missing.json", LIMIT)
    binary = tmp_path / "binary.json"
    binary.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(WeaverError, match="UTF-8"):
        load_baseline(binary, LIMIT)


def test_findings_are_new_updated_unchanged_or_resolved(tmp_path: Path) -> None:
    baseline = load_baseline(
        _write(
            tmp_path,
            _baseline(
                _behavior("kept", "x <= 5"),
                _behavior("moved", "x <= 5"),
                _behavior("edited", "x <= 5"),
                _behavior("gone", "x <= 5"),
            ),
        ),
        LIMIT,
    )
    current = [
        _current(1, "kept", "x <= 5"),
        # Only its line moved, after an unrelated edit above it: the same change.
        _current(2, "moved", "x <= 5", line=40),
        _current(3, "edited", "x <= 6"),
        _current(4, "added", "x <= 5"),
    ]
    comparison = compare_with_baseline(baseline, current)
    assert (comparison.unchanged, comparison.updated, comparison.new) == (
        ["bc-001", "bc-002"],
        ["bc-003"],
        ["bc-004"],
    )
    assert [(item.symbol, item.category, item.risk) for item in comparison.resolved] == [
        ("gone", BehaviorCategory.BOUNDARY, RiskLabel.MEDIUM)
    ]
    assert (comparison.analysis_id, comparison.head_commit) == ("sdw_earlier", "a" * 40)


def test_a_changed_category_is_a_new_finding_and_a_resolved_one(tmp_path: Path) -> None:
    baseline = load_baseline(_write(tmp_path, _baseline(_behavior("f", "x is None"))), LIMIT)
    comparison = compare_with_baseline(
        baseline, [_current(1, "f", "x is None", category="unknown_semantic_change")]
    )
    assert comparison.new == ["bc-001"]
    assert [item.category for item in comparison.resolved] == [BehaviorCategory.BOUNDARY]


def test_a_repeated_identity_in_a_crafted_baseline_counts_once(tmp_path: Path) -> None:
    baseline = load_baseline(
        _write(tmp_path, _baseline(_behavior("f", "x <= 5"), _behavior("f", "x <= 9"))), LIMIT
    )
    comparison = compare_with_baseline(baseline, [])
    assert len(comparison.resolved) == 1


def test_a_baseline_that_grows_past_the_limit_during_the_read_is_refused(
    tmp_path: Path, monkeypatch
) -> None:
    """`stat` and `read` are separate syscalls, so the read is bounded on its own."""
    path = _write(tmp_path, json.dumps(_baseline()) + " " * 4000)
    real_stat = Path.stat

    class Understated:
        """A stat result that under-reports size and delegates everything else."""

        def __init__(self, real: object) -> None:
            self._real = real
            self.st_size = 64

        def __getattr__(self, name: str) -> object:
            return getattr(self._real, name)

    def understate(self: Path, *args: object, **kwargs: object) -> object:
        result = real_stat(self, *args, **kwargs)
        return Understated(result) if self.name == "baseline.json" else result

    monkeypatch.setattr(Path, "stat", understate)
    with pytest.raises(WeaverError, match="while being read") as caught:
        load_baseline(path, 1024)
    assert caught.value.code is ErrorCode.BASELINE_UNREADABLE
