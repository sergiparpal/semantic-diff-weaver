"""`scripts/run_benchmark.py` and the labels of `benchmark/cases.json`."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from scripts import run_benchmark
from scripts.openai_compatible import (
    BudgetExhausted,
    CallRecord,
    ProviderUnavailable,
    StructuredResult,
)
from scripts.run_benchmark import Benchmark, Case, Finding, Replies, load, score, total
from semantic_diff_weaver.semantic_interpreter import INPUT_PREFIX, INPUT_SUFFIX
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
    assert [case.split for case in benchmark.cases].count("development") == 60
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


# Measuring the inference layer with a model.


def _behavior(category: str, evidence_ids: list[str]) -> dict[str, object]:
    return {
        "category": category,
        "summary": "As the evidence shows.",
        "observable_impact": "What the comparison admits changes.",
        "evidence_ids": evidence_ids,
        "confidence": 0.9,
    }


class ScriptedModel:
    """A model that supports each finding it is shown, at a fixed cost per call."""

    url = "https://models.example/api/v1/chat/completions"

    def __init__(
        self,
        *,
        cost: float = 0.001,
        cap: float | None = None,
        adds: Callable[[list[str]], list[dict[str, object]]] | None = None,
        fail: bool = False,
        silent: bool = False,
    ) -> None:
        self.calls: list[CallRecord] = []
        self.max_cost_usd = cap
        self.cost = cost
        self.adds = adds
        self.fail = fail
        self.silent = silent

    @property
    def spent(self) -> float:
        return sum(call.cost or 0.0 for call in self.calls)

    def complete_structured(self, **request: Any) -> StructuredResult:
        if self.max_cost_usd is not None and self.spent >= self.max_cost_usd:
            self.calls.append(CallRecord(outcome="refused_budget"))
            raise BudgetExhausted("the spending cap was reached")
        if self.fail:
            self.calls.append(CallRecord(outcome="http_503", status=503))
            raise ProviderUnavailable("the provider answered HTTP 503")
        self.calls.append(CallRecord(outcome="ok", cost=self.cost, input_tokens=100, seconds=0.5))
        groups = run_benchmark._evidence_groups(request["input"])
        shown = [item["id"] for group in groups for item in group["evidence"]]
        behaviors = [
            _behavior(group["category_hint"], [item["id"] for item in group["evidence"]])
            for group in groups
            if not self.silent
        ]
        behaviors += self.adds(shown) if self.adds else []
        return StructuredResult("json", {"behaviors": behaviors, "obligations": []})


def _measured(shas: dict[str, str], base: str, head: str, number: int, *labels: object) -> object:
    return {**_case(*labels, number=number), "base": shas[base], "head": shas[head]}


def _bound_case(shas: dict[str, str], number: int = 1) -> object:
    label = {**_label("f", ["boundary_change"]), "rationale": "The bound becomes inclusive."}
    return _measured(shas, "Initial commit", "Fix the bound (#1)", number, label)


def test_a_case_is_measured_without_a_model_and_with_one(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    _, shas = _repository(scratch)
    benchmark = Benchmark.model_validate(_benchmark(_bound_case(shas)))
    model = ScriptedModel(
        adds=lambda shown: [
            _behavior("side_effect_change", shown[:1]),
            _behavior("output_contract_change", ["ev-999"]),
        ]
    )
    [run] = run_benchmark.measure_with_model(benchmark, scratch, model, public=lambda slug: True)
    assert run.validity == "valid"
    assert run.replies == Replies(
        behaviors=3, fabricated_reference=1, fabricated_ids=1, unsupported_category=1
    )
    assert (run.unanchored, run.calls) == (0, model.calls)
    without = run_benchmark.findings_of(run.deterministic)
    with_model = run_benchmark.findings_of(run.model)
    # Without a model every finding is the deterministic fallback's.
    assert [(found.category, found.origin) for found in without] == [
        ("boundary_change", "deterministic_fallback")
    ]
    # The model supports the finding and adds an unknown change in place of the category it
    # named, which no deterministic finding on that evidence has.
    assert sorted((found.category, found.origin) for found in with_model) == [
        ("boundary_change", "llm_supported"),
        ("unknown_semantic_change", "llm_supported"),
    ]
    report = run_benchmark.render_model(
        benchmark, [run], model="scripted", cap=1.0, spent=model.spent
    )
    for line in (
        "| Measure, on the 1 valid cases | Without a model | With scripted |",
        "| Precision, findings in an expected category | 1 of 1 (100.0%) | 1 of 2 (50.0%) |",
        "| Findings only the run with the model has, valid cases | 1 |",
        "| Findings only the run without it has, valid cases | 0 |",
        "| Behaviors discarded for citing evidence never shown | 1 |",
        "| Evidence IDs cited that were never shown | 1 |",
        "| Behaviors kept in a category no evidence supports, made unknown | 1 |",
        "| Evidence in the output the analysis did not extract | 0 |",
        "| Calls | 1: ok 1 |",
        "| Cost | 0.0010 USD of a 1 USD cap |",
        f"| `owner/name#1` | valid | 1 | 1, 2 | 2 | 1 | 0.0010 | {run.seconds:.1f} |",
    ):
        assert line in report


@pytest.mark.parametrize(
    ("options", "head", "validity"),
    [
        ({"fail": True}, "Fix the bound (#1)", "failed"),
        ({"silent": True}, "Fix the bound (#1)", "unsupported"),
        ({}, "Test only (#2)", "no_call"),
    ],
)
def test_a_case_counts_only_if_the_model_answered(
    tmp_path: Path, options: dict[str, bool], head: str, validity: str
) -> None:
    scratch = tmp_path / "scratch"
    _, shas = _repository(scratch)
    base = "Initial commit" if head == "Fix the bound (#1)" else "Fix the bound (#1)"
    case = _measured(shas, base, head, 1, _label("f", ["boundary_change"]))
    benchmark = Benchmark.model_validate(_benchmark(case))
    model = ScriptedModel(**options)
    [run] = run_benchmark.measure_with_model(benchmark, scratch, model, public=lambda slug: True)
    assert run.validity == validity
    assert (len(model.calls) == 0) is (validity == "no_call")
    report = run_benchmark.render_model(benchmark, [run], model="m", cap=1.0, spent=model.spent)
    assert "| Measure, on the 0 valid cases | Without a model | With m |" in report
    assert "| Cases | 0 | 0 |" in report


def test_the_cap_stops_the_measurement_before_the_next_case(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    _, shas = _repository(scratch)
    added = {**_label("g0", [], change="added"), "path": "pkg/b.py"}
    benchmark = Benchmark.model_validate(
        _benchmark(_bound_case(shas), _measured(shas, "Test only (#2)", "Many (#3)", 3, added))
    )
    model = ScriptedModel(cost=0.01, cap=0.005)
    runs = run_benchmark.measure_with_model(benchmark, scratch, model, public=lambda slug: True)
    assert [run.validity for run in runs] == ["valid", "not_run"]
    assert len(model.calls) == 1
    report = run_benchmark.render_model(benchmark, runs, model="m", cap=0.005, spent=model.spent)
    assert "1 not run for the cap |" in report
    assert "| `owner/name#3` | not_run | | | | | | |" in report


def test_a_repository_not_served_as_public_is_never_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    benchmark = Benchmark.model_validate(_benchmark(_case(_label("f", []))))
    monkeypatch.setattr(run_benchmark, "clone", lambda slug, scratch: pytest.fail("cloned"))
    model = ScriptedModel()
    with pytest.raises(RuntimeError, match="owner/name is not a public repository"):
        run_benchmark.measure_with_model(benchmark, tmp_path, model, public=lambda slug: False)
    assert model.calls == []


@pytest.mark.parametrize(
    ("answer", "public"),
    [
        (b'{"full_name": "owner/name", "private": false}', True),
        (b'{"full_name": "owner/name", "private": true}', False),
        (b"<html>rate limited</html>", False),
        (OSError("offline"), False),
    ],
)
def test_github_is_asked_without_credentials_whether_a_repository_is_public(
    monkeypatch: pytest.MonkeyPatch, answer: bytes | OSError, public: bool
) -> None:
    requests: list[Any] = []

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self, limit: int) -> bytes:
            assert isinstance(answer, bytes)
            return answer

    def urlopen(request: Any, timeout: float) -> Response:
        requests.append(request)
        if isinstance(answer, OSError):
            raise answer
        return Response()

    monkeypatch.setattr(run_benchmark.urllib.request, "urlopen", urlopen)
    assert run_benchmark.public_repository("owner/name") is public
    assert requests[0].full_url == "https://api.github.com/repos/owner/name"
    assert requests[0].get_header("Authorization") is None


def test_a_reply_is_judged_by_the_analysis_s_rules_in_their_order() -> None:
    def framed(group: dict[str, object]) -> list[dict[str, str]]:
        document = json.dumps({"evidence_groups": [group]})
        return [{"type": "text", "text": f"{INPUT_PREFIX}{document}{INPUT_SUFFIX}"}]

    first = {"id": "ev-001", "path": "a.py", "symbol": "f", "kind": "comparison_change"}
    second = {"id": "ev-002", "path": "b.py", "symbol": "g", "kind": "call_added"}
    answers = [
        {"behaviors": [_behavior("boundary_change", ["ev-001"])], "obligations": [{}]},
        {
            "behaviors": [
                _behavior("side_effect_change", ["ev-002", "ev-002"]),
                _behavior("side_effect_change", ["ev-002", "ev-404"]),
                _behavior("boundary_change", ["ev-001"]),
                _behavior("output_contract_change", ["ev-002"]),
                _behavior("unknown_semantic_change", ["ev-002"]),
                "not a behavior",
            ],
            "obligations": "none",
        },
    ]

    class Answering:
        def complete_structured(self, **request: Any) -> dict[str, object]:
            return {"content_type": "json", "parsed": answers.pop(0)}

    recorder = run_benchmark.RecordingClient(Answering())
    recorder.complete_structured(
        input=framed({"category_hint": "boundary_change", "evidence": [first]})
    )
    recorder.complete_structured(
        input=framed({"category_hint": "side_effect_change", "evidence": [second]})
    )
    assert recorder.shown == {
        "ev-001": {"path": "a.py", "symbol": "f", "kind": "comparison_change"},
        "ev-002": {"path": "b.py", "symbol": "g", "kind": "call_added"},
    }
    assert recorder.replies() == Replies(
        behaviors=6,
        obligations=1,
        repeated_reference=1,
        fabricated_reference=1,
        other_batch_reference=1,
        unsupported_category=1,
        fabricated_ids=1,
    )


def test_evidence_the_analysis_did_not_extract_is_counted() -> None:
    record = {"id": "ev-001", "path": "a.py", "symbol": "f", "kind": "comparison_change"}
    deterministic = {"behavior_changes": [{"evidence": [record]}]}
    shown = {"ev-002": {"path": "a.py", "symbol": "g", "kind": "call_added"}}

    def findings(*evidence: dict[str, object]) -> dict[str, object]:
        return {"behavior_changes": [{"evidence": list(evidence)}]}

    shown_record = {
        "id": "ev-002",
        "path": "a.py",
        "symbol": "g",
        "kind": "call_added",
        "new": "g()",
    }
    assert run_benchmark.unanchored(findings(record, shown_record), deterministic, shown) == 0
    for wrong in (
        {**record, "new": "x < 2"},
        {**shown_record, "path": "b.py"},
        {"id": "ev-003", "path": "a.py", "kind": "comparison_change"},
    ):
        assert run_benchmark.unanchored(findings(wrong), deterministic, shown) == 1


def _model_cases(tmp_path: Path, shas: dict[str, str]) -> Path:
    held_out = {**_bound_case(shas, number=3), "split": "held_out"}
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps(_benchmark(_bound_case(shas), held_out)), encoding="utf-8")
    return cases


def test_the_command_measures_the_development_cases_and_saves_a_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scratch = tmp_path / "scratch"
    _, shas = _repository(scratch)
    model = ScriptedModel()
    monkeypatch.setattr(
        run_benchmark.OpenAICompatibleClient, "from_environment", lambda **options: model
    )
    monkeypatch.setattr(run_benchmark, "public_repository", lambda slug: True)
    arguments = [
        "llm",
        "--model",
        "vendor/model",
        "--max-cost-usd",
        "0.5",
        "--cases",
        str(_model_cases(tmp_path, shas)),
        "--scratch",
        str(scratch),
    ]
    assert run_benchmark.main(arguments) == 0
    out, err = capsys.readouterr()
    assert "| Measure, on the 1 valid cases | Without a model | With vendor/model |" in out
    path = Path(err.strip().splitlines()[-1].removeprefix("report: "))
    assert path.parent.parent == scratch.resolve() / "model-runs"
    report = json.loads(path.read_text(encoding="utf-8"))
    assert [case["id"] for case in report["cases"]] == ["owner/name#1"]
    assert (report["model"], report["endpoint"], report["cap_usd"]) == (
        "vendor/model",
        "models.example",
        0.5,
    )
    assert report["cases"][0]["validity"] == "valid"
    assert sorted(item.name for item in path.parent.iterdir()) == [
        "owner-name-1.with-model.json",
        "owner-name-1.without-model.json",
        "report.json",
    ]


def test_the_command_refuses_a_held_out_case_and_needs_a_cap_a_key_and_public_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cases = _model_cases(tmp_path, {"Initial commit": SHA, "Fix the bound (#1)": "b" * 40})
    base = ["llm", "--model", "m", "--cases", str(cases), "--scratch", str(tmp_path / "scratch")]
    for refused, message in (
        (["--max-cost-usd", "1", "--case", "owner/name#3"], "never measured with a model"),
        (["--max-cost-usd", "1", "--case", "owner/name#9"], "no such case"),
        ([], "--max-cost-usd"),
        (["--max-cost-usd", "0"], "must be more than 0"),
        (["--max-cost-usd", "1", "--limit", "0"], "positive whole number"),
    ):
        with pytest.raises(SystemExit) as exited:
            run_benchmark.main([*base, *refused])
        assert exited.value.code == 2
        assert message in capsys.readouterr().err
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert run_benchmark.main([*base, "--max-cost-usd", "1"]) == 2
    assert "no OPENROUTER_API_KEY in the environment" in capsys.readouterr().err
    monkeypatch.setattr(
        run_benchmark.OpenAICompatibleClient, "from_environment", lambda **options: ScriptedModel()
    )
    monkeypatch.setattr(run_benchmark, "public_repository", lambda slug: False)
    assert run_benchmark.main([*base, "--max-cost-usd", "1"]) == 1
    assert "not a public repository" in capsys.readouterr().err
