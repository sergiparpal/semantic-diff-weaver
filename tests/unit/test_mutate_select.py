"""The mutant measurement of `select`: which tokens it mutates, and how it judges a mutant."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.mutate_select import (
    Site,
    apply,
    changes_the_program,
    contexts_by_line,
    failed_tests,
    has_escaped,
    is_test_path,
    measured_keys,
    missed_tests,
    mutant_timeout,
    mutation_sites,
    nodeid_file,
    run_environment,
    summarize,
)

SOURCE = (
    "LIMIT = 3\n"
    "\n"
    "\n"
    "def allowed(x, flag=True):\n"
    "    # x < 1 in a comment is no site\n"
    "    message = 'a < b and c'\n"
    "    return x <= LIMIT and flag\n"
    "\n"
    "\n"
    "class Box:\n"
    "    size = 2\n"
    "\n"
    "    def grow(self):\n"
    "        return self.size + 1\n"
)


def test_a_site_is_an_operator_a_keyword_or_a_whole_number_in_code() -> None:
    sites = [(site.line, site.token, site.replacement) for site in mutation_sites(SOURCE)]
    assert sites == [
        (1, "3", "4"),
        (4, "True", "False"),
        (7, "<=", "<"),
        (7, "and", "or"),
        (11, "2", "3"),
        (14, "+", "-"),
        (14, "1", "2"),
    ]


def test_source_that_does_not_tokenize_has_no_site() -> None:
    assert mutation_sites("def f(:\n    '''unterminated") == []


def test_a_mutant_changes_its_token_and_nothing_else() -> None:
    site = Site(7, 13, "<=", "<")
    mutated = apply(SOURCE, site)
    assert mutated.splitlines()[6] == "    return x < LIMIT and flag"
    assert mutated.splitlines()[:6] == SOURCE.splitlines()[:6]
    with pytest.raises(ValueError, match="holds no"):
        apply(SOURCE, Site(7, 0, "<=", "<"))


def test_a_mutant_keeps_the_line_endings_tokenize_counts_lines_by() -> None:
    # A form feed ends a line for `str.splitlines`, never for `tokenize`.
    source = "x = '\f'\r\ny = x < 2\r\n"
    (site,) = [site for site in mutation_sites(source) if site.token == "<"]
    assert apply(source, site) == "x = '\f'\r\ny = x <= 2\r\n"


def test_a_mutant_counts_when_it_parses_into_another_program() -> None:
    assert changes_the_program("x = 1\n", "x = 2\n")
    assert not changes_the_program("x = 1\n", "x = 1  # same\n")
    assert not changes_the_program("x = 1\n", "x = \n")


def test_pytest_s_short_summary_names_the_failed_tests() -> None:
    output = (
        "....F.E\n"
        "=========================== short test summary info ============================\n"
        "FAILED tests/test_a.py::test_one - AssertionError: assert 1 == 2\n"
        "ERROR tests/test_b.py::TestB::test_two[x-1] - RuntimeError\n"
        "FAILED tests/test_c.py::test_three[a b]\n"
        "ERROR tests/test_d.py\n"
        "3 failed, 5 passed in 0.10s\n"
    )
    assert failed_tests(output) == {
        "tests/test_a.py::test_one",
        "tests/test_b.py::TestB::test_two[x-1]",
        "tests/test_c.py::test_three[a b]",
        "tests/test_d.py",
    }
    assert nodeid_file("tests/test_b.py::TestB::test_two[x-1]") == "tests/test_b.py"


def test_a_miss_is_a_catching_test_in_a_file_select_left_out() -> None:
    killed = ["tests/test_a.py::test_one", "tests/test_b.py::test_two"]
    assert missed_tests(killed, ["tests/test_a.py"], run_all=False) == ["tests/test_b.py::test_two"]
    assert missed_tests(killed, [], run_all=True) == []
    assert missed_tests([], ["tests/test_a.py"], run_all=False) == []


def test_a_mutant_escapes_when_every_test_that_caught_it_was_left_out() -> None:
    assert has_escaped(["t.py::a"], ["t.py::a"])
    assert not has_escaped(["t.py::a", "u.py::b"], ["u.py::b"])
    assert not has_escaped([], [])


def test_a_mutant_s_tests_may_take_a_multiple_of_their_base_time_within_bounds() -> None:
    took = {"tests/test_a.py": 4.0, "tests/test_b.py": 30.0}
    assert mutant_timeout(["tests/test_a.py"], took, cap=600) == 60  # the floor
    assert mutant_timeout(["tests/test_a.py", "tests/test_b.py"], took, cap=600) == 170
    assert mutant_timeout(["tests/test_b.py"], took, cap=100) == 100
    # A file the base never timed, as one that timed out there, may take the cap.
    assert mutant_timeout(["tests/test_c.py"], took, cap=600) == 600


def test_the_analyzed_tests_never_see_a_credential_nor_write_bytecode() -> None:
    environ = {
        "PATH": "/bin",
        "HOME": "/home/x",
        "OPENROUTER_API_KEY": "k",
        "GH_TOKEN": "t",
        "aws_secret_access_key": "s",
        "SSH_AUTH_SOCK": "/run/agent",
    }
    assert run_environment(environ, Path("/work/no-bytecode")) == {
        "PATH": "/bin",
        "HOME": "/home/x",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": str(Path("/work/no-bytecode")),
    }


def test_a_run_skips_the_mutants_already_measured_on_its_base(tmp_path: Path) -> None:
    out = tmp_path / "mutants.jsonl"
    assert measured_keys(out, "repo", "b1") == set()
    rows = [
        {
            "repository": "repo",
            "base": "b1",
            "path": "p.py",
            "line": 2,
            "column": 4,
            "replacement": "<=",
        },
        {
            "repository": "repo",
            "base": "b2",
            "path": "p.py",
            "line": 3,
            "column": 0,
            "replacement": "1",
        },
        {
            "repository": "other",
            "base": "b1",
            "path": "q.py",
            "line": 1,
            "column": 0,
            "replacement": "or",
        },
    ]
    out.write_text("\n".join(json.dumps(row) for row in rows) + "\n\n", encoding="utf-8")
    assert measured_keys(out, "repo", "b1") == {("p.py", 2, 4, "<=")}


@pytest.mark.parametrize(
    ("path", "test"),
    [
        ("tests/unit/test_a.py", True),
        ("pkg/conftest.py", True),
        ("pkg/test_helpers.py", True),
        ("pkg/thing_test.py", True),
        ("pkg/service.py", False),
        ("testsuite/run.py", False),
    ],
)
def test_test_paths_are_never_mutated(path: str, test: bool) -> None:
    assert is_test_path(path, ["tests"]) is test


def test_coverage_contexts_name_the_tests_that_ran_each_line(tmp_path: Path) -> None:
    from coverage import CoverageData

    source = tmp_path / "pkg" / "mod.py"
    source.parent.mkdir()
    source.write_text("x = 1\n", encoding="utf-8")
    data = CoverageData(basename=str(tmp_path / ".coverage"))
    data.set_context("tests/test_a.py")
    data.add_lines({str(source): [1, 2]})
    data.set_context("tests/test_b.py")
    data.add_lines({str(source): [2]})
    data.set_context("")
    data.add_lines({str(source): [3], str(tmp_path.parent / "outside.py"): [1]})
    data.write()
    found = contexts_by_line(tmp_path / ".coverage", tmp_path)
    # A line that ran outside every test file's process names none.
    assert found == {
        "pkg/mod.py": {1: {"tests/test_a.py"}, 2: {"tests/test_a.py", "tests/test_b.py"}}
    }
    assert contexts_by_line(tmp_path / "missing.coverage", tmp_path) == {}


def test_a_summary_counts_the_mutants_caught_and_missed(tmp_path: Path) -> None:
    def record(**fields: object) -> dict:
        base = {
            "repository": "repo",
            "base": "b1",
            "path": "pkg/mod.py",
            "line": 1,
            "token": "<",
            "replacement": "<=",
            "killed_by": [],
            "missed": [],
            "flaky": [],
            "escaped": False,
            "selected": [],
            "run_all": False,
            "tests_total": 4,
            "outcome": "passed",
        }
        return {**base, **fields}

    rows = [
        record(killed_by=["tests/test_a.py::t"], selected=["tests/test_a.py"]),
        record(
            killed_by=["tests/test_b.py::t"],
            selected=["tests/test_a.py"],
            missed=["tests/test_b.py::t"],
            escaped=True,
            base="b2",
        ),
        record(run_all=True, outcome="timeout", flaky=["tests/test_c.py::t"]),
        record(outcome="not covered"),
        record(repository="other", outcome="error 2: collection failed"),
    ]
    path = tmp_path / "mutants.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    summary = summarize([path])
    assert summary.splitlines()[0] == "other: 1 mutants on 1 bases"
    assert "0 caught by a test, 0 timed out, 0 on lines no test runs, 1 broke the test run" in (
        summary
    )
    assert "repo: 4 mutants on 2 bases" in summary
    assert "2 caught by a test, 1 timed out, 1 on lines no test runs, 0 broke" in summary
    assert "select chose the whole suite 1 times, and otherwise 17% of the test files" in summary
    assert "missed: 1 mutants caught by a test select left out, 1 by no test it chose; 1 tests" in (
        summary
    )
    assert "pkg/mod.py:1 <-><=: tests/test_b.py::t (escaped)" in summary
