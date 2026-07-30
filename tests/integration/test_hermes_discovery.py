from __future__ import annotations

import importlib.metadata
import importlib.util
import inspect
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).parents[2]
INSTALL_COMMAND = "hermes plugins install sergiparpal/semantic-diff-weaver --enable"

# Hermes loads a directory plugin by executing the root `__init__.py` through
# `spec_from_file_location`, with the plugin directory as the submodule search path and never on
# `sys.path`. Both scripts below reproduce exactly that, in a subprocess, so the shim's own
# `sys.path` bootstrap is observed rather than inherited from the test session.
_LOAD_FROM_CLONE = """
import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path


class FakeContext:
    llm = None

    def __init__(self):
        self.tools = []

    def register_tool(self, **kwargs):
        self.tools.append(kwargs["name"])


plugin_directory = sys.argv[1]
before = list(sys.path)
spec = importlib.util.spec_from_file_location(
    "hermes_plugin_semantic_diff_weaver",
    str(Path(plugin_directory, "__init__.py")),
    submodule_search_locations=[plugin_directory],
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

context = FakeContext()
module.register(context)
resolved = importlib.machinery.PathFinder.find_spec("semantic_diff_weaver", [plugin_directory])
print(
    json.dumps(
        {
            "tools": context.tools,
            "bootstrapped": plugin_directory in sys.path,
            "kept_existing_entries_first": (
                plugin_directory in sys.path and sys.path.index(plugin_directory) >= len(before)
            ),
            "clone_is_importable": resolved is not None and resolved.origin is not None,
        }
    )
)
"""

_LOAD_WITHOUT_PYDANTIC = """
import importlib.util
import sys
from pathlib import Path


class BlockPydantic:
    # A Hermes host whose environment never had the plugin's runtime dependencies installed.
    def find_spec(self, name, path=None, target=None):
        if name.partition(".")[0] == "pydantic":
            raise ModuleNotFoundError("No module named 'pydantic'", name=name)
        return None


sys.meta_path.insert(0, BlockPydantic())

plugin_directory = sys.argv[1]
spec = importlib.util.spec_from_file_location(
    "hermes_plugin_semantic_diff_weaver",
    str(Path(plugin_directory, "__init__.py")),
    submodule_search_locations=[plugin_directory],
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
"""


def _installed_clone(root: Path) -> Path:
    """Build what `hermes plugins install OWNER/REPO` leaves behind: a clone, never a build."""
    directory = root / "plugins" / "semantic-diff-weaver"
    directory.mkdir(parents=True)
    for name in ("plugin.yaml", "__init__.py", "after-install.md"):
        shutil.copy2(REPOSITORY_ROOT / name, directory / name)
    shutil.copytree(
        REPOSITORY_ROOT / "semantic_diff_weaver",
        directory / "semantic_diff_weaver",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return directory


def _load_clone(
    script: str, directory: Path, working_directory: Path
) -> subprocess.CompletedProcess[str]:
    # `-P` keeps the working directory off `sys.path` and `PYTHONPATH` is dropped, so the only
    # route to the package is the one the shim installs.
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    return subprocess.run(
        [sys.executable, "-P", "-c", script, str(directory)],
        cwd=working_directory,
        env=environment,
        capture_output=True,
        encoding="utf-8",
        errors="strict",
        check=False,
        shell=False,
    )


def test_manifest_and_entry_point_agree() -> None:
    root = REPOSITORY_ROOT
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


def test_manifest_declares_the_installer_contract() -> None:
    """`--enable` makes the plugin live at install time, so the manifest states what loads."""
    manifest = yaml.safe_load((REPOSITORY_ROOT / "plugin.yaml").read_text(encoding="utf-8"))

    assert manifest["manifest_version"] == 1
    assert manifest["kind"] == "standalone"
    assert manifest["name"] == "semantic-diff-weaver"
    assert manifest["provides_hooks"] == []


def test_documented_install_command_names_this_plugin() -> None:
    manifest = yaml.safe_load((REPOSITORY_ROOT / "plugin.yaml").read_text(encoding="utf-8"))
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    after_install = (REPOSITORY_ROOT / "after-install.md").read_text(encoding="utf-8")

    assert INSTALL_COMMAND in readme
    assert INSTALL_COMMAND.endswith(f"/{manifest['name']} --enable")
    assert f"hermes plugins enable {manifest['name']}" in after_install


def test_a_repository_clone_registers_the_tool_without_being_installed(tmp_path: Path) -> None:
    """The install path clones and never builds, so the checkout has to load on its own."""
    directory = _installed_clone(tmp_path)

    completed = _load_clone(_LOAD_FROM_CLONE, directory, tmp_path)

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["tools"] == ["analyze_semantic_diff"]
    assert report["bootstrapped"] is True
    assert report["clone_is_importable"] is True
    # Appended, not inserted: an installed copy stays authoritative when both are present.
    assert report["kept_existing_entries_first"] is True


def test_a_missing_runtime_dependency_is_reported_by_name(tmp_path: Path) -> None:
    """A clone brings no environment with it; the one likely failure has to be legible."""
    directory = _installed_clone(tmp_path)

    completed = _load_clone(_LOAD_WITHOUT_PYDANTIC, directory, tmp_path)

    assert completed.returncode != 0
    assert "pydantic" in completed.stderr
    assert "python -m pip install" in completed.stderr


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
