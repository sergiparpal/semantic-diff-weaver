"""A change that leaves a failure partway to survive names the steps its test should make fail.

The Action's field test on meal-manager missed two such tests in three pull requests:
- `atomic_write_json` came to move an unreadable file aside through a new helper before
  replacing the original with a temporary file. Its obligation asked for "the side-effect
  occurrence of the changed call", which the user judged noise. The user asked instead for each
  step to fail in turn, with the original or its copy kept and the error propagated.
- `HANDLER` came to put a retracted event back when restoring the fridge failed, to report a
  failure of that, and to raise again what it caught. Its obligation said only "Exercise the
  changed failure trigger".
"""

from __future__ import annotations

import ast

import pytest

from semantic_diff_weaver.ast_diff import analyze_ast, limits
from semantic_diff_weaver.ast_diff.analyze import attach_reached_steps
from semantic_diff_weaver.ast_diff.extract import (
    extract_symbols,
    file_operation,
    file_steps,
    handled_failures,
)
from semantic_diff_weaver.ast_diff.types import (
    BARE_RAISE,
    CAUGHT_AGAIN,
    StructuralDelta,
    SymbolSnapshot,
    at_risk_midway,
)
from semantic_diff_weaver.evidence_scenarios import (
    SECOND_SCENARIO_RELEVANCE_DROP,
    Change,
    changes_by_evidence,
    describe_change,
    evidence_scenarios,
    weakly_anchored,
)
from semantic_diff_weaver.models import (
    BehaviorCategory,
    BehaviorChange,
    Evidence,
    ObligationType,
    Origin,
    RiskLabel,
    ScoreExplanation,
    WeaverConfig,
)
from semantic_diff_weaver.obligations import generate_obligations
from semantic_diff_weaver.semantic_candidates import SemanticCandidate, build_candidates
from semantic_diff_weaver.source import SourceHunk, SourceRevisionPair

WIDE_HUNK = SourceHunk(id="hunk-001", old_start=1, old_count=400, new_start=1, new_count=400)


def _function(source: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    function = ast.parse(source).body[0]
    assert isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
    return function


def _call(source: str) -> ast.Call:
    expression = ast.parse(source, mode="eval").body
    assert isinstance(expression, ast.Call)
    return expression


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


def _deltas(old: str, new: str) -> list[StructuralDelta]:
    pair = SourceRevisionPair(
        path="m.py",
        old_path="m.py",
        new_path="m.py",
        hunks=(WIDE_HUNK,),
        old_text=old,
        new_text=new,
    )
    return analyze_ast([pair]).deltas


def _findings(old: str, new: str) -> tuple[list[BehaviorChange], dict[str, Change]]:
    deltas = _deltas(old, new)
    behaviors = [_behavior(item, index) for index, item in enumerate(build_candidates(deltas), 1)]
    return behaviors, changes_by_evidence(deltas)


def _manual(category: BehaviorCategory, kind: str) -> BehaviorChange:
    candidate = SemanticCandidate(
        path="m.py",
        symbol="f",
        category=category,
        summary="Changed.",
        observable_impact="Something differs.",
        evidence=(Evidence(id="ev-001", path="m.py", symbol="f", kind=kind, old="a", new="b"),),
        confidence_baseline=0.5,
    )
    return _behavior(candidate)


# The field test's `HANDLER`, reduced: a retracted event is put back when restoring fails.
UNDO_OLD = (
    "def handle(event, fridge, history):\n"
    "    history.retract(event)\n"
    "    fridge.restore(event)\n"
    "    return event\n"
)
UNDO_NEW = (
    "def handle(event, fridge, history):\n"
    "    history.retract(event)\n"
    "    try:\n"
    "        fridge.restore(event)\n"
    "    except Exception:\n"
    "        try:\n"
    "            history.reinstate(event)\n"
    "        except Exception:\n"
    "            logger.exception('could not reinstate %s', event)\n"
    "        raise\n"
    "    return event\n"
)
# The field test's `atomic_write_json`, reduced: an unreadable file is moved aside through a new
# helper before the temporary file replaces the original.
STEPS_OLD = (
    "import json, os, tempfile\n"
    "def write(path, data):\n"
    "    fd, tmp = tempfile.mkstemp()\n"
    "    with os.fdopen(fd, 'w') as f:\n"
    "        json.dump(data, f)\n"
    "    os.replace(tmp, path)\n"
)
STEPS_NEW = (
    "import json, os, tempfile\n"
    "def _keep(path):\n"
    "    os.replace(path, path + '.bad')\n"
    "\n"
    "def write(path, data):\n"
    "    fd, tmp = tempfile.mkstemp()\n"
    "    with os.fdopen(fd, 'w') as f:\n"
    "        json.dump(data, f)\n"
    "    _keep(path)\n"
    "    os.replace(tmp, path)\n"
)


def test_a_handler_that_puts_a_step_back_and_raises_again_is_recorded() -> None:
    assert handled_failures(_function(UNDO_NEW)) == (
        ("Exception", ("fridge.restore",), ("history.reinstate",), True, CAUGHT_AGAIN),
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # A new exception, built from what it caught, after the response is closed.
        (
            "def fetch(url):\n"
            "    try:\n"
            "        return urlopen(url).read()\n"
            "    except (URLError, TimeoutError) as error:\n"
            "        error.close()\n"
            "        raise ClientError(str(error)) from error\n",
            (
                "(URLError, TimeoutError)",
                ("urlopen",),
                ("error.close",),
                False,
                "ClientError",
            ),
        ),
        # The name it bound is what it caught; an awaited call is made for what it does.
        (
            "async def run(conn):\n"
            "    try:\n"
            "        await conn.execute()\n"
            "    except DatabaseError as error:\n"
            "        await conn.rollback()\n"
            "        raise error\n",
            ("DatabaseError", ("conn.execute",), ("conn.rollback",), False, CAUGHT_AGAIN),
        ),
        # A bare `except` catches anything, and a raise of no name is quoted as code.
        (
            "def first(errors):\n"
            "    try:\n"
            "        step()\n"
            "    except:\n"
            "        undo()\n"
            "        raise errors[0]\n",
            ("", ("step",), ("undo",), False, "errors[0]"),
        ),
        # An `except*` group handler counts too.
        (
            "def group():\n"
            "    try:\n"
            "        step()\n"
            "    except* OSError:\n"
            "        undo()\n"
            "        raise\n",
            ("OSError", ("step",), ("undo",), False, CAUGHT_AGAIN),
        ),
        # A call on what another call returns is quoted as code.
        (
            "def run(connect):\n"
            "    try:\n"
            "        step()\n"
            "    except OSError:\n"
            "        connect().rollback()\n"
            "        raise\n",
            ("OSError", ("step",), ("connect().rollback",), False, CAUGHT_AGAIN),
        ),
        # A class called by its name builds a value, which is no step that fails: pyjwt's
        # `fetch_data` built a `Request` before `urlopen` sent it.
        (
            "def fetch(url):\n"
            "    try:\n"
            "        request = urllib.request.Request(url)\n"
            "        return json.load(urllib.request.urlopen(request))\n"
            "    except OSError as error:\n"
            "        error.close()\n"
            "        raise FetchError() from error\n",
            (
                "OSError",
                ("urllib.request.urlopen", "json.load"),
                ("error.close",),
                False,
                "FetchError",
            ),
        ),
        # A private class builds a value too, and a list's `append` works in memory.
        (
            "def fetch(handlers, url):\n"
            "    try:\n"
            "        handlers.append(_NoRedirect())\n"
            "        opener = build_opener(*handlers)\n"
            "        return opener.open(url)\n"
            "    except OSError:\n"
            "        cleanup()\n"
            "        raise\n",
            ("OSError", ("build_opener", "opener.open"), ("cleanup",), False, CAUGHT_AGAIN),
        ),
    ],
)
def test_what_a_handler_raises_and_undoes_is_read_from_its_code(
    source: str, expected: tuple[object, ...]
) -> None:
    (recorded,) = handled_failures(_function(source))
    assert recorded == expected


@pytest.mark.parametrize(
    "source",
    [
        # It asks something, and returns or raises on the answer: it undoes nothing.
        "def handle(repo):\n"
        "    try:\n"
        "        return repo.load()\n"
        "    except DataError:\n"
        "        if repo.damaged():\n"
        "            return []\n"
        "        raise\n",
        # It only reports before raising.
        "def handle(repo):\n"
        "    try:\n"
        "        repo.save()\n"
        "    except OSError:\n"
        "        logger.warning('could not save')\n"
        "        raise\n",
        # It recovers: its last statement raises nothing.
        "def handle(repo):\n"
        "    try:\n"
        "        repo.save()\n"
        "    except OSError:\n"
        "        repo.discard()\n"
        "        return None\n",
        # What a function it defines would undo runs only when that function is called.
        "def handle(repo):\n"
        "    try:\n"
        "        repo.save()\n"
        "    except OSError:\n"
        "        def later():\n"
        "            repo.discard()\n"
        "        raise\n",
        # What it closes for some of the errors it catches alone, it does not undo whenever it
        # runs: pyjwt's `fetch_data` closed an `HTTPError` and nothing else it caught.
        "def fetch(url):\n"
        "    try:\n"
        "        return urlopen(url).read()\n"
        "    except (URLError, HTTPException) as error:\n"
        "        if isinstance(error, HTTPError):\n"
        "            error.close()\n"
        "        raise ClientError(str(error)) from error\n",
    ],
)
def test_a_handler_that_undoes_nothing_before_raising_is_not_recorded(source: str) -> None:
    assert handled_failures(_function(source)) == ()


def test_what_a_handler_undoes_is_what_it_runs_whenever_it_runs() -> None:
    source = (
        "def handle(repo, kind):\n"
        "    try:\n"
        "        repo.save()\n"
        "        repo.save()\n"
        "        logger.info('saved')\n"
        "        repo.hooks[0]()\n"
        "    except OSError:\n"
        "        if kind:\n"
        "            repo.undo_if()\n"
        "        else:\n"
        "            repo.undo_else()\n"
        "        for item in repo.items:\n"
        "            item.release()\n"
        "        while repo.pending:\n"
        "            repo.drain()\n"
        "        match kind:\n"
        "            case 'a':\n"
        "                repo.undo_case()\n"
        "        with repo.lock:\n"
        "            repo.unlock()\n"
        "        try:\n"
        "            repo.try_undo()\n"
        "        except KeyError:\n"
        "            repo.fallback()\n"
        "        else:\n"
        "            repo.after()\n"
        "        finally:\n"
        "            repo.close()\n"
        "        repo.forget()\n"
        "        raise\n"
    )
    ((_, guarded, work, reported, _),) = handled_failures(_function(source))
    # The guarded calls come once each, a report and a call through a value aside.
    assert guarded == ("repo.save",)
    # A `with` block, the code a nested `try` guards, and its `finally` run whenever the handler
    # does. A branch, a loop, a case, a nested handler, and an `else` run only sometimes. At most
    # `MAX_HANDLER_CALLS` of what it undoes are named, in order.
    assert work == ("repo.unlock", "repo.try_undo", "repo.close")
    assert len(work) == limits.MAX_HANDLER_CALLS
    # A `try` whose handler undoes something does not merely report a failure of what it guards.
    assert reported is False


def test_a_callable_records_at_most_so_many_handlers() -> None:
    handler = "    try:\n        step()\n    except OSError:\n        undo()\n        raise\n"
    source = "def many():\n" + handler * (limits.MAX_HANDLED_FAILURES + 2)
    assert len(handled_failures(_function(source))) == limits.MAX_HANDLED_FAILURES


@pytest.mark.parametrize(
    ("call", "kind"),
    [
        ("os.replace(tmp, path)", "move"),
        ("shutil.move(a, b)", "move"),
        ("path.rename(target)", "move"),
        ("path.replace(target)", "move"),
        ("shutil.copy2(a, b)", "copy"),
        ("os.unlink(path)", "delete"),
        ("path.unlink(missing_ok=True)", "delete"),
        ("tempfile.mkstemp(dir=folder)", "temp"),
        ("json.dump(data, handle)", "write"),
        ("os.fsync(handle.fileno())", "write"),
        ("path.write_text(text)", "write"),
        ("open(path, 'w')", "write"),
        ("open(path, mode='a', encoding='utf-8')", "write"),
        ("os.fdopen(fd, 'wb')", "write"),
        ("path.open('r+')", "write"),
        ("open(path)", None),
        ("open(path, mode)", None),
        ("path.open()", None),
        ("text.replace('a', 'b')", None),
        ("moment.replace(year=2000)", None),
        ("frame.rename(columns=names)", None),
        ("compute(path)", None),
    ],
)
def test_a_call_is_read_as_a_file_step_by_its_name_and_arguments(
    call: str, kind: str | None
) -> None:
    assert file_operation(_call(call)) == kind


def test_a_callable_s_own_file_steps_are_recorded_in_order() -> None:
    source = (
        "def write(path, data):\n"
        "    fd, tmp = tempfile.mkstemp()\n"
        "    with os.fdopen(fd, 'w') as handle:\n"
        "        json.dump(data, handle)\n"
        "    cleanup = lambda: os.unlink(tmp)\n"
        "    os.replace(tmp, path)\n"
    )
    assert file_steps(_function(source)) == (
        ("temp", "tempfile.mkstemp", 2),
        ("write", "os.fdopen", 3),
        ("write", "json.dump", 4),
        ("move", "os.replace", 6),
    )
    many = "def many(a, b):\n" + "    os.replace(a, b)\n" * (limits.MAX_FILE_STEPS + 3)
    assert len(file_steps(_function(many))) == limits.MAX_FILE_STEPS


def _reached(source: str) -> dict[str, SymbolSnapshot]:
    snapshots = extract_symbols(source)
    attach_reached_steps(snapshots)
    return {snapshot.qualified_name: snapshot for snapshot in snapshots}


def test_a_callable_reaches_the_file_steps_of_the_functions_it_calls() -> None:
    reached = _reached(
        "import os\n"
        "from typing import Protocol\n"
        "class Store(Protocol):\n"
        "    def put(self, path): ...\n"
        "class Disk:\n"
        "    def put(self, path):\n"
        "        os.replace(path + '.tmp', path)\n"
        "    def save(self, path):\n"
        "        self._prepare(path)\n"
        "        self.put(path)\n"
        "    def _prepare(self, path):\n"
        "        os.remove(path + '.old')\n"
        "def use(store, path):\n"
        "    store.put(path)\n"
        "    os.unlink(path)\n"
    )
    save = reached["Disk.save"]
    assert save.reached_steps == (
        ("delete", "os.remove", "Disk._prepare"),
        ("move", "os.replace", "Disk.put"),
    )
    assert save.reached_steps_total == 2
    assert save.step_calls == ("Disk._prepare", "Disk.put")
    # A Protocol's declaration has nothing to run, so `put` names its one implementation.
    assert reached["use"].reached_steps == (
        ("move", "os.replace", "Disk.put"),
        ("delete", "os.unlink", ""),
    )
    assert reached["use"].step_calls == ("Disk.put", "os.unlink")
    # Neither a declaration nor a class takes steps.
    assert reached["Store.put"].reached_steps == ()
    assert reached["Disk"].reached_steps == ()


def test_a_call_is_followed_only_where_it_names_one_function_close_enough() -> None:
    reached = _reached(
        "import os\n"
        "def deep(path):\n    one(path)\n"
        "def one(path):\n    two(path)\n"
        "def two(path):\n    three(path)\n"
        "def three(path):\n    os.remove(path)\n"
        "def shared(path):\n    os.remove(path)\n"
        "class Other:\n"
        "    def shared(self, path):\n        os.remove(path)\n"
        "    def own(self, path):\n        self.missing(path)\n"
        "def caller(path):\n    shared(path)\n"
        "def lonely(path):\n    self.shared(path)\n"
        "def ping(path):\n    pong(path)\n    os.remove(path)\n"
        "def pong(path):\n    ping(path)\n"
        "def again(path):\n    again(path)\n    os.remove(path)\n"
    )
    # `REACHED_STEPS_DEPTH` calls deep: `deep` would reach `three` through a third.
    assert limits.REACHED_STEPS_DEPTH == 2
    assert reached["deep"].reached_steps == ()
    # A step is named with the function its caller calls, however deep it is taken.
    assert reached["one"].reached_steps == (("delete", "os.remove", "two"),)
    # Two functions of one name: neither is followed.
    assert reached["caller"].reached_steps == ()
    # A method its class does not have, and a `self` call outside a class, name nothing.
    assert reached["Other.own"].reached_steps == ()
    assert reached["lonely"].reached_steps == ()
    # A cycle ends; a function calling itself takes its own steps once.
    assert reached["ping"].reached_steps == (
        ("delete", "os.remove", "pong"),
        ("delete", "os.remove", ""),
    )
    assert reached["again"].reached_steps == (("delete", "os.remove", ""),)


def test_a_callable_records_at_most_so_many_reached_steps_and_counts_them_all() -> None:
    body = "    os.replace(a, b)\n" * limits.MAX_FILE_STEPS
    calls = "".join(f"    step{index}(a, b)\n" for index in range(3))
    helpers = "".join(f"def step{index}(a, b):\n{body}" for index in range(3))
    reached = _reached(f"import os\ndef run(a, b):\n{calls}{helpers}")
    assert reached["run"].reached_steps_total == 3 * limits.MAX_FILE_STEPS
    assert len(reached["run"].reached_steps) == limits.MAX_REACHED_STEPS


def test_a_failure_midway_matters_once_a_file_is_put_in_place_or_copied() -> None:
    assert at_risk_midway((("temp", "t", ""), ("move", "m", "")))
    assert at_risk_midway((("copy", "c", ""), ("delete", "d", "")))
    assert not at_risk_midway((("move", "m", ""),))
    assert not at_risk_midway((("temp", "t", ""), ("delete", "d", "")))


def _kinds_with(deltas: list[StructuralDelta], key: str) -> list[str]:
    return sorted(item.kind for item in deltas if key in item.metadata)


def test_a_new_handler_that_undoes_and_raises_is_noted_on_its_raise_and_handler() -> None:
    deltas = _deltas(UNDO_OLD, UNDO_NEW)
    assert _kinds_with(deltas, "handled_failures") == ["exception_handler_change", "raise_change"]
    (noted,) = {
        str(item.metadata["handled_failures"])
        for item in deltas
        if "handled_failures" in item.metadata
    }
    assert noted == str(
        [["Exception", ["fridge.restore"], ["history.reinstate"], True, CAUGHT_AGAIN]]
    )


def test_a_handler_that_did_not_change_is_not_noted_again() -> None:
    handler = (
        "    try:\n"
        "        fridge.restore(event)\n"
        "    except Exception:\n"
        "        history.reinstate(event)\n"
        "        raise\n"
    )
    old = f"def handle(event, fridge, history):\n{handler}    return event\n"
    new = (
        f"def handle(event, fridge, history):\n{handler}"
        "    if event is None:\n"
        "        raise ValueError('no event')\n"
        "    return event\n"
    )
    deltas = _deltas(old, new)
    assert "raise_change" in {item.kind for item in deltas}
    assert _kinds_with(deltas, "handled_failures") == []


def test_a_handler_that_catches_more_but_undoes_as_before_is_not_noted() -> None:
    # pyjwt's `fetch_data` came to catch `http.client.HTTPException` too, and its obligation said
    # that the close it had made before was new.
    def fetch(caught: str) -> str:
        return (
            "def fetch(url):\n"
            "    try:\n"
            "        return urlopen(url).read()\n"
            f"    except {caught} as error:\n"
            "        error.close()\n"
            "        raise ClientError(str(error)) from error\n"
        )

    deltas = _deltas(fetch("(URLError, OSError)"), fetch("(URLError, OSError, HTTPException)"))
    assert "exception_handler_change" in {item.kind for item in deltas}
    assert _kinds_with(deltas, "handled_failures") == []


def test_a_handler_whose_undoing_comes_to_be_reported_when_it_fails_is_noted() -> None:
    old = (
        "def handle(event, fridge, history):\n"
        "    try:\n"
        "        fridge.restore(event)\n"
        "    except Exception:\n"
        "        history.reinstate(event)\n"
        "        raise\n"
    )
    new = old.replace(
        "        history.reinstate(event)\n",
        "        try:\n"
        "            history.reinstate(event)\n"
        "        except Exception:\n"
        "            logger.exception('could not reinstate %s', event)\n",
    )
    (handler,) = [item for item in _deltas(old, new) if "handled_failures" in item.metadata]
    assert handler.kind == "exception_handler_change"
    assert handler.metadata["handled_failures"] == [
        ["Exception", ["fridge.restore"], ["history.reinstate"], True, CAUGHT_AGAIN]
    ]


def test_a_call_that_brings_a_step_is_noted_with_the_steps_in_order() -> None:
    deltas = _deltas(STEPS_OLD, STEPS_NEW)
    (call,) = [item for item in deltas if item.symbol == "write" and item.kind == "call_change"]
    assert call.metadata["file_steps"] == [
        ["temp", "tempfile.mkstemp", ""],
        ["write", "os.fdopen", ""],
        ["write", "json.dump", ""],
        ["move", "os.replace", "_keep"],
        ["move", "os.replace", ""],
    ]
    assert call.metadata["file_steps_total"] == 5


def test_a_call_whose_arguments_alone_changed_brings_no_step() -> None:
    helper = (
        "import os\n"
        "def write(path, data, flag=None):\n"
        "    os.replace(path + '.tmp', path)\n"
        "    os.remove(path + '.old')\n"
    )
    old = f"{helper}def save(path, data):\n    write(path, data)\n"
    new = f"{helper}def save(path, data):\n    write(path, data, flag=True)\n"
    deltas = _deltas(old, new)
    assert "call_change" in {item.kind for item in deltas}
    assert _kinds_with(deltas, "file_steps") == []


def test_steps_that_put_no_file_in_place_are_not_noted() -> None:
    old = "import os, tempfile\ndef page(text):\n    f = tempfile.NamedTemporaryFile()\n"
    new = (
        "import os, tempfile\ndef page(text):\n    f = tempfile.NamedTemporaryFile()\n"
        "    os.unlink(f.name)\n"
    )
    assert _kinds_with(_deltas(old, new), "file_steps") == []


def test_an_undoing_handler_s_obligation_names_the_step_the_undoing_and_the_error() -> None:
    behaviors, changes = _findings(UNDO_OLD, UNDO_NEW)
    (behavior,) = [item for item in behaviors if item.category is BehaviorCategory.ERROR_HANDLING]
    (scenario,) = evidence_scenarios(behavior, changes)
    assert scenario.type is ObligationType.ERROR
    assert scenario.title == "Verify what `handle` undoes when `fridge.restore` fails"
    assert scenario.given == (
        "When `fridge.restore` raises an exception, `handle` now calls `history.reinstate` and "
        "then raises it again"
    )
    assert scenario.when == "`fridge.restore` raises an exception while `handle` runs"
    assert scenario.then == (
        "`history.reinstate` runs, and the caller gets the exception it caught; if "
        "`history.reinstate` fails too, that is reported and the caller still gets the exception "
        "it caught"
    )


def test_a_change_that_also_raises_something_else_keeps_that_obligation_first() -> None:
    # The field test's `HANDLER` also came to reject a flag that was not a boolean, and the user
    # judged the obligation that asked for it useful.
    new = UNDO_NEW.replace(
        "    history.retract(event)\n",
        "    if not isinstance(event, dict):\n"
        "        raise ValueError('event must be a dict')\n"
        "    history.retract(event)\n",
    )
    behaviors, changes = _findings(UNDO_OLD, new)
    (behavior,) = [item for item in behaviors if item.category is BehaviorCategory.ERROR_HANDLING]
    first, second = evidence_scenarios(behavior, changes)
    assert first.title == "Exercise the changed failure trigger in `handle`"
    assert "ValueError" in first.given
    assert second.title == "Verify what `handle` undoes when `fridge.restore` fails"
    assert second.relevance == first.relevance - SECOND_SCENARIO_RELEVANCE_DROP


@pytest.mark.parametrize(
    ("kind", "added", "added_total", "explained"),
    [
        ("raise_change", (BARE_RAISE, "StoreError('x')"), 2, True),
        ("raise_change", (BARE_RAISE,), 2, False),
        ("raise_change", ("KeyError('x')",), 1, False),
        ("exception_handler_change", ("OSError",), 1, True),
        ("exception_handler_change", ("OSError", "KeyError"), 2, False),
    ],
)
def test_the_undoing_is_the_one_obligation_where_it_explains_what_the_change_added(
    kind: str, added: tuple[str, ...], added_total: int, explained: bool
) -> None:
    handled = (
        ("OSError", ("put",), ("undo",), False, CAUGHT_AGAIN),
        ("OSError", ("put",), ("undo",), False, "StoreError"),
    )
    change = Change(kind=kind, added=added, added_total=added_total, handled_failures=handled)
    scenarios = evidence_scenarios(
        _manual(BehaviorCategory.ERROR_HANDLING, kind), {"ev-001": change}
    )
    assert len(scenarios) == (1 if explained else 2)
    assert scenarios[-1].title == "Verify what `f` undoes when `put` fails"


def _undo(*handled: tuple[str, tuple[str, ...], tuple[str, ...], bool, str]) -> tuple[str, ...]:
    # What the change added is the handlers' own raises, so their scenario is the one.
    added = tuple(BARE_RAISE if raised == CAUGHT_AGAIN else f"{raised}()" for *_, raised in handled)
    change = Change(
        kind="raise_change", added=added, added_total=len(added), handled_failures=handled
    )
    (scenario,) = evidence_scenarios(
        _manual(BehaviorCategory.ERROR_HANDLING, "raise_change"), {"ev-001": change}
    )
    return scenario.title, scenario.given, scenario.when, scenario.then


def test_an_undoing_handler_s_obligation_follows_what_it_catches_and_raises() -> None:
    assert _undo(("ValueError", ("store.put",), ("store.rollback",), False, "StoreError")) == (
        "Verify what `f` undoes when `store.put` fails",
        "When `store.put` raises `ValueError`, `f` now calls `store.rollback` and then raises "
        "`StoreError`",
        "`store.put` raises `ValueError` while `f` runs",
        "`store.rollback` runs, and the caller gets `StoreError`",
    )
    title, given, _, then = _undo(
        ("", (), ("a.undo", "b.close", "c.free"), False, CAUGHT_AGAIN),
        ("KeyError", ("x",), ("y",), False, CAUGHT_AGAIN),
    )
    assert title == "Verify what `f` undoes when its guarded code fails"
    assert given == (
        "When the code its handler guards raises an exception, `f` now calls `a.undo`, `b.close`, "
        "and 1 more and then raises it again; 1 more such handler(s) changed too"
    )
    assert then == (
        "`a.undo`, `b.close`, and 1 more run, and the caller gets the exception it caught"
    )
    # Of several guarded steps the test picks the one to make fail: the title names none.
    title, given, when, _ = _undo(("OSError", ("a.put", "b.put"), ("c.undo",), False, "E"))
    assert title == "Verify what `f` undoes when its guarded code fails"
    assert given.startswith("When the code its handler guards raises `OSError`, `f` now calls")
    assert when == (
        "A step of the code its handler guards, such as `a.put` or `b.put`, raises `OSError` "
        "while `f` runs"
    )
    _, _, when, _ = _undo(("OSError", ("a.put", "b.put", "c.put"), ("d.undo",), False, "E"))
    assert when.startswith("A step of the code its handler guards, such as `a.put`, `b.put`, or")
    _, _, when, _ = _undo(("OSError", (), ("d.undo",), False, "E"))
    assert when == "The code its handler guards raises `OSError` while `f` runs"


def test_a_call_that_brings_a_step_asks_for_each_step_to_fail_in_turn() -> None:
    behaviors, changes = _findings(STEPS_OLD, STEPS_NEW)
    (behavior,) = [
        item
        for item in behaviors
        if item.evidence[0].symbol == "write" and item.evidence[0].kind == "call_change"
    ]
    (scenario,) = evidence_scenarios(behavior, changes)
    assert scenario.type is ObligationType.ERROR
    assert scenario.title == "Make each file step of `write` fail in turn"
    assert scenario.given == (
        "`write` now also calls `_keep(path)`; its file steps now run in this order: "
        "`tempfile.mkstemp`, `os.fdopen`, `json.dump`, `os.replace` in `_keep`, `os.replace`"
    )
    assert scenario.when == (
        "Each step raises in turn, through a stub or a failing file system, after the steps "
        "before it succeed"
    )
    assert scenario.then == (
        "After each failure the original file, or the copy set aside, is still in place, no "
        "partial file replaces it, and the caller learns of the error"
    )
    # Its steps say what to make fail, so it keeps an obligation of its own.
    assert not weakly_anchored(behavior, changes)
    obligations, _ = generate_obligations(
        behaviors, {}, False, WeaverConfig().rules, changes=changes
    )
    assert "Make each file step of `write` fail in turn" in {item.title for item in obligations}


def test_a_long_sequence_of_steps_lists_the_first_and_counts_the_rest() -> None:
    steps = tuple(("move", f"os.replace{index}", "") for index in range(8))
    change = Change(
        kind="call_change",
        added=("g()",),
        added_total=1,
        internal=True,
        file_steps=steps,
        file_steps_total=10,
    )
    behavior = _manual(BehaviorCategory.DEPENDENCY_INTERACTION, "call_change")
    (scenario,) = evidence_scenarios(behavior, {"ev-001": change})
    assert scenario.given.endswith("`os.replace4`, `os.replace5`, and 4 more")
    assert not weakly_anchored(behavior, {"ev-001": change})
    # Another category's call keeps the scenario its category writes.
    guarded = _manual(BehaviorCategory.AUTHORIZATION, "call_change")
    (other,) = evidence_scenarios(guarded, {"ev-001": change})
    assert other.title == "Verify an allowed and a denied principal through the changed call in `f`"


def _described(metadata: dict[str, object]) -> Change:
    delta = StructuralDelta(
        path="m.py",
        symbol="f",
        kind="raise_change",
        old=None,
        new="E",
        old_lines=None,
        new_lines=None,
        hunk_id="hunk-001",
        metadata=metadata,
    )
    change = describe_change(delta)
    assert change is not None
    return change


def test_handlers_and_steps_out_of_shape_in_metadata_are_dropped() -> None:
    kept = ["Exception", ["a"], ["b"], True, CAUGHT_AGAIN]
    change = _described(
        {
            "handled_failures": [
                kept,
                ["Exception", ["a"], [], True, CAUGHT_AGAIN],
                ["Exception", ["a"], ["b", 3], True, CAUGHT_AGAIN],
                ["Exception", "a", ["b"], True, CAUGHT_AGAIN],
                ["Exception", ["a"], ["b"], "yes", CAUGHT_AGAIN],
                ["Exception", ["a"], ["b"], True, ""],
                ["Exception", ["a"], ["b"], True],
                "a handler",
            ],
            "file_steps": [["move", "os.replace", ""], ["write", "json.dump", "g"]],
            "file_steps_total": 7,
        }
    )
    assert change.handled_failures == (("Exception", ("a",), ("b",), True, CAUGHT_AGAIN),)
    assert change.file_steps == (("move", "os.replace", ""), ("write", "json.dump", "g"))
    assert change.file_steps_total == 7
    assert _described({"handled_failures": "none", "file_steps": "none"}).handled_failures == ()
    # A step out of shape drops the whole sequence, whose order is what it says.
    broken = _described({"file_steps": [["move", "os.replace", ""], ["melt", "x", ""]]})
    assert broken.file_steps == ()
    assert _described({"file_steps": [["move", "", ""]]}).file_steps == ()
