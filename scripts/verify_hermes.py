"""Verify the plugin against an installed real Hermes runtime."""

from __future__ import annotations

import importlib.metadata
import inspect
import os
import shutil
import tempfile
from pathlib import Path

PLUGIN_NAME = "semantic-diff-weaver"
TOOL_NAME = "analyze_semantic_diff"


def _require_parameters(callable_object: object, expected: set[str]) -> None:
    actual = set(inspect.signature(callable_object).parameters)
    missing = expected - actual
    if missing:
        raise AssertionError(f"{callable_object!r} is missing parameters: {sorted(missing)}")


def _assert_loaded(manager: object, source: str) -> None:
    matches = [item for item in manager.list_plugins() if item["name"] == PLUGIN_NAME]
    if len(matches) != 1:
        raise AssertionError(f"expected one {source} plugin, found {matches!r}")
    plugin = matches[0]
    if not plugin["enabled"] or plugin["error"] is not None:
        raise AssertionError(f"{source} plugin did not load cleanly: {plugin!r}")
    if plugin["tools"] != 1:
        raise AssertionError(f"{source} plugin did not register exactly {TOOL_NAME!r}")


def main() -> int:
    import hermes_cli.plugins as plugins
    from agent.plugin_llm import PluginLlm

    version = importlib.metadata.version("hermes-agent")
    _require_parameters(
        plugins.PluginContext.register_tool,
        {"name", "toolset", "schema", "handler", "description", "override"},
    )
    _require_parameters(
        PluginLlm.complete_structured,
        {
            "instructions",
            "input",
            "json_schema",
            "json_mode",
            "schema_name",
            "max_tokens",
            "timeout",
        },
    )

    entry_points = list(
        importlib.metadata.entry_points().select(group="hermes_agent.plugins", name=PLUGIN_NAME)
    )
    if len(entry_points) != 1:
        raise AssertionError(f"expected one installed entry point, found {len(entry_points)}")
    if not callable(entry_points[0].load().register):
        raise AssertionError("installed entry point does not expose register(ctx)")

    # Hermes does not currently expose public discovery-isolation hooks. Keep the compatibility
    # shims local to this process and restore every value so production plugin code never depends
    # on these internals.
    original_enabled = plugins._get_enabled_plugins
    original_bundled = plugins.get_bundled_plugins_dir
    original_entry_points = plugins.PluginManager._scan_entry_points
    original_home = os.environ.get("HERMES_HOME")
    plugins._get_enabled_plugins = lambda: {PLUGIN_NAME}
    try:
        with tempfile.TemporaryDirectory(prefix="semantic-diff-weaver-hermes-") as temporary:
            root = Path(temporary)
            empty_bundled = root / "bundled"
            empty_bundled.mkdir()
            plugins.get_bundled_plugins_dir = lambda: empty_bundled
            os.environ["HERMES_HOME"] = str(root / "home")

            entry_point_manager = plugins.PluginManager()
            entry_point_manager.discover_and_load()
            _assert_loaded(entry_point_manager, "entry-point")

            project_root = Path(__file__).resolve().parents[1]
            directory = root / "home" / "plugins" / PLUGIN_NAME
            directory.mkdir(parents=True)
            shutil.copy2(project_root / "plugin.yaml", directory / "plugin.yaml")
            shutil.copy2(project_root / "__init__.py", directory / "__init__.py")
            plugins.PluginManager._scan_entry_points = lambda self: []

            directory_manager = plugins.PluginManager()
            directory_manager.discover_and_load()
            _assert_loaded(directory_manager, "directory")
    finally:
        plugins._get_enabled_plugins = original_enabled
        plugins.get_bundled_plugins_dir = original_bundled
        plugins.PluginManager._scan_entry_points = original_entry_points
        if original_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = original_home

    print(f"Hermes {version}: entry-point and directory discovery passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
