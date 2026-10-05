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


def _boundary_result(repo_factory) -> AnalysisResult:
    long_expression = " and ".join(f"value_{index} < limit_{index}" for index in range(30))
    repo, base, head = repo_factory(
        {"src/api.py": f"def check(x):\n    return {long_expression} and x < 1\n"},
        {"src/api.py": f"def check(x):\n    return {long_expression} and x <= 1\n"},
    )
    envelope = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}
    )
    return AnalysisResult.model_validate(envelope)


def test_the_brief_names_each_finding_and_each_obligation_with_what_it_covers(
    repo_factory,
) -> None:
    """Obligation IDs were only in the JSON, so asking for them meant asking for the JSON."""
    result = _boundary_result(repo_factory)
    markdown = render_markdown(result)
    for behavior in result.behavior_changes:
        assert f"- **Finding `{behavior.id}` · " in markdown
    for obligation in result.test_obligations:
        covers = ", ".join(f"`{item}`" for item in obligation.behavior_change_ids)
        assert f"(`{obligation.id}`; covers {covers})" in markdown


def test_evidence_excerpts_can_be_cut_or_dropped_and_the_brief_says_so(repo_factory) -> None:
    result = _boundary_result(repo_factory)
    full = render_markdown(result)
    cut = render_markdown(result, excerpt_chars=40)
    dropped = render_markdown(result, excerpt_chars=0)
    assert "**Evidence excerpts:**" not in full
    assert "**Evidence excerpts:** cut to 40 characters" in cut
    assert "**Evidence excerpts:** omitted" in dropped
    assert len(dropped) < len(cut) < len(full)
    excerpts = [line for line in cut.splitlines() if line.startswith(("    old: ", "    new: "))]
    assert excerpts
    assert all(len(line) <= len("    new: ") + 40 + len(" …") for line in excerpts)
    assert any(line.endswith(" …") for line in excerpts)
    assert "````text" not in dropped
    # Findings, obligations, and their IDs are untouched by either.
    for variant in (cut, dropped):
        for obligation in result.test_obligations:
            assert f"(`{obligation.id}`; covers" in variant
