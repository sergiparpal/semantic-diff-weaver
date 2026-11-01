"""Verify the built distributions before they ship.

Two phases. The archive inspection reads both distributions without installing anything, so
it runs offline and is what `tests/unit/test_verify_wheel.py` covers. The install phase puts
the wheel into a clean environment and exercises every entry point it registers; it needs the
package index for the runtime dependencies, so it runs only in the local gate
(`python -m build && python scripts/verify_wheel.py dist`).
"""

from __future__ import annotations

import configparser
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile
from collections.abc import Iterable
from pathlib import Path

FORBIDDEN_PARTS = frozenset({".git", ".pytest_cache", "__pycache__", ".venv", "tests"})
# (group, name, value, what the failure message calls it). Matched per group and by exact
# value, so a line under the wrong group or a near-miss target does not count as declared.
REQUIRED_ENTRY_POINTS = (
    (
        "console_scripts",
        "semantic-diff-weaver",
        "semantic_diff_weaver.cli:main",
        "the semantic-diff-weaver console script",
    ),
    (
        "console_scripts",
        "semantic-diff-weaver-mcp",
        "semantic_diff_weaver.mcp_server:main",
        "the semantic-diff-weaver-mcp server",
    ),
    (
        "hermes_agent.plugins",
        "semantic-diff-weaver",
        "semantic_diff_weaver.plugin",
        "the Hermes plugin entry point",
    ),
)
MCP_TOOL_NAMES = ["analyze_semantic_diff", "generate_test_skeletons"]


class ArtifactError(Exception):
    """A distribution that must not ship; the message says why."""


def find_artifacts(artifact_dir: Path) -> tuple[Path, Path]:
    """Return the single wheel and the single sdist in `artifact_dir`."""
    wheels = sorted(artifact_dir.glob("semantic_diff_weaver-*.whl"))
    if len(wheels) != 1:
        raise ArtifactError(f"expected exactly one wheel in {artifact_dir}, found {len(wheels)}")
    source_distributions = sorted(artifact_dir.glob("semantic_diff_weaver-*.tar.gz"))
    if len(source_distributions) != 1:
        raise ArtifactError(
            f"expected exactly one sdist in {artifact_dir}, found {len(source_distributions)}"
        )
    return wheels[0], source_distributions[0]


def _has_forbidden_part(names: Iterable[str]) -> bool:
    return any(FORBIDDEN_PARTS & set(Path(name).parts) for name in names)


class _EntryPointParser(configparser.ConfigParser):
    def optionxform(self, optionstr: str) -> str:
        # Entry point names are case-sensitive; configparser lowercases them by default.
        return optionstr


def _parse_entry_points(text: str) -> configparser.ConfigParser:
    # `=` only: entry point values contain `:`, which configparser would otherwise split on.
    parser = _EntryPointParser(delimiters=("=",), interpolation=None)
    parser.read_string(text)
    return parser


def check_wheel(wheel: Path) -> None:
    """Raise `ArtifactError` unless the wheel's contents and metadata are shippable."""
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        if _has_forbidden_part(names):
            raise ArtifactError("wheel contains a forbidden cache, repository, or test path")
        metadata_name = next((name for name in names if name.endswith(".dist-info/METADATA")), None)
        if metadata_name is None:
            raise ArtifactError("wheel does not contain METADATA")
        metadata = archive.read(metadata_name).decode("utf-8", errors="strict")
        if "License-Expression: MIT" not in metadata.splitlines():
            raise ArtifactError("wheel metadata does not declare the MIT license")
        entry_points_name = next(
            (name for name in names if name.endswith(".dist-info/entry_points.txt")), None
        )
        if entry_points_name is None:
            raise ArtifactError("wheel does not declare any entry points")
        try:
            declared = _parse_entry_points(
                archive.read(entry_points_name).decode("utf-8", errors="strict")
            )
        except (configparser.Error, UnicodeDecodeError) as exc:
            raise ArtifactError("wheel entry_points.txt is malformed") from exc
        for group, name, value, label in REQUIRED_ENTRY_POINTS:
            if declared.get(group, name, fallback=None) != value:
                raise ArtifactError(f"wheel does not declare {label}")
        if not any(name.endswith(".dist-info/licenses/LICENSE") for name in names):
            raise ArtifactError("wheel does not contain LICENSE")


def check_sdist(sdist: Path) -> None:
    """Raise `ArtifactError` unless the source distribution is shippable."""
    with tarfile.open(sdist, mode="r:gz") as archive:
        names = archive.getnames()
    if _has_forbidden_part(names):
        raise ArtifactError("sdist contains a forbidden cache, repository, or test path")
    if not any(name.endswith("/LICENSE") for name in names):
        raise ArtifactError("sdist does not contain LICENSE")


def _environment_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _installed_script(python: Path, name: str) -> Path:
    return python.parent / (f"{name}.exe" if os.name == "nt" else name)


def verify_installation(wheel: Path) -> int:
    """Install the wheel into a clean environment and exercise each registered entry point."""
    with tempfile.TemporaryDirectory(prefix="semantic-diff-weaver-wheel-") as temporary:
        environment = Path(temporary) / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
        python = _environment_python(environment)
        subprocess.run(
            [str(python), "-m", "pip", "install", str(wheel)],
            check=True,
            shell=False,
        )
        check = (
            "import importlib.metadata as m; "
            "eps=list(m.entry_points().select(group='hermes_agent.plugins', "
            "name='semantic-diff-weaver')); "
            "assert len(eps)==1; module=eps[0].load(); assert callable(module.register); "
            "print(eps[0].value)"
        )
        subprocess.run([str(python), "-c", check], check=True, shell=False)
        console = (
            "import importlib.metadata as m; "
            "eps=list(m.entry_points().select(group='console_scripts', "
            "name='semantic-diff-weaver')); "
            "assert len(eps)==1; entry=eps[0].load(); assert callable(entry); "
            "print(eps[0].value)"
        )
        subprocess.run([str(python), "-c", console], check=True, shell=False)
        script = _installed_script(python, "semantic-diff-weaver")
        if not script.exists():
            print(f"installed environment is missing {script.name}", file=sys.stderr)
            return 2
        subprocess.run([str(script), "--help"], check=True, shell=False, capture_output=True)
        # The subcommand imports its module only when it runs, so `--help` alone would not show
        # that the installed package can.
        subprocess.run(
            [str(script), "skeletons", "--help"], check=True, shell=False, capture_output=True
        )
        server = _installed_script(python, "semantic-diff-weaver-mcp")
        if not server.exists():
            print(f"installed environment is missing {server.name}", file=sys.stderr)
            return 2
        # One request, then end of input: the server answers it and exits cleanly.
        listing = subprocess.run(
            [str(server)],
            input=b'{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}\n',
            check=True,
            shell=False,
            capture_output=True,
            timeout=120,
        )
        tools = [tool["name"] for tool in json.loads(listing.stdout)["result"]["tools"]]
        if tools != MCP_TOOL_NAMES:
            print(f"installed MCP server lists {tools}, expected {MCP_TOOL_NAMES}", file=sys.stderr)
            return 2
    return 0


def main() -> int:
    artifact_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "dist").resolve(strict=True)
    try:
        wheel, sdist = find_artifacts(artifact_dir)
        check_wheel(wheel)
        check_sdist(sdist)
    except ArtifactError as exc:
        print(exc, file=sys.stderr)
        return 2
    return verify_installation(wheel)


if __name__ == "__main__":
    raise SystemExit(main())
