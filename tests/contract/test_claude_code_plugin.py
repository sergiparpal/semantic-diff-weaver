"""The Claude Code plugin files at the repository root stay consistent with the package."""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from semantic_diff_weaver import __version__, scoring
from semantic_diff_weaver.service import analyze

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _json(relative: str) -> dict[str, Any]:
    return json.loads((REPOSITORY_ROOT / relative).read_text(encoding="utf-8"))


def test_the_plugin_manifest_names_the_package_version() -> None:
    manifest = _json(".claude-plugin/plugin.json")
    assert manifest["name"] == "semantic-diff-weaver"
    assert manifest["version"] == __version__


def test_the_marketplace_lists_the_repository_root_as_its_only_plugin() -> None:
    marketplace = _json(".claude-plugin/marketplace.json")
    assert marketplace["owner"]["name"]
    assert [(item["name"], item["source"]) for item in marketplace["plugins"]] == [
        ("semantic-diff-weaver", "./")
    ]


def test_the_manifest_launches_the_declared_console_script() -> None:
    servers = _json(".claude-plugin/plugin.json")["mcpServers"]
    assert list(servers) == ["semantic-diff-weaver"]
    server = servers["semantic-diff-weaver"]
    pyproject = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]
    assert scripts["semantic-diff-weaver-mcp"] == "semantic_diff_weaver.mcp_server:main"
    assert server["args"][-1] == "semantic-diff-weaver-mcp"
    # Installed from the plugin's own checkout, never from an index a typo could redirect.
    assert server["args"][:2] == ["--from", "${CLAUDE_PLUGIN_ROOT}"]
    # The bound comes from CLAUDE_PROJECT_DIR in the server; the config must not override it.
    assert "env" not in server


def test_uv_rebuilds_the_server_when_package_sources_change() -> None:
    """`uvx --from` reuses a cached build until a cache key changes; the default is pyproject only.

    Without the source glob, a working copy installed through a local marketplace kept serving
    the code it was first built from, whatever was edited afterwards.
    """
    pyproject = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    keys = pyproject["tool"]["uv"]["cache-keys"]
    assert {"file": "pyproject.toml"} in keys
    assert {"file": "semantic_diff_weaver/**/*.py"} in keys


def test_there_is_no_root_mcp_config() -> None:
    """A root `.mcp.json` doubles as this repository's project MCP config in Claude Code.

    There `${CLAUDE_PLUGIN_ROOT}` is undefined, so a contributor opening the checkout got a
    broken server pending approval. The plugin declares its server in `plugin.json` instead.
    """
    assert not (REPOSITORY_ROOT / ".mcp.json").exists()


def test_the_skill_names_the_tool_and_has_frontmatter() -> None:
    text = (REPOSITORY_ROOT / "skills/semantic-diff/SKILL.md").read_text(encoding="utf-8")
    header, _, body = text.removeprefix("---\n").partition("\n---\n")
    fields = dict(line.split(": ", 1) for line in header.splitlines())
    assert fields["name"] == "semantic-diff"
    assert len(fields["description"]) <= 1024
    assert "analyze_semantic_diff" in body


FORMULA = re.compile(
    r"risk_score = round\(behavioral_impact \* ([\d.]+) \+ critical_path_weight \* ([\d.]+) "
    r"\+ test_gap_weight \* ([\d.]+) \+ change_surface_weight \* ([\d.]+)\)"
)
AXES = ("behavioral_impact", "critical_path_weight", "test_gap_weight", "change_surface_weight")


def _skill_scoring() -> tuple[dict[str, float], dict[str, int]]:
    """The weights and label thresholds as the skill states them, read from its text."""
    text = " ".join(
        (REPOSITORY_ROOT / "skills/semantic-diff/SKILL.md").read_text(encoding="utf-8").split()
    )
    formula = FORMULA.search(text)
    assert formula is not None
    weights = dict(zip(AXES, (float(value) for value in formula.groups()), strict=True))
    starts = {}
    for label in ("critical", "high", "medium"):
        found = re.search(rf"`{label}` from (\d+)", text)
        assert found is not None
        starts[label] = int(found.group(1))
    return weights, starts


def test_the_risk_formula_in_the_skill_reproduces_every_score(
    repo_factory: Callable[..., tuple[Path, str, str]],
) -> None:
    """The JSON carries a score's axes, not how they combine; the skill says, and must stay true.

    Checked against behavior, not constants: a test comparing the text with `HIGH_FROM` kept
    passing when `>=` became `>`, although the skill's "high from 60" was then false.
    """
    weights, starts = _skill_scoring()
    for label, start in starts.items():
        assert scoring.risk_label(start).value == label
        assert scoring.risk_label(start - 1).value != label
    repo, base, head = repo_factory(
        {"api.py": "def allowed(x):\n    return x < 5\n\n\ndef notify(user):\n    send(user)\n"},
        {
            "api.py": "def allowed(x):\n    return x <= 5\n\n\ndef notify(user):\n    send(user, 1)\n"
        },
    )
    result = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "json"}
    )
    assert result["behavior_changes"]
    for behavior in result["behavior_changes"]:
        explanation = behavior["score_explanation"]
        stated = round(sum(explanation[axis] * weight for axis, weight in weights.items()))
        assert behavior["risk_score"] == stated
