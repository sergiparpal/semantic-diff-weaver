from __future__ import annotations

from semantic_diff_weaver.ast_diff import StructuralDelta
from semantic_diff_weaver.git_diff import GitRepository
from semantic_diff_weaver.models import LineRange, WeaverConfig
from semantic_diff_weaver.semantic_candidates import build_candidates
from semantic_diff_weaver.service import analyze
from semantic_diff_weaver.test_mapper import build_test_index, map_candidate_tests


def test_committed_test_index_maps_import_name_and_path(repo_factory) -> None:
    tests = """
from src.api import allowed

def test_allowed_boundary():
    assert allowed(5)
"""
    repo_path, _, head = repo_factory(
        {
            "src/api.py": "def allowed(x):\n    return x < 5\n",
            "tests/test_api.py": tests,
        },
        {
            "src/api.py": "def allowed(x):\n    return x <= 5\n",
            "tests/test_api.py": tests,
        },
    )
    candidate = build_candidates(
        [
            StructuralDelta(
                path="src/api.py",
                symbol="allowed",
                kind="comparison_change",
                old="x < 5",
                new="x <= 5",
                old_lines=LineRange(start=2, end=2),
                new_lines=LineRange(start=2, end=2),
                hunk_id="src/api.py#hunk-001",
            )
        ]
    )[0]
    config = WeaverConfig()
    index = build_test_index(GitRepository.open(str(repo_path)), head, config)
    mapped = map_candidate_tests([candidate], index, config)[0]
    assert mapped[0].path == "tests/test_api.py"
    assert mapped[0].symbol == "test_allowed_boundary"
    assert mapped[0].match_score >= 0.7
    assert mapped[0].verified is False


def _candidate_tests(result: dict) -> list[str]:
    return [
        test["path"]
        for obligation in result["test_obligations"]
        for test in obligation["candidate_existing_tests"]
    ]


def test_include_narrows_the_analysis_but_not_candidate_test_discovery(repo_factory) -> None:
    """The Claude Code skill narrows large diffs with `include` and never with `exclude`.

    That advice rests on this asymmetry: `include` limits only which *changed* files are
    analyzed, while `exclude` also hides matching files from candidate-test discovery, so
    excluding a tests directory silently drops every candidate test.
    """
    tests = "from src.api import allowed\n\ndef test_allowed_boundary():\n    assert allowed(5)\n"
    repo, base, head = repo_factory(
        {
            "src/api.py": "def allowed(x):\n    return x < 5\n",
            "tools/report.py": "def limit():\n    return 1\n",
            "tests/test_api.py": tests,
        },
        {
            "src/api.py": "def allowed(x):\n    return x <= 5\n",
            "tools/report.py": "def limit():\n    return 2\n",
            "tests/test_api.py": tests,
        },
    )
    request = {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}

    narrowed = analyze({**request, "include": ["src/**"]})
    assert narrowed["scope"]["analyzed_files"] == ["src/api.py"]
    assert "tests/test_api.py" in _candidate_tests(narrowed)

    excluded = analyze({**request, "exclude": ["tests/**"]})
    assert "src/api.py" in excluded["scope"]["analyzed_files"]
    assert excluded["test_obligations"]
    assert _candidate_tests(excluded) == []
