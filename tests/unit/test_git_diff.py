from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import semantic_diff_weaver.git_diff.collect as collect_module
import semantic_diff_weaver.git_diff.limits as limits_module
import semantic_diff_weaver.git_diff.repository as repository_module
from semantic_diff_weaver.errors import ErrorCode, WeaverError
from semantic_diff_weaver.git_diff import GitRepository, collect_diff
from semantic_diff_weaver.git_diff.collect import _file_hunks, _hunks_by_path
from semantic_diff_weaver.git_diff.limits import MAX_GIT_INPUT_BYTES
from semantic_diff_weaver.git_diff.parse import (
    parse_hunks_by_path as _parse_hunks_by_path,
)
from semantic_diff_weaver.git_diff.parse import (
    parse_name_status as _parse_name_status,
)
from semantic_diff_weaver.git_diff.parse import (
    parse_numstat as _parse_numstat,
)
from semantic_diff_weaver.git_diff.parse import (
    unquote_git_path as _unquote_git_path,
)
from semantic_diff_weaver.git_diff.process import OutputLimitExceeded, run_bounded_process
from semantic_diff_weaver.models import CriticalPath, WeaverConfig


def test_open_resolve_and_collect(repo_factory) -> None:
    repo_path, base, head = repo_factory(
        {"src/api.py": "def allowed(x):\n    return x < 5\n"},
        {"src/api.py": "def allowed(x):\n    return x <= 5\n"},
    )
    repo = GitRepository.open(str(repo_path / "src"))
    assert repo.resolve_ref(base) == base
    assert repo.resolve_ref(head) == head
    result = collect_diff(repo, base, head, WeaverConfig())
    assert len(result.files) == 1
    assert result.files[0].old_text
    assert result.files[0].new_text
    assert result.files[0].hunks
    assert result.changed_lines == 2


def test_git_boundary_rejects_a_filesystem_root_and_invalid_resolver_output(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: "git")
    monkeypatch.setattr(GitRepository, "run", lambda self, *args, **kwargs: tmp_path.anchor)
    with pytest.raises(WeaverError) as root_error:
        GitRepository.open(str(tmp_path))
    assert root_error.value.code is ErrorCode.PATH_OUTSIDE_REPOSITORY

    repo = GitRepository(tmp_path, "git")
    with pytest.raises(WeaverError) as ref_error:
        repo.resolve_ref("main")
    assert ref_error.value.code is ErrorCode.INVALID_REF


def test_crlf_move_and_rename_is_correlated(repo_factory) -> None:
    repo_path, base, head = repo_factory(
        {"src/old.py": "def old_name(x):\r\n    return x + 1\r\n"},
        {"src/new.py": "def new_name(x):\r\n    return x + 1\r\n"},
        remove=("src/old.py",),
    )
    result = collect_diff(GitRepository.open(str(repo_path)), base, head, WeaverConfig())
    assert len(result.files) == 1
    assert result.files[0].status.startswith("R")
    assert result.files[0].old_path == "src/old.py"
    assert result.files[0].new_path == "src/new.py"
    assert result.changed_lines == 2


@pytest.mark.parametrize("ref", ["--help", "-n", "bad\nref", ""])
def test_option_like_and_invalid_refs_are_rejected(repo_factory, ref: str) -> None:
    repo_path, _, _ = repo_factory({"a.py": "x = 1\n"}, {"a.py": "x = 2\n"})
    repo = GitRepository.open(str(repo_path))
    with pytest.raises(WeaverError) as caught:
        repo.resolve_ref(ref)
    assert caught.value.code is ErrorCode.INVALID_REF


def test_diff_limits_fail_with_safe_counts(repo_factory) -> None:
    repo_path, base, head = repo_factory(
        {"a.py": "x = 1\n"},
        {"a.py": "x = 2\ny = 3\n"},
    )
    config = WeaverConfig()
    config.rules.max_diff_lines = 1
    with pytest.raises(WeaverError) as caught:
        collect_diff(GitRepository.open(str(repo_path)), base, head, config)
    assert caught.value.code is ErrorCode.DIFF_TOO_LARGE
    assert "configured limit is 1" in caught.value.safe_message


def test_not_a_repository_is_safe(tmp_path: Path) -> None:
    with pytest.raises(WeaverError) as caught:
        GitRepository.open(str(tmp_path))
    assert caught.value.code is ErrorCode.NOT_A_GIT_REPOSITORY


def test_missing_git_is_safe(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(WeaverError) as caught:
        GitRepository.open(str(tmp_path))
    assert caught.value.code is ErrorCode.NOT_A_GIT_REPOSITORY


def test_nonexistent_ref_and_blob_paths_are_bounded(repo_factory) -> None:
    repo_path, _, head = repo_factory({"a.py": "x = 1\n"}, {"a.py": "x = 2\n"})
    repo = GitRepository.open(str(repo_path))
    with pytest.raises(WeaverError) as caught:
        repo.resolve_ref("does-not-exist")
    assert caught.value.code is ErrorCode.INVALID_REF
    assert repo.read_blob(head, "missing.py", 100) is None
    assert "a.py" in repo.list_files(head)


def test_git_output_and_decode_limits_are_safe(tmp_path: Path, monkeypatch) -> None:
    repo = GitRepository(tmp_path, "git")

    with pytest.raises(WeaverError) as input_error:
        repo.run(["cat-file", "--batch"], input_data=b"x" * (MAX_GIT_INPUT_BYTES + 1))
    assert input_error.value.code is ErrorCode.DIFF_TOO_LARGE

    def huge(*args, **kwargs):
        raise OutputLimitExceeded

    monkeypatch.setattr(repository_module, "run_bounded_process", huge)
    with pytest.raises(WeaverError) as caught:
        repo.run(["status"], max_bytes=10)
    assert caught.value.code is ErrorCode.DIFF_TOO_LARGE

    def invalid_utf8(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout=b"\xff", stderr=b"")

    monkeypatch.setattr(repository_module, "run_bounded_process", invalid_utf8)
    with pytest.raises(WeaverError) as decode_error:
        repo.run(["status"])
    assert decode_error.value.code is ErrorCode.PARSE_FAILURE


def test_process_output_is_stopped_while_streaming(tmp_path: Path) -> None:
    with pytest.raises(OutputLimitExceeded):
        run_bounded_process(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x' * 1000000)"],
            cwd=tmp_path,
            env={},
            input_data=None,
            max_bytes=1024,
        )


def test_tree_and_blob_batch_parsing_is_bounded(tmp_path: Path, monkeypatch) -> None:
    repo = GitRepository(tmp_path, "git")
    commit = "a" * 40
    good = "b" * 40
    binary = "c" * 40
    invalid_utf8 = "d" * 40
    oversized = "e" * 40
    not_blob = "f" * 40

    with pytest.raises(WeaverError):
        repo.tree_entries("unresolved", ["good.py"])
    assert repo.tree_entries(commit, []) == {}

    tree_output = (
        f"100644 blob {good}\tgood.py\0".encode()
        + b"malformed\0"
        + b"bad-header\tgood.py\0"
        + b"100644 blob invalid\tbad.py\0"
        + f"100644 blob {binary}\t../escape.py\0".encode()
        + f"100644 blob {invalid_utf8}\tother.py\0".encode()
        + f"\xff blob {oversized}\tbad-mode.py\0".encode()
    )
    monkeypatch.setattr(repo, "run", lambda *args, **kwargs: tree_output)
    entries = repo.tree_entries(commit, ["good.py"])
    assert entries["good.py"].mode == "100644"
    assert entries["good.py"].object_id == good

    with pytest.raises(WeaverError):
        repo.read_blob_objects({"invalid"}, 5)
    assert repo.read_blob_objects(set(), 5) == {}

    def batch_output(arguments, **kwargs):
        if "--batch-check" in arguments:
            return (
                f"{good} blob 5\n"
                f"{binary} blob 1\n"
                f"{invalid_utf8} blob 1\n"
                f"{oversized} blob 6\n"
                f"{not_blob} tree 1\n"
                f"{'9' * 40} blob 1\n"
                "malformed\n"
            ).encode()
        prefix = (f"{good} blob 5\nhello\n{binary} blob 1\n\0\n{invalid_utf8} blob 1\n").encode()
        return prefix + b"\xff\n"

    monkeypatch.setattr(repo, "run", batch_output)
    blobs = repo.read_blob_objects({good, binary, invalid_utf8, oversized, not_blob}, max_bytes=5)
    assert blobs == {
        good: "hello",
        binary: None,
        invalid_utf8: None,
        oversized: None,
        not_blob: None,
    }


def test_blob_batch_failures_remain_safe(tmp_path: Path, monkeypatch) -> None:
    repo = GitRepository(tmp_path, "git")
    object_id = "a" * 40

    def fail(*args, **kwargs):
        raise WeaverError(ErrorCode.INVALID_REF, "safe", "retry")

    monkeypatch.setattr(repo, "run", fail)
    assert repo.read_blob_objects({object_id}, 5) == {object_id: None}

    def fail_content(arguments, **kwargs):
        if "--batch-check" in arguments:
            return f"{object_id} blob 5\n".encode()
        raise WeaverError(ErrorCode.INVALID_REF, "safe", "retry")

    monkeypatch.setattr(repo, "run", fail_content)
    assert repo.read_blob_objects({object_id}, 5) == {object_id: None}


def test_collection_batches_tree_and_blob_commands(repo_factory, monkeypatch) -> None:
    repo_path, base, head = repo_factory(
        {"one.py": "x = 1\n", "two.py": "y = 1\n"},
        {"one.py": "x = 2\n", "two.py": "y = 2\n"},
    )
    repo = GitRepository.open(str(repo_path))
    real_run = run_bounded_process
    commands: list[list[str]] = []

    def spy(*args, **kwargs):
        commands.append(args[0])
        return real_run(*args, **kwargs)

    monkeypatch.setattr(repository_module, "run_bounded_process", spy)
    result = collect_diff(repo, base, head, WeaverConfig())
    assert len(result.files) == 2
    assert sum("ls-tree" in command for command in commands) == 2
    assert sum("--batch-check" in command for command in commands) == 1
    assert sum("--batch" in command for command in commands) == 1
    assert all("show" not in command for command in commands)


def test_oversized_diff_can_prioritize_explicit_critical_scope(repo_factory) -> None:
    repo_path, base, head = repo_factory(
        {"critical.py": "x = 1\n", "other.py": "y = 1\n"},
        {"critical.py": "x = 2\n", "other.py": "y = 2\n"},
    )
    config = WeaverConfig(critical_paths=[CriticalPath(pattern="critical.py", weight=100)])
    config.rules.max_changed_files = 1
    config.rules.max_diff_lines = 2
    result = collect_diff(GitRepository.open(str(repo_path)), base, head, config)
    assert [item.path for item in result.files] == ["critical.py"]
    assert result.truncated is True
    assert result.omitted_counts == {"resource_prioritization": 1}


def test_secret_source_of_a_rename_remains_excluded(repo_factory) -> None:
    source = "def value():\n    return 1\n"
    repo_path, base, head = repo_factory(
        {".env": source},
        {"safe.py": source},
        remove=(".env",),
    )
    result = collect_diff(GitRepository.open(str(repo_path)), base, head, WeaverConfig())
    assert result.files == []
    assert result.excluded_counts["secret_filename"] >= 1


def test_blob_and_tree_edge_cases_are_bounded(tmp_path: Path, monkeypatch) -> None:
    repo = GitRepository(tmp_path, "git")
    commit = "a" * 40
    with pytest.raises(WeaverError):
        repo.read_blob("unresolved", "a.py", 100)
    with pytest.raises(WeaverError):
        repo.entry_mode("unresolved", "a.py")

    monkeypatch.setattr(repo, "run", lambda *args, **kwargs: "200")
    assert repo.read_blob(commit, "a.py", 100) is None

    def nul_blob(arguments, **kwargs):
        return "2" if arguments[0] == "cat-file" else b"\x00x"

    monkeypatch.setattr(repo, "run", nul_blob)
    assert repo.read_blob(commit, "a.py", 100) is None

    def invalid_blob(arguments, **kwargs):
        return "1" if arguments[0] == "cat-file" else b"\xff"

    monkeypatch.setattr(repo, "run", invalid_blob)
    assert repo.read_blob(commit, "a.py", 100) is None

    def failed_show(arguments, **kwargs):
        if arguments[0] == "cat-file":
            return "1"
        raise WeaverError(ErrorCode.INVALID_REF, "safe", "retry")

    monkeypatch.setattr(repo, "run", failed_show)
    assert repo.read_blob(commit, "a.py", 100) is None

    monkeypatch.setattr(repo, "run", lambda *args, **kwargs: b"")
    assert repo.entry_mode(commit, "a.py") is None
    monkeypatch.setattr(repo, "run", lambda *args, **kwargs: b"invalid\x00")
    assert repo.entry_mode(commit, "a.py") is None
    monkeypatch.setattr(
        repo,
        "run",
        lambda *args, **kwargs: b"\xff blob aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\ta.py\x00",
    )
    assert repo.entry_mode(commit, "a.py") is None

    def failed_tree(*args, **kwargs):
        raise WeaverError(ErrorCode.INVALID_REF, "safe", "retry")

    monkeypatch.setattr(repo, "run", failed_tree)
    assert repo.entry_mode(commit, "a.py") is None
    monkeypatch.setattr(repo, "run", lambda *args, **kwargs: b"../bad\x00good.py\x00\xff\x00")
    assert repo.list_files(commit) == ["good.py"]


def test_malformed_and_truncated_numstat_records_are_ignored() -> None:
    assert _parse_numstat(b"malformed\x00") == ({}, 0)
    assert _parse_numstat(b"1\t1\t\x00") == ({}, 0)


def test_copy_name_status_preserves_both_nul_delimited_paths() -> None:
    copied = _parse_name_status(b"C100\x00src/original.py\x00src/copied.py\x00")
    assert len(copied) == 1
    assert copied[0].status == "C100"
    assert copied[0].old_path == "src/original.py"
    assert copied[0].new_path == "src/copied.py"


def test_nul_delimited_metadata_preserves_newline_filename() -> None:
    path = "src/line\nbreak.py"
    changed = _parse_name_status(b"M\x00" + path.encode("utf-8") + b"\x00")
    assert len(changed) == 1
    assert changed[0].old_path == path
    assert changed[0].new_path == path
    assert _parse_numstat(b"1\t1\t" + path.encode("utf-8") + b"\x00") == (
        {path: (1, 1, False)},
        2,
    )


def test_critical_prioritization_requires_a_match_and_respects_line_budget(
    repo_factory,
) -> None:
    repo_path, base, head = repo_factory(
        {"critical.py": "x = 1\n", "other.py": "y = 1\n"},
        {"critical.py": "x = 2\n", "other.py": "y = 2\n"},
    )
    repo = GitRepository.open(str(repo_path))
    no_match = WeaverConfig(critical_paths=[CriticalPath(pattern="missing.py", weight=100)])
    no_match.rules.max_changed_files = 1
    with pytest.raises(WeaverError):
        collect_diff(repo, base, head, no_match)

    line_budget = WeaverConfig(critical_paths=[CriticalPath(pattern="critical.py", weight=100)])
    line_budget.rules.max_changed_files = 2
    line_budget.rules.max_diff_lines = 2
    result = collect_diff(repo, base, head, line_budget)
    assert [item.path for item in result.files] == ["critical.py"]


def test_unsupported_and_oversized_sources_are_explicitly_excluded(repo_factory) -> None:
    repo_path, base, head = repo_factory({"README.md": "old\n"}, {"README.md": "new\n"})
    config = WeaverConfig()
    config.paths.include = ["**/*"]
    unsupported = collect_diff(GitRepository.open(str(repo_path)), base, head, config)
    assert unsupported.excluded_counts["unsupported_extension"] == 1

    large = "value = '" + "x" * 2000 + "'\n"
    large_repo, large_base, large_head = repo_factory(
        {"large.py": large.replace("x", "y")}, {"large.py": large}
    )
    size_config = WeaverConfig()
    size_config.rules.max_file_bytes = 1024
    oversized = collect_diff(
        GitRepository.open(str(large_repo)), large_base, large_head, size_config
    )
    assert oversized.files == []
    assert oversized.excluded_counts["oversized_or_non_utf8"] == 1


def test_resource_limits_are_applied_after_include_filtering(repo_factory) -> None:
    repo_path, base, head = repo_factory(
        {"keep.py": "x = 1\n", "ignored.txt": "old\n"},
        {"keep.py": "x = 2\n", "ignored.txt": "new\n"},
    )
    config = WeaverConfig()
    config.paths.include = ["keep.py"]
    config.rules.max_changed_files = 1
    result = collect_diff(GitRepository.open(str(repo_path)), base, head, config)
    assert [item.path for item in result.files] == ["keep.py"]
    assert result.truncated is False


def test_git_attributes_cannot_hide_utf8_python_as_binary(repo_factory) -> None:
    attributes = "*.py binary\n"
    repo_path, base, head = repo_factory(
        {".gitattributes": attributes, "visible.py": "value = 1\n"},
        {".gitattributes": attributes, "visible.py": "value = 2\n"},
    )
    result = collect_diff(GitRepository.open(str(repo_path)), base, head, WeaverConfig())
    assert [item.path for item in result.files] == ["visible.py"]
    assert result.files[0].hunks


def test_aggregate_source_cap_is_immutable_and_visible(repo_factory, monkeypatch) -> None:
    repo_path, base, head = repo_factory(
        {"bounded.py": "value = 1\n"},
        {"bounded.py": "value = 2\n"},
    )
    monkeypatch.setattr(limits_module, "MAX_SOURCE_BLOB_BYTES", 12)
    result = collect_diff(GitRepository.open(str(repo_path)), base, head, WeaverConfig())
    assert result.files == []
    assert result.truncated is True
    assert result.excluded_counts["aggregate_source_limit"] == 1
    assert result.omitted_counts["aggregate_source_limit"] == 1


def test_batched_hunk_split_keys_paths_and_ignores_content_lines() -> None:
    """A ``--unified=0`` content line may look like a header; only real headers may count."""
    output = (
        "diff --git a/src/one.py b/src/one.py\n"
        "index 111..222 100644\n"
        "--- a/src/one.py\n"
        "+++ b/src/one.py\n"
        "@@ -3 +3,2 @@ def one():\n"
        "-    return 1\n"
        "+++ b/src/spoofed.py\n"
        "+@@ -9 +9 @@\n"
        "--- a/src/spoofed.py\n"
        "@@ -20,0 +22,3 @@\n"
        "+added\n"
        "diff --git a/src/two.py b/src/two.py\n"
        "--- a/src/two.py\n"
        "+++ b/src/two.py\n"
        "@@ -1 +1 @@\n"
        "-x\n"
        "+y\n"
    )
    requested = frozenset({"src/one.py", "src/two.py", "src/spoofed.py"})
    hunks = _parse_hunks_by_path(output, requested)
    assert set(hunks) == {"src/one.py", "src/two.py"}
    assert [(item.old_start, item.new_start, item.new_count) for item in hunks["src/one.py"]] == [
        (3, 3, 2),
        (20, 22, 3),
    ]
    assert [item.id for item in hunks["src/one.py"]] == ["hunk-001", "hunk-002"]
    assert [item.id for item in hunks["src/two.py"]] == ["hunk-001"]


def test_batched_hunk_split_decodes_quoted_paths_and_skips_unrequested() -> None:
    output = (
        'diff --git "a/src/we\\"ird.py" "b/src/we\\"ird.py"\n'
        '--- "a/src/we\\"ird.py"\n'
        '+++ "b/src/we\\"ird.py"\n'
        "@@ -1 +1 @@\n"
        "diff --git a/src/elsewhere.py b/src/elsewhere.py\n"
        "--- a/src/elsewhere.py\n"
        "+++ b/src/elsewhere.py\n"
        "@@ -1 +1 @@\n"
    )
    hunks = _parse_hunks_by_path(output, frozenset({'src/we"ird.py'}))
    assert set(hunks) == {'src/we"ird.py'}
    assert _unquote_git_path(r'"a/caf\303\251.py"') == "a/café.py"
    assert _unquote_git_path("a/plain.py") == "a/plain.py"


def test_batched_hunks_match_per_file_hunks(repo_factory) -> None:
    """The batched reader must agree with the single-file reader it replaced."""
    old = {
        f"src/mod_{index}.py": f"def f_{index}(x):\n    return x < {index}\n" for index in range(6)
    }
    new = {
        f"src/mod_{index}.py": f"def f_{index}(x):\n    return x <= {index}\n" for index in range(6)
    }
    repo_path, base, head = repo_factory(old, new)
    repo = GitRepository.open(str(repo_path))
    paths = sorted(old)
    batched = _hunks_by_path(repo, base, head, paths)
    assert set(batched) == set(paths)
    for path in paths:
        assert batched[path] == _file_hunks(repo, base, head, path)


def test_missing_batch_entry_falls_back_to_the_per_file_reader(repo_factory, monkeypatch) -> None:
    repo_path, base, head = repo_factory(
        {"src/api.py": "def allowed(x):\n    return x < 5\n"},
        {"src/api.py": "def allowed(x):\n    return x <= 5\n"},
    )
    monkeypatch.setattr(collect_module, "_hunks_by_path", lambda *a, **k: {})
    repo = GitRepository.open(str(repo_path))
    collection = collect_diff(repo, repo.resolve_ref(base), repo.resolve_ref(head), WeaverConfig())
    assert [item.path for item in collection.files] == ["src/api.py"]
    assert collection.files[0].hunks, "the fallback must still supply hunks"
