from __future__ import annotations

from semantic_diff_weaver.models import AnalysisResult
from semantic_diff_weaver.renderer import render_markdown
from semantic_diff_weaver.service import analyze


def test_markdown_is_pr_ready_and_uses_candidate_disclaimer(repo_factory) -> None:
    tests = "from src.api import check\n\ndef test_check_boundary():\n    assert check(1)\n"
    repo, base, head = repo_factory(
        {"src/api.py": "def check(x):\n    return x < 1\n", "tests/test_api.py": tests},
        {"src/api.py": "def check(x):\n    return x <= 1\n", "tests/test_api.py": tests},
    )
    result = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "both"}
    )
    markdown = result["markdown"]
    assert markdown.startswith("## Semantic Diff Test Brief")
    assert "- [ ] **P" in markdown
    assert "Candidate existing tests (unverified)" in markdown
    assert "no tests or repository code were executed" in markdown
    assert "do not verify runtime coverage" in markdown


def test_untrusted_markdown_path_is_escaped(repo_factory) -> None:
    repo, base, head = repo_factory(
        {"src/a[1].py": "def f(x):\n    return x < 1\n"},
        {"src/a[1].py": "def f(x):\n    return x <= 1\n"},
    )
    result = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "markdown"}
    )
    assert "a\\[1\\]\\.py" in result["markdown"]


def test_terminal_and_bidirectional_controls_are_visible(repo_factory) -> None:
    repo, base, head = repo_factory(
        {"a.py": "def f(x):\n    return x < 1\n"},
        {"a.py": "def f(x):\n    return x <= 1\n"},
    )
    result = AnalysisResult.model_validate(
        analyze(
            {
                "repo_path": str(repo),
                "base_ref": base,
                "head_ref": head,
                "output_format": "json",
            }
        )
    )
    result.behavior_changes[0].evidence[0].path = "src/escape\x1b[31m\u202epy"
    markdown = render_markdown(result)
    assert "\x1b" not in markdown
    assert "\u202e" not in markdown
    assert r"\u001b" in markdown
    assert r"\u202e" in markdown


def test_high_risk_low_configured_confidence_renders_review_question(repo_factory) -> None:
    config = (
        "version: 1\n"
        "critical_paths:\n"
        "  - pattern: 'auth.py'\n"
        "    weight: 100\n"
        "rules:\n"
        "  review_question_confidence: 0.90\n"
    )
    repo, base, head = repo_factory(
        {
            "auth.py": "def allowed(user):\n    if user.is_owner:\n        return True\n    return False\n",
            ".semantic-diff-weaver.yaml": config,
        },
        {
            "auth.py": "def allowed(user):\n    if user.is_admin:\n        return True\n    return False\n",
            ".semantic-diff-weaver.yaml": config,
        },
    )
    result = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "both"}
    )
    assert any(
        item["presentation"] == "review_question" for item in result["analysis"]["behavior_changes"]
    )
    assert "### Review questions" in result["markdown"]


def test_markdown_handles_minimal_evidence_and_empty_optional_sections(repo_factory) -> None:
    repo, base, head = repo_factory(
        {"a.py": "def f(x):\n    return x < 1\n"},
        {"a.py": "def f(x):\n    return x <= 1\n"},
    )
    result = AnalysisResult.model_validate(
        analyze(
            {
                "repo_path": str(repo),
                "base_ref": base,
                "head_ref": head,
                "output_format": "json",
            }
        )
    )
    evidence = result.behavior_changes[0].evidence[0]
    evidence.symbol = None
    evidence.old_lines = None
    evidence.new_lines = None
    evidence.old = None
    evidence.new = None
    result.warnings = []
    result.limitations = []
    markdown = render_markdown(result)
    assert "Evidence `ev-001`" in markdown
    assert "### Warnings" not in markdown
    assert "### Limitations" not in markdown
