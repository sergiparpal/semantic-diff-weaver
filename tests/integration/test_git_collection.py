from __future__ import annotations

from semantic_diff_weaver.ast_diff import analyze_ast
from semantic_diff_weaver.git_diff import GitRepository, collect_diff
from semantic_diff_weaver.models import WeaverConfig


def test_add_delete_and_no_change_paths(repo_factory) -> None:
    repo_path, base, head = repo_factory(
        {"old.py": "def old():\n    return 1\n", "README.md": "old\n"},
        {"new.py": "def new():\n    return 2\n", "README.md": "new\n"},
        remove=("old.py",),
    )
    result = collect_diff(GitRepository.open(str(repo_path)), base, head, WeaverConfig())
    assert {item.status[:1] for item in result.files} == {"A", "D"}
    assert result.excluded_counts["path_filter"] == 1


def test_secret_and_binary_files_are_excluded(repo_factory) -> None:
    repo_path, base, head = repo_factory(
        {"safe.py": "x = 1\n", ".env": "TOKEN=old\n"},
        {"safe.py": "x = 2\n", ".env": "TOKEN=supersecret\n", "binary.py": "x\x00y"},
    )
    result = collect_diff(GitRepository.open(str(repo_path)), base, head, WeaverConfig())
    assert [item.path for item in result.files] == ["safe.py"]
    assert result.excluded_counts["secret_filename"] == 1
    assert result.excluded_counts["binary"] == 1


def test_exact_file_rename_metadata_is_preserved(repo_factory) -> None:
    content = "def renamed(x):\n    return x + 1\n"
    repo_path, base, head = repo_factory(
        {"src/old.py": content}, {"src/new.py": content}, remove=("src/old.py",)
    )
    result = collect_diff(GitRepository.open(str(repo_path)), base, head, WeaverConfig())
    assert len(result.files) == 1
    assert result.files[0].status.startswith("R")
    assert result.files[0].old_path == "src/old.py"
    assert result.files[0].new_path == "src/new.py"


def test_detected_copy_is_analyzed_as_new_surface(repo_factory) -> None:
    original = "def copied():\n    return 1\n"
    repo_path, base, head = repo_factory(
        {"src/original.py": original},
        {
            "src/original.py": "def copied():\n    return 2\n",
            "src/copied.py": original,
        },
    )
    result = collect_diff(GitRepository.open(str(repo_path)), base, head, WeaverConfig())
    copied = next(item for item in result.files if item.path == "src/copied.py")
    assert copied.status.startswith("C")
    assert copied.hunks[0].new_start == 1
    deltas = analyze_ast([copied]).deltas
    assert any(item.kind == "symbol_added" and item.symbol == "copied" for item in deltas)
