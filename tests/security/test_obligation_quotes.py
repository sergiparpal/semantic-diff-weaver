"""An obligation quotes repository code, and a quote must never become Markdown or code of its own.

An obligation now quotes what its finding's evidence changed, a call, a condition, or a returned
value, as code spans in its text. Repository code is untrusted: a quoted value can hold backticks,
link and image syntax, HTML, a mention, or a forged heading, and the report reaches a pull request
as a comment and a skeleton as a test file.
"""

from __future__ import annotations

import ast
import io
import json
import re
import tokenize
from pathlib import Path

from semantic_diff_weaver.service import analyze
from semantic_diff_weaver.skeletons import generate_skeletons
from semantic_diff_weaver.textutil import split_code_spans
from tests.conftest import git

MARKER = "INJECTED"
# Outside a code span, each would be Markdown, HTML, or a notification.
HOSTILE = (
    f"``` [{MARKER}](javascript:alert(1)) ![i](http://x/i.png) @everyone "
    "<img src=x onerror=alert(1)> ## Overall risk: low"
)
OLD = {"api.py": "def notify(client):\n    return client.send('hello')\n"}
NEW = {"api.py": f"def notify(client):\n    return client.send({HOSTILE!r})\n"}
TESTS = {"tests/test_api.py": "def test_notify():\n    assert True\n"}
# An obligation's title line and its Given, When, and Then lines.
OBLIGATION_LINE = re.compile(r"- \[ \] \*\*P\d+ — |  - (Given|When|Then): ")
OUTSIDE_A_SPAN = ("javascript:", "](", "@everyone", "<img", "## Overall risk")


def _analysis(repo_factory) -> tuple[Path, dict]:
    repo, base, head = repo_factory({**OLD, **TESTS}, NEW)
    return repo, analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "both"}
    )


def test_quoted_code_stays_inside_code_spans_in_the_report(repo_factory) -> None:
    _, envelope = _analysis(repo_factory)
    # The changed call is quoted, so the test is not vacuous.
    assert any(MARKER in item["given"] for item in envelope["analysis"]["test_obligations"])
    lines = envelope["markdown"].splitlines()
    quoting = [line for line in lines if OBLIGATION_LINE.match(line) and MARKER in line]
    assert quoting
    for line in lines:
        if not OBLIGATION_LINE.match(line):
            continue
        prose = "".join(part for part, code in split_code_spans(line) if not code)
        for text in OUTSIDE_A_SPAN:
            assert text not in prose, (text, line)
    assert not any(line.lstrip().startswith("## Overall risk") for line in lines)


def test_a_skeleton_of_an_obligation_quoting_hostile_code_carries_it_only_in_comments(
    repo_factory, tmp_path
) -> None:
    repo, envelope = _analysis(repo_factory)
    analysis = envelope["analysis"]
    quoting = [item["id"] for item in analysis["test_obligations"] if MARKER in item["given"]]
    assert quoting
    path = tmp_path / "analysis.json"
    path.write_text(json.dumps(analysis), encoding="utf-8")
    result = generate_skeletons(
        {
            "repo_path": str(repo),
            "analysis_path": str(path),
            "obligation_ids": quoting,
            "framework": "pytest",
        }
    )
    patch = tmp_path / "skeletons.patch"
    patch.write_bytes(result["patch"].encode("utf-8"))
    git(repo, "apply", str(patch))
    text = (repo / "tests" / "test_api.py").read_text(encoding="utf-8")
    tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    assert any(MARKER in token.string for token in tokens)
    assert all(token.type == tokenize.COMMENT for token in tokens if MARKER in token.string)
    tree = ast.parse(text)
    names = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
    assert names[0] == "test_notify"
    # A test's name comes from the title's prose, never from the code it quotes.
    assert all(re.fullmatch(r"test_[a-z0-9_]+", name) for name in names)
    assert not any(MARKER.casefold() in name for name in names)
