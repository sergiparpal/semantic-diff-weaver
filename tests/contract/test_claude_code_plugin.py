"""The Claude Code plugin files at the repository root stay consistent with the package."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from semantic_diff_weaver import __version__

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
