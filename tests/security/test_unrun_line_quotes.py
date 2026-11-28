"""A line no test ran is quoted from repository code, and must be redacted and stay code.

With a coverage report, an obligation quotes the changed lines of its finding's own code that no
test ran. They are untrusted: a line can hold a credential, or backticks, link and image syntax,
HTML, a mention, or a forged heading, and the report reaches a pull request as a comment.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from semantic_diff_weaver.service import analyze
from semantic_diff_weaver.textutil import split_code_spans

MARKER = "INJECTED"
HOSTILE = (
    f"``` [{MARKER}](javascript:alert(1)) ![i](http://x/i.png) @everyone "
    "<img src=x onerror=alert(1)> ## Overall risk: low"
)
TOKEN = "ghp_" + "Q" * 36
OLD = {"api.py": "def send(client):\n    return client.post()\n"}
NEW = {
    "api.py": (
        "def send(client):\n"  # 1
        "    try:\n"  # 2
        "        return client.post()\n"  # 3
        "    except OSError:\n"  # 4
        f"        client.log({HOSTILE!r})\n"  # 5
        f"        raise LookupError({TOKEN!r}) from None\n"  # 6
    )
}
REPORT = {
    "meta": {"version": "7.15.2"},
    "files": {"api.py": {"executed_lines": [1, 3], "missing_lines": [4, 5, 6]}},
    "totals": {},
}
OBLIGATION_LINE = re.compile(r"  - Given: ")
OUTSIDE_A_SPAN = ("javascript:", "](", "@everyone", "<img", "## Overall risk")


def _envelope(repo_factory) -> dict[str, Any]:
    repo, base, head = repo_factory(OLD, NEW)
    path = Path(repo) / "coverage.json"
    path.write_text(json.dumps(REPORT), encoding="utf-8")
    return analyze(
        {
            "repo_path": str(repo),
            "base_ref": base,
            "head_ref": head,
            "output_format": "both",
            "coverage_report": str(path),
        }
    )


def test_a_credential_on_a_line_no_test_ran_is_redacted(repo_factory) -> None:
    envelope = _envelope(repo_factory)
    givens = [item["given"] for item in envelope["analysis"]["test_obligations"]]
    # The handler's lines are named, so the test is not vacuous.
    assert any("no test ran the changed lines 4-6 of `api.py`" in given for given in givens)
    assert TOKEN not in json.dumps(envelope)
    assert TOKEN not in envelope["markdown"]


def test_a_quoted_line_stays_inside_its_code_span_in_the_report(repo_factory) -> None:
    envelope = _envelope(repo_factory)
    lines = envelope["markdown"].splitlines()
    quoting = [line for line in lines if OBLIGATION_LINE.match(line) and MARKER in line]
    assert any("no test ran" in line for line in quoting)
    for line in lines:
        if not OBLIGATION_LINE.match(line):
            continue
        prose = "".join(part for part, code in split_code_spans(line) if not code)
        for text in OUTSIDE_A_SPAN:
            assert text not in prose, (text, line)
    assert not any(line.lstrip().startswith("## Overall risk") for line in lines)
