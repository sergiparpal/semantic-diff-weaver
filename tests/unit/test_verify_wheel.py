"""Offline checks of `scripts/verify_wheel.py`'s archive inspection.

The gate runs the script against the real build, which only ever proves that a good wheel is
accepted. These synthetic distributions prove the other half: each defect the script exists to
catch is rejected with its own message, before anything is installed.
"""

from __future__ import annotations

import io
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts import verify_wheel
from scripts.verify_wheel import ArtifactError, check_sdist, check_wheel, find_artifacts

DIST_INFO = "semantic_diff_weaver-9.9.9.dist-info"
ENTRY_POINTS = """[console_scripts]
semantic-diff-weaver = semantic_diff_weaver.cli:main
semantic-diff-weaver-mcp = semantic_diff_weaver.mcp_server:main

[hermes_agent.plugins]
semantic-diff-weaver = semantic_diff_weaver.plugin
"""
METADATA = "Metadata-Version: 2.4\nName: semantic-diff-weaver\nLicense-Expression: MIT\n"


def _wheel_members() -> dict[str, str]:
    return {
        "semantic_diff_weaver/__init__.py": "",
        f"{DIST_INFO}/METADATA": METADATA,
        f"{DIST_INFO}/entry_points.txt": ENTRY_POINTS,
        f"{DIST_INFO}/licenses/LICENSE": "MIT License\n",
    }


def _write_wheel(directory: Path, members: dict[str, str]) -> Path:
    path = directory / "semantic_diff_weaver-9.9.9-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in members.items():
            archive.writestr(name, text)
    return path


def _write_sdist(directory: Path, names: list[str]) -> Path:
    path = directory / "semantic_diff_weaver-9.9.9.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for name in names:
            info = tarfile.TarInfo(name)
            info.size = 0
            archive.addfile(info, io.BytesIO(b""))
    return path


SDIST_NAMES = [
    "semantic_diff_weaver-9.9.9/LICENSE",
    "semantic_diff_weaver-9.9.9/pyproject.toml",
    "semantic_diff_weaver-9.9.9/semantic_diff_weaver/__init__.py",
]


def test_a_well_formed_pair_passes_the_archive_checks(tmp_path: Path) -> None:
    check_wheel(_write_wheel(tmp_path, _wheel_members()))
    check_sdist(_write_sdist(tmp_path, SDIST_NAMES))


def _replace_entry_points(text: str) -> dict[str, str]:
    members = _wheel_members()
    members[f"{DIST_INFO}/entry_points.txt"] = text
    return members


@pytest.mark.parametrize(
    ("members", "message"),
    [
        pytest.param(
            _replace_entry_points(
                ENTRY_POINTS.replace(
                    "semantic-diff-weaver-mcp = semantic_diff_weaver.mcp_server:main\n", ""
                )
            ),
            "wheel does not declare the semantic-diff-weaver-mcp server",
            id="missing-mcp-server",
        ),
        pytest.param(
            _replace_entry_points(
                ENTRY_POINTS.replace("semantic-diff-weaver = semantic_diff_weaver.cli:main\n", "")
            ),
            "wheel does not declare the semantic-diff-weaver console script",
            id="missing-console-script",
        ),
        pytest.param(
            _replace_entry_points(
                ENTRY_POINTS.replace("semantic-diff-weaver = semantic_diff_weaver.plugin\n", "")
            ),
            "wheel does not declare the Hermes plugin entry point",
            id="missing-hermes-plugin",
        ),
        pytest.param(
            # Declared, but in the wrong group: a substring match would have accepted this.
            _replace_entry_points(
                "[console_scripts]\n"
                "semantic-diff-weaver = semantic_diff_weaver.cli:main\n\n"
                "[hermes_agent.plugins]\n"
                "semantic-diff-weaver = semantic_diff_weaver.plugin\n"
                "semantic-diff-weaver-mcp = semantic_diff_weaver.mcp_server:main\n"
            ),
            "wheel does not declare the semantic-diff-weaver-mcp server",
            id="mcp-server-in-the-wrong-group",
        ),
        pytest.param(
            # A near-miss target: a substring match would have accepted this too.
            _replace_entry_points(ENTRY_POINTS.replace("cli:main", "cli:main_legacy")),
            "wheel does not declare the semantic-diff-weaver console script",
            id="near-miss-console-target",
        ),
        pytest.param(
            _replace_entry_points("semantic-diff-weaver = semantic_diff_weaver.cli:main\n"),
            "wheel entry_points.txt is malformed",
            id="entry-points-without-a-group",
        ),
        pytest.param(
            {k: v for k, v in _wheel_members().items() if not k.endswith("entry_points.txt")},
            "wheel does not declare any entry points",
            id="no-entry-points",
        ),
        pytest.param(
            {k: v for k, v in _wheel_members().items() if not k.endswith("METADATA")},
            "wheel does not contain METADATA",
            id="no-metadata",
        ),
        pytest.param(
            {**_wheel_members(), f"{DIST_INFO}/METADATA": METADATA.replace("MIT", "MIT-0")},
            "wheel metadata does not declare the MIT license",
            id="different-license",
        ),
        pytest.param(
            {k: v for k, v in _wheel_members().items() if not k.endswith("LICENSE")},
            "wheel does not contain LICENSE",
            id="no-license-file",
        ),
    ],
)
def test_each_wheel_defect_is_rejected_with_its_own_message(
    tmp_path: Path, members: dict[str, str], message: str
) -> None:
    with pytest.raises(ArtifactError) as rejected:
        check_wheel(_write_wheel(tmp_path, members))
    assert str(rejected.value) == message


@pytest.mark.parametrize(
    "forbidden",
    [
        "tests/test_api.py",
        "semantic_diff_weaver/__pycache__/cli.cpython-312.pyc",
        ".git/config",
        ".venv/pyvenv.cfg",
        ".pytest_cache/README.md",
    ],
)
def test_forbidden_paths_are_rejected_in_both_distributions(tmp_path: Path, forbidden: str) -> None:
    with pytest.raises(ArtifactError, match="wheel contains a forbidden"):
        check_wheel(_write_wheel(tmp_path, {**_wheel_members(), forbidden: ""}))
    with pytest.raises(ArtifactError, match="sdist contains a forbidden"):
        check_sdist(
            _write_sdist(tmp_path, [*SDIST_NAMES, f"semantic_diff_weaver-9.9.9/{forbidden}"])
        )


def test_an_sdist_without_license_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ArtifactError, match="sdist does not contain LICENSE"):
        check_sdist(_write_sdist(tmp_path, [n for n in SDIST_NAMES if not n.endswith("LICENSE")]))


@pytest.mark.parametrize(
    ("wheels", "sdists", "message"),
    [
        (0, 1, "expected exactly one wheel"),
        (2, 1, "expected exactly one wheel"),
        (1, 0, "expected exactly one sdist"),
        (1, 2, "expected exactly one sdist"),
    ],
)
def test_the_artifact_directory_must_hold_exactly_one_of_each(
    tmp_path: Path, wheels: int, sdists: int, message: str
) -> None:
    for index in range(wheels):
        (tmp_path / f"semantic_diff_weaver-{index}-py3-none-any.whl").touch()
    for index in range(sdists):
        (tmp_path / f"semantic_diff_weaver-{index}.tar.gz").touch()
    with pytest.raises(ArtifactError, match=message):
        find_artifacts(tmp_path)


def test_main_reports_a_defect_and_never_installs(tmp_path: Path, monkeypatch, capsys) -> None:
    members = _replace_entry_points(ENTRY_POINTS.replace("semantic-diff-weaver-mcp", "other-mcp"))
    _write_wheel(tmp_path, members)
    _write_sdist(tmp_path, SDIST_NAMES)

    def never(wheel: Path) -> int:
        raise AssertionError("a rejected wheel must not be installed")

    monkeypatch.setattr(verify_wheel, "verify_installation", never)
    monkeypatch.setattr(sys, "argv", ["verify_wheel.py", str(tmp_path)])
    assert verify_wheel.main() == 2
    assert capsys.readouterr().err.strip() == (
        "wheel does not declare the semantic-diff-weaver-mcp server"
    )


def test_main_installs_only_after_both_archives_pass(tmp_path: Path, monkeypatch) -> None:
    wheel = _write_wheel(tmp_path, _wheel_members())
    _write_sdist(tmp_path, SDIST_NAMES)
    installed: list[Path] = []

    def record(candidate: Path) -> int:
        installed.append(candidate)
        return 0

    monkeypatch.setattr(verify_wheel, "verify_installation", record)
    monkeypatch.setattr(sys, "argv", ["verify_wheel.py", str(tmp_path)])
    assert verify_wheel.main() == 0
    assert installed == [wheel.resolve()]
