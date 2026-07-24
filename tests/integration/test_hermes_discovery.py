from __future__ import annotations

import importlib.metadata
import importlib.util
import inspect
import tomllib
from pathlib import Path

import pytest
import yaml


def test_manifest_and_entry_point_agree() -> None:
    root = Path(__file__).parents[2]
    manifest = yaml.safe_load((root / "plugin.yaml").read_text(encoding="utf-8"))
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    configured = project["project"]["entry-points"]["hermes_agent.plugins"]
    assert configured[manifest["name"]] == "semantic_diff_weaver.plugin"
    entry_points = importlib.metadata.entry_points().select(group="hermes_agent.plugins")
    matches = [item for item in entry_points if item.name == manifest["name"]]
    if matches:
        assert len(matches) == 1
        module = matches[0].load()
        assert callable(module.register)
    assert manifest["provides_tools"] == ["analyze_semantic_diff"]


def test_real_hermes_discovery_when_runtime_is_available() -> None:
    if importlib.util.find_spec("hermes_cli") is None:
        pytest.skip("Hermes runtime is not installed in this isolated test environment")
    from agent.plugin_llm import PluginLlm  # pragma: no cover
    from hermes_cli.plugins import PluginContext  # pragma: no cover

    register_parameters = inspect.signature(PluginContext.register_tool).parameters
    llm_parameters = inspect.signature(PluginLlm.complete_structured).parameters
    assert {"name", "toolset", "schema", "handler", "description", "override"} <= set(
        register_parameters
    )
    assert {
        "instructions",
        "input",
        "json_schema",
        "json_mode",
        "schema_name",
        "max_tokens",
        "timeout",
    } <= set(llm_parameters)
