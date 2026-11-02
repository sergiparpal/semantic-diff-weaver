"""`scripts/run_benchmark.py` and the labels of `benchmark/cases.json`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts import run_benchmark
from scripts.run_benchmark import Benchmark, Case, Finding, load, score, total
from tests.conftest import git

SHA = "a" * 40


def _label(symbol: str, expected: list[str], change: str = "modified") -> dict[str, object]:
    return {
        "path": "pkg/a.py",
        "symbol": symbol,
        "change": change,
        "expected": expected,
        "rationale": "why",
    }


def _case(*labels: dict[str, object], number: int = 1) -> dict[str, object]:
    return {
        "id": f"owner/name#{number}",
        "repository": "owner/name",
        "pull_request": number,
        "base": SHA,
        "head": "b" * 40,
        "subject": "Fix (#1)",
        "split": "development",
        "labels": list(labels),
    }


def _benchmark(*cases: dict[str, object]) -> dict[str, object]:
    return {
        "annotation": "one annotator",
        "sampled_from": {"owner/name": SHA},
        "cases": list(cases),
    }


def test_the_published_labels_are_valid() -> None:
    benchmark = load()
    assert [case.split for case in benchmark.cases].count("development") == 30
    assert [case.split for case in benchmark.cases].count("held_out") == 30
    assert {case.repository for case in benchmark.cases} == set(benchmark.sampled_from)
    for case in benchmark.cases:
        assert all(label.path.endswith(".py") for label in case.labels)


@pytest.mark.parametrize(
    ("document", "error"),
    [
        pytest.param(
            _benchmark(_case(_label("f", ["unknown_semantic_change"]))),
            "what a change",
            id="unknown",
        ),
        pytest.param(
            _benchmark(_case(_label("f", ["refactor_likely_no_behavior_change"]))),
            "what a change",
            id="refactor",
        ),
        pytest.param(
            _benchmark(_case(_label("f", ["boundary_change", "boundary_change"]))),
            "distinct",
            id="repeated",
        ),
        pytest.param(
            _benchmark(_case(_label("f", []), _label("f", []))), "twice", id="symbol-twice"
        ),
        pytest.param(
            _benchmark({**_case(_label("f", [])), "base": "abc"}), "40-character", id="short-sha"
        ),
        pytest.param(
            _benchmark({**_case(_label("f", [])), "id": "x#1"}), "OWNER/NAME#NUMBER", id="bad-id"
        ),
        pytest.param(
            _benchmark({**_case(_label("f", [])), "repository": "no-slug"}), "slug", id="no-slug"
        ),
        pytest.param(
            _benchmark(_case(_label("f", [])), _case(_label("f", []))), "twice", id="case-twice"
        ),
        pytest.param(
            {**_benchmark(_case(_label("f", []))), "sampled_from": {}},
            "sampled from",
            id="unsampled",
        ),
    ],
)
def test_malformed_labels_are_refused(document: dict[str, object], error: str) -> None:
    with pytest.raises(ValidationError, match=error):
        Benchmark.model_validate(document)


def test_each_finding_and_each_label_is_scored() -> None:
    case = Case.model_validate(
        _case(
            _label("changed", ["boundary_change"]),
            _label("unchanged", []),
            _label("missed", ["validation_change"]),
            _label("added", [], change="added"),
            _label("tidied", []),
        )
    )
    findings = [
        Finding("pkg/a.py", "changed", "boundary_change"),
        Finding("pkg/a.py", "changed", "unknown_semantic_change"),
        Finding("pkg/a.py", "unchanged", "unknown_semantic_change"),
        Finding("pkg/a.py", "tidied", "refactor_likely_no_behavior_change"),
        Finding("pkg/a.py", "added", "unknown_semantic_change"),
        Finding("pkg/a.py", "elsewhere", "side_effect_change"),
        Finding("api/openapi.yaml", "GET /x", "output_contract_change"),
    ]
    scored = score(case, findings)
    assert [outcome for _, outcome in scored.findings] == [
        "correct",
        "wrong_category",
        "false_positive",
        "correct",
        "unscored",
        "unlabeled",
        "out_of_scope",
    ]
    assert scored.detected == {
        ("pkg/a.py", "changed"): True,
        ("pkg/a.py", "unchanged"): True,
        ("pkg/a.py", "missed"): False,
        ("pkg/a.py", "added"): True,
        # Saying no behavior changed claims none did.
        ("pkg/a.py", "tidied"): False,
    }
    benchmark = Benchmark.model_validate(_benchmark(case.model_dump()))
    totals = total(benchmark, [scored])
    assert (totals.changes, totals.detected, totals.right) == (2, 1, 1)
    assert (totals.unchanged, totals.flagged, totals.misnamed) == (2, 1, 0)
    report = run_benchmark.render(benchmark, [scored], totals)
    assert "| Precision, findings in an expected category | 2 of 4 (50.0%) |" in report
    assert "| `owner/name#1` | 5 | 7 | 2 | 1 | 1 | 1 |" in report
    assert run_benchmark.disagreements(benchmark, [scored]) == [
        "owner/name#1 pkg/a.py changed: wrong_category, found unknown_semantic_change, "
        "labeled boundary_change",
        "owner/name#1 pkg/a.py unchanged: false_positive, found unknown_semantic_change, "
        "labeled no behavior change",
        "owner/name#1 pkg/a.py elsewhere: unlabeled, found side_effect_change, labeled nothing",
        "owner/name#1 pkg/a.py missed: missed, labeled validation_change",
    ]


def test_an_empty_total_reads_as_none() -> None:
    assert run_benchmark._rate(0, 0) == "none"


def test_a_finding_without_a_symbol_is_the_module_s() -> None:
    analysis = {
        "behavior_changes": [
            {"category": "unknown_semantic_change", "evidence": [{"path": "a.py", "symbol": None}]}
        ]
    }
    assert run_benchmark.findings_of(analysis) == [
        Finding("a.py", "<module>", "unknown_semantic_change")
    ]


def _repository(scratch: Path) -> tuple[Path, dict[str, str]]:
    """A clone, as `clone` would leave one in the scratch directory, with pull requests."""
    repo = scratch / "owner__name"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    shas = {}
    steps = [
        ("pkg/a.py", "def f(x):\n    return x < 1\n", "Initial commit"),
        ("pkg/a.py", "def f(x):\n    return x <= 1\n", "Fix the bound (#1)"),
        ("tests/test_a.py", "def test_f():\n    pass\n", "Test only (#2)"),
        ("pkg/b.py", "".join(f"def g{n}():\n    return {n}\n" for n in range(3)), "Many (#3)"),
    ]
    for name, text, message in steps:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        git(repo, "add", "--all")
        git(repo, "commit", "-q", "-m", message)
        shas[message] = git(repo, "rev-parse", "HEAD")
    return repo, shas


def test_proposals_are_counted_never_shown(tmp_path: Path, capsys) -> None:
    scratch = tmp_path / "scratch"
    _, shas = _repository(scratch)
    arguments = ["propose", "--repo", "owner/name", "--scratch", str(scratch), "--max-symbols", "2"]
    assert run_benchmark.main(arguments) == 0
    proposed = json.loads(capsys.readouterr().out)["owner/name"]
    assert proposed["sampled_from"] == shas["Many (#3)"]
    # The tests-only pull request changes no analyzed Python, and the last changes three symbols.
    assert [(item["pull_request"], item["changed_symbols"]) for item in proposed["candidates"]] == [
        (1, 1)
    ]
    assert "behavior_changes" not in json.dumps(proposed)


def test_a_case_is_measured_against_a_local_clone(tmp_path: Path, capsys) -> None:
    scratch = tmp_path / "scratch"
    _, shas = _repository(scratch)
    case = {
        **_case(
            {
                "path": "pkg/a.py",
                "symbol": "f",
                "change": "modified",
                "expected": ["boundary_change"],
                "rationale": "The bound becomes inclusive.",
            }
        ),
        "base": shas["Initial commit"],
        "head": shas["Fix the bound (#1)"],
    }
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps(_benchmark(case)), encoding="utf-8")
    arguments = ["--cases", str(cases), "--scratch", str(scratch), "--disagreements"]
    assert run_benchmark.main(arguments) == 0
    report = capsys.readouterr().out
    assert (
        "| Recall, behavior changes given one in an expected category | 1 of 1 (100.0%) |" in report
    )
    # A split measures its own cases alone.
    assert run_benchmark.main([*arguments, "--split", "held_out"]) == 0
    assert "| Cases | 0 |" in capsys.readouterr().out


def test_a_scratch_directory_inside_the_checkout_is_refused() -> None:
    with pytest.raises(SystemExit, match="outside this working tree"):
        run_benchmark._scratch(str(run_benchmark.REPOSITORY_ROOT / "build"))


def test_an_unreachable_repository_stops_the_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    benchmark = Benchmark.model_validate(_benchmark(_case(_label("f", []))))
    monkeypatch.setattr(run_benchmark, "clone", lambda slug, scratch: None)
    with pytest.raises(RuntimeError, match="could not be cloned"):
        run_benchmark.measure(benchmark, tmp_path)
    monkeypatch.setattr(run_benchmark, "clone", lambda slug, scratch: tmp_path)
    monkeypatch.setattr(run_benchmark, "analyze", lambda repo, base, head: None)
    with pytest.raises(RuntimeError, match="could not be analyzed"):
        run_benchmark.measure(benchmark, tmp_path)
    monkeypatch.setattr(run_benchmark, "clone", lambda slug, scratch: None)
    assert run_benchmark.main(["propose", "--repo", "owner/name", "--scratch", str(tmp_path)]) == 1
