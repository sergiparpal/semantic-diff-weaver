from __future__ import annotations

from semantic_diff_weaver.ast_diff import StructuralDelta
from semantic_diff_weaver.git_diff import GitRepository
from semantic_diff_weaver.models import LineRange, WeaverConfig
from semantic_diff_weaver.semantic_candidates import build_candidates
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
