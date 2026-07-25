from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from semantic_diff_weaver.models import AnalysisResult, BothEnvelope, MarkdownEnvelope
from semantic_diff_weaver.service import analyze


def test_json_and_envelopes_validate_against_local_models(
    repo_factory: Callable[..., tuple[Path, str, str]],
) -> None:
    repo, base, head = repo_factory(
        {"a.py": "def f(x):\n    return x < 1\n"},
        {"a.py": "def f(x):\n    return x <= 1\n"},
    )
    common = {"repo_path": str(repo), "base_ref": base, "head_ref": head}
    assert AnalysisResult.model_validate(analyze({**common, "output_format": "json"}))
    assert MarkdownEnvelope.model_validate(analyze({**common, "output_format": "markdown"}))
    both = BothEnvelope.model_validate(analyze({**common, "output_format": "both"}))
    assert both.analysis.analysis_id in both.markdown or both.markdown.startswith(
        "## Semantic Diff Test Brief"
    )
