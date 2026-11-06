"""Verify the plugin against an installed real Hermes runtime."""

from __future__ import annotations

import importlib.metadata
import inspect
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

PLUGIN_NAME = "semantic-diff-weaver"
TOOL_NAMES = ("analyze_semantic_diff", "generate_test_skeletons")
# The distribution version of a Hermes built from its Git tag. Releases after 0.19.0 exist only as
# tags, and their pyproject.toml carries this placeholder rather than the release's version.
SOURCE_BUILD_VERSION = "0.0.0"
UNKNOWN_SOURCE_BUILD = "built from source, version unknown"


def hermes_version_label(
    installed: str | None = None, version_info: Callable[[], Any] | None = None
) -> str:
    """The Hermes release under test, named as Hermes names itself.

    A release from PyPI carries its version in its metadata. One built from its Git tag carries the
    placeholder there, and names itself from its install stamp or its checkout instead, as
    `hermes --version` does. That is read only to label the result, so a failure to read it leaves
    the label unknown rather than failing the check it labels.
    """
    if installed is None:
        installed = importlib.metadata.version("hermes-agent")
    if installed != SOURCE_BUILD_VERSION:
        return installed
    try:
        if version_info is None:
            from hermes_cli.version_info import get_version_info

            version_info = get_version_info
        info = version_info()
        base, commit = str(info.base_version), info.commit
    except Exception:
        return UNKNOWN_SOURCE_BUILD
    if base == "unknown":
        return UNKNOWN_SOURCE_BUILD
    return f"{base} (source checkout {str(commit)[:12]})" if commit else base


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
    if plugin["tools"] != len(TOOL_NAMES):
        raise AssertionError(f"{source} plugin did not register exactly {list(TOOL_NAMES)!r}")


def main() -> int:
    import hermes_cli.plugins as plugins
    from agent.plugin_llm import PluginLlm

    from semantic_diff_weaver.llm_client import LlmClient

    version = hermes_version_label()
    _require_parameters(
        plugins.PluginContext.register_tool,
        {"name", "toolset", "schema", "handler", "description", "override"},
    )
    # Exactly what the interpreter passes: tests/contract/test_llm_client_protocol.py pins the
    # protocol to its single call site, so a list kept by hand here cannot drift from it.
    _require_parameters(
        PluginLlm.complete_structured,
        set(inspect.signature(LlmClient.complete_structured).parameters) - {"self"},
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

            # The directory form is what `hermes plugins install OWNER/REPO` produces, so copy
            # what that installer clones — manifest, loadable root, and the package itself —
            # rather than a two-file stub. Whether the package resolves from the clone alone is
            # asserted offline in tests/integration/test_hermes_discovery.py; the wheel is
            # necessarily installed in this environment for the entry-point check above.
            project_root = Path(__file__).resolve().parents[1]
            directory = root / "home" / "plugins" / PLUGIN_NAME
            directory.mkdir(parents=True)
            for name in ("plugin.yaml", "__init__.py", "after-install.md"):
                shutil.copy2(project_root / name, directory / name)
            shutil.copytree(
                project_root / "semantic_diff_weaver",
                directory / "semantic_diff_weaver",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
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
