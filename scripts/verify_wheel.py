"""Install the built wheel into a clean environment and verify plugin discovery metadata."""

from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile
from pathlib import Path


def _environment_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def main() -> int:
    artifact_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "dist").resolve(strict=True)
    wheels = sorted(artifact_dir.glob("semantic_diff_weaver-*.whl"))
    if len(wheels) != 1:
        print(f"expected exactly one wheel in {artifact_dir}, found {len(wheels)}", file=sys.stderr)
        return 2
    source_distributions = sorted(artifact_dir.glob("semantic_diff_weaver-*.tar.gz"))
    if len(source_distributions) != 1:
        print(
            f"expected exactly one sdist in {artifact_dir}, found {len(source_distributions)}",
            file=sys.stderr,
        )
        return 2
    forbidden_parts = {".git", ".pytest_cache", "__pycache__", ".venv", "tests"}
    with zipfile.ZipFile(wheels[0]) as archive:
        wheel_names = archive.namelist()
        if any(forbidden_parts & set(Path(name).parts) for name in wheel_names):
            print("wheel contains a forbidden cache, repository, or test path", file=sys.stderr)
            return 2
        metadata_name = next(name for name in wheel_names if name.endswith(".dist-info/METADATA"))
        metadata = archive.read(metadata_name).decode("utf-8", errors="strict")
        if "License-Expression: MIT" not in metadata:
            print("wheel metadata does not declare the MIT license", file=sys.stderr)
            return 2
        if not any(name.endswith(".dist-info/licenses/LICENSE") for name in wheel_names):
            print("wheel does not contain LICENSE", file=sys.stderr)
            return 2
    with tarfile.open(source_distributions[0], mode="r:gz") as archive:
        source_names = archive.getnames()
        if any(forbidden_parts & set(Path(name).parts) for name in source_names):
            print("sdist contains a forbidden cache, repository, or test path", file=sys.stderr)
            return 2
        if not any(name.endswith("/LICENSE") for name in source_names):
            print("sdist does not contain LICENSE", file=sys.stderr)
            return 2
    with tempfile.TemporaryDirectory(prefix="semantic-diff-weaver-wheel-") as temporary:
        environment = Path(temporary) / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
        python = _environment_python(environment)
        subprocess.run(
            [str(python), "-m", "pip", "install", str(wheels[0])],
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
