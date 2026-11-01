"""Bounded collection of committed Python diffs."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from ..errors import ErrorCode, WeaverError
from ..models import WeaverConfig
from ..path_policy import (
    critical_weight,
    first_exclusion,
    glob_matches,
    in_test_root,
    is_included,
    is_test_support_path,
)
from ..source import SourceHunk
from . import limits
from .parse import parse_hunks, parse_hunks_by_path, parse_name_status, parse_numstat
from .repository import GitRepository
from .types import ChangedFile, DiffCollection


def _git_error(code: ErrorCode, message: str, remediation: str) -> WeaverError:
    return WeaverError(code, message, remediation)


_HUNK_DIFF_ARGUMENTS = (
    "diff",
    "--no-ext-diff",
    "--no-textconv",
    "--no-color",
    "--text",
    "--unified=0",
    # Single-path diffs can never pair a rename, so batching must not start finding them.
    # Accepted cost: collection detects renames with RENAME_DETECTION_ARGUMENTS, but this
    # stage does not, so a rename's new path diffs as a whole-file addition. Its symbols then
    # all anchor to one file-sized hunk instead of the lines that actually moved. Scope and
    # classification are unaffected; only hunk-level line attribution degrades for renames.
    "--no-renames",
)


def _file_hunks(repo: GitRepository, base: str, head: str, path: str) -> list[SourceHunk]:
    output = repo.run(
        [*_HUNK_DIFF_ARGUMENTS, base, head, "--", f":(literal){path}"],
        max_bytes=4 * 1024 * 1024,
    )
    return parse_hunks(output)


def _hunks_by_path(
    repo: GitRepository, base: str, head: str, paths: list[str]
) -> dict[str, list[SourceHunk]]:
    """Read every file's hunks with one bounded command per path chunk instead of per file."""
    result: dict[str, list[SourceHunk]] = {}
    ordered = sorted(set(paths))
    for start in range(0, len(ordered), limits.MAX_TREE_PATHS_PER_COMMAND):
        chunk = ordered[start : start + limits.MAX_TREE_PATHS_PER_COMMAND]
        try:
            output = repo.run(
                [
                    *_HUNK_DIFF_ARGUMENTS,
                    base,
                    head,
                    "--",
                    *(f":(literal){path}" for path in chunk),
                ],
                max_bytes=limits.MAX_GIT_OUTPUT_BYTES,
            )
        except WeaverError:
            # An oversized or failed chunk degrades to the per-file reader below.
            continue
        result.update(parse_hunks_by_path(output, frozenset(chunk)))
    return result


def _resource_selection(
    files: list[ChangedFile],
    stats: dict[str, tuple[int, int, bool]],
    changed_lines: int,
    config: WeaverConfig,
) -> tuple[set[str] | None, dict[str, int], list[str]]:
    """Prioritize bounded critical-path scope after mandatory and user filtering."""
    file_limit = min(config.rules.max_changed_files, limits.MAX_ANALYZED_FILES)
    files_exceeded = len(files) > file_limit
    lines_exceeded = changed_lines > config.rules.max_diff_lines
    if not files_exceeded and not lines_exceeded:
        return None, {}, []
    if not config.critical_paths:
        if files_exceeded:
            raise _git_error(
                ErrorCode.DIFF_TOO_LARGE,
                f"The diff contains {len(files)} changed files; the configured limit is "
                f"{file_limit}.",
                "Narrow the include patterns, split the change, or increase "
                "rules.max_changed_files.",
            )
        raise _git_error(
            ErrorCode.DIFF_TOO_LARGE,
            f"The diff contains {changed_lines} changed lines; the configured limit is "
            f"{config.rules.max_diff_lines}.",
            "Narrow the include patterns, split the change, or increase rules.max_diff_lines.",
        )
    ranked = sorted(
        files,
        key=lambda changed: (
            -critical_weight(changed.path, config.critical_paths),
            -sum(stats.get(changed.path, (0, 0, False))[:2]),
            changed.path,
        ),
    )
    if not ranked or critical_weight(ranked[0].path, config.critical_paths) == 0:
        raise _git_error(
            ErrorCode.DIFF_TOO_LARGE,
            "The diff exceeds configured limits and no bounded critical-path scope can be selected.",
            "Narrow the include patterns, split the change, or configure a matching critical path.",
        )
    selected: set[str] = set()
    selected_lines = 0
    for changed in ranked:
        if len(selected) >= file_limit:
            break
        additions, deletions, _ = stats.get(changed.path, (0, 0, False))
        file_lines = additions + deletions
        if file_lines > config.rules.max_diff_lines and critical_weight(
            changed.path, config.critical_paths
        ):
            raise _git_error(
                ErrorCode.DIFF_TOO_LARGE,
                "A prioritized critical-path file exceeds the configured changed-line limit.",
                "Split the critical-path change or increase rules.max_diff_lines.",
            )
        if selected_lines + file_lines > config.rules.max_diff_lines:
            continue
        selected.add(changed.path)
        selected_lines += file_lines
    if not selected:
        raise _git_error(
            ErrorCode.DIFF_TOO_LARGE,
            "The diff exceeds configured limits and no file fits the bounded analysis scope.",
            "Narrow the include patterns or split the change.",
        )
    omitted = max(0, len(files) - len(selected))
    omitted_counts = {"resource_prioritization": omitted} if omitted else {}
    warnings = [
        "The diff exceeded global resource limits; analyzed prioritized critical-path scope only."
    ]
    return selected, omitted_counts, warnings


@dataclass
class _Reading:
    """What reading changed blobs leaves to report: exclusions, omissions, and truncation."""

    excluded: Counter[str]
    omitted_counts: dict[str, int]
    warnings: list[str]
    truncated: bool = False


def _read_texts(
    repo: GitRepository,
    base_commit: str,
    head_commit: str,
    changed_files: list[ChangedFile],
    max_file_bytes: int,
    max_total_bytes: int,
    reading: _Reading,
    *,
    documents: bool = False,
    named: frozenset[str] = frozenset(),
) -> list[ChangedFile]:
    """Attach each file's base and head text, leaving out what cannot be read as bounded text.

    A Python file that cannot be read leaves the analysis incomplete, and so does a configuration
    file the repository `named`, which was asked for: that one is `unreadable_config_file`. Any
    other YAML or JSON file is only `unreadable_document`: whether it was an API schema cannot be
    told unread, and most such files are lockfiles and data, which would otherwise keep every
    analysis touching one from a pass. Past the byte total, all of them are omitted scope.
    """
    base_entries = repo.tree_entries(
        base_commit,
        [
            item.old_path
            for item in changed_files
            if item.old_path and not item.status.startswith("C")
        ],
    )
    head_entries = repo.tree_entries(
        head_commit, [item.new_path for item in changed_files if item.new_path]
    )
    object_ids = {
        entry.object_id
        for entries in (base_entries, head_entries)
        for entry in entries.values()
        if entry.mode not in {"120000", "160000"}
    }
    blob_batch = repo.read_blob_batch(object_ids, max_file_bytes, max_total_bytes=max_total_bytes)
    read: list[ChangedFile] = []
    for changed in changed_files:
        is_copy = changed.status.startswith("C")
        old_entry = base_entries.get(changed.old_path) if changed.old_path and not is_copy else None
        new_entry = head_entries.get(changed.new_path) if changed.new_path else None
        modes = {entry.mode for entry in (old_entry, new_entry) if entry is not None}
        if modes & {"120000", "160000"}:
            if changed.path in named:
                reading.excluded["unreadable_config_file"] += 1
            elif documents:
                reading.excluded["unreadable_document"] += 1
            else:
                reading.excluded["symlink_or_gitlink"] += 1
            continue
        changed.old_text = blob_batch.texts.get(old_entry.object_id) if old_entry else None
        changed.new_text = blob_batch.texts.get(new_entry.object_id) if new_entry else None
        if (changed.old_path and not is_copy and changed.old_text is None) or (
            changed.new_path and changed.new_text is None
        ):
            failure_classes = {
                blob_batch.failures.get(entry.object_id, "missing_or_not_blob")
                for entry in (old_entry, new_entry)
                if entry is not None and blob_batch.texts.get(entry.object_id) is None
            }
            if documents and "aggregate_source_limit" in failure_classes:
                reading.omitted_counts["document_limit"] = (
                    reading.omitted_counts.get("document_limit", 0) + 1
                )
                reading.truncated = True
            elif changed.path in named:
                reading.excluded["unreadable_config_file"] += 1
            elif documents:
                reading.excluded["unreadable_document"] += 1
            elif "binary" in failure_classes:
                reading.excluded["binary"] += 1
            elif "aggregate_source_limit" in failure_classes:
                reading.excluded["aggregate_source_limit"] += 1
                reading.omitted_counts["aggregate_source_limit"] = (
                    reading.omitted_counts.get("aggregate_source_limit", 0) + 1
                )
                reading.truncated = True
            else:
                reading.excluded["oversized_or_non_utf8"] += 1
            reading.warnings.append(f"Skipped bounded source parsing for {changed.path!r}.")
            continue
        read.append(changed)
    return read


def _read_documents(
    repo: GitRepository,
    base_commit: str,
    head_commit: str,
    documents: list[ChangedFile],
    config_files: list[ChangedFile],
    config: WeaverConfig,
    reading: _Reading,
) -> tuple[list[ChangedFile], list[ChangedFile]]:
    """Read changed documents whole under budgets of their own: the YAML and JSON files that may
    be API schemas, and the configuration files the repository names, which come first.

    Whether a YAML or JSON file is an API schema is decided by its content, in `schema_diff`, so
    all of them are read; a file count and a byte total of their own keep a change to many
    manifests or a large lockfile from crowding out the Python files the line budget is for.
    Past the count, configuration files go first, as they were asked for, then critical paths.
    """
    named = frozenset(item.path for item in config_files)
    candidates = [*config_files, *documents]
    if len(candidates) > limits.MAX_DOCUMENTS:
        candidates = sorted(
            candidates,
            key=lambda item: (
                item.path not in named,
                -critical_weight(item.path, config.critical_paths, default=0),
                item.path,
            ),
        )
        reading.omitted_counts["document_limit"] = len(candidates) - limits.MAX_DOCUMENTS
        reading.truncated = True
        reading.warnings.append(
            f"Only {limits.MAX_DOCUMENTS} changed YAML, JSON, and TOML files were read, those "
            "named as configuration first."
        )
        candidates = candidates[: limits.MAX_DOCUMENTS]
    if not candidates:
        return [], []
    read = _read_texts(
        repo,
        base_commit,
        head_commit,
        candidates,
        config.rules.max_file_bytes,
        limits.MAX_DOCUMENT_BLOB_BYTES,
        reading,
        documents=True,
        named=named,
    )
    return (
        [item for item in read if item.path not in named],
        [item for item in read if item.path in named],
    )


def _named_config(path: str, config: WeaverConfig) -> bool:
    """Whether `paths.config_files` names a file. Python source stays Python, so `config/**`
    leaves a Django `config/settings.py` to the AST analysis."""
    return not path.endswith(".py") and any(
        glob_matches(path, pattern) for pattern in config.paths.config_files
    )


def collect_diff(
    repo: GitRepository,
    base_commit: str,
    head_commit: str,
    config: WeaverConfig,
) -> DiffCollection:
    """Collect bounded changed Python blobs, the YAML and JSON files an API schema can be
    written in, and the configuration files the repository names, without checking out or
    executing code."""
    name_raw = repo.run(
        [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-status",
            "-z",
            *limits.RENAME_DETECTION_ARGUMENTS,
            base_commit,
            head_commit,
            "--",
        ],
        binary=True,
    )
    files = parse_name_status(name_raw)
    numstat_raw = repo.run(
        [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--text",
            "--numstat",
            "-z",
            *limits.RENAME_DETECTION_ARGUMENTS,
            base_commit,
            head_commit,
            "--",
        ],
        binary=True,
    )
    stats, changed_lines = parse_numstat(numstat_raw)
    excluded: Counter[str] = Counter()
    candidates: list[ChangedFile] = []
    documents: list[ChangedFile] = []
    config_files: list[ChangedFile] = []
    warnings: list[str] = []
    for changed in files:
        reason = first_exclusion((changed.old_path, changed.new_path))
        if reason:
            excluded[reason] += 1
            continue
        path = changed.path
        named = _named_config(path, config)
        if named and not path.endswith(limits.CONFIG_SUFFIXES):
            excluded["unsupported_config_format"] += 1
            continue
        is_document = named or (
            config.rules.analyze_api_schemas and path.endswith(limits.DOCUMENT_SUFFIXES)
        )
        # The extension first: an include a repository configured for its code would otherwise
        # claim every file it does not name, and say so as a path filter.
        if not path.endswith(".py") and not is_document:
            excluded["unsupported_extension"] += 1
            continue
        # Also before the path filter, which the default include would pass: test code is
        # candidate-test material, not behavior under review, and the reason should say so.
        # Only what the path settles is left out here, before the line budget is spent on it;
        # a file merely named like a test module is settled by its source, in `service`. A
        # document under a test root is a fixture, whatever it holds.
        if not config.rules.analyze_test_files and (
            in_test_root(path, config.paths.test_roots)
            if is_document
            else is_test_support_path(path, config.paths.test_roots)
        ):
            excluded["test_file"] += 1
            continue
        # A configuration file the repository names is included by being named, whatever
        # `include` says, and `exclude` still leaves it out.
        if not (
            is_included(path, ["**"], config.paths.exclude)
            if named
            else is_included(path, config.paths.include, config.paths.exclude)
        ):
            excluded["path_filter"] += 1
            continue
        if named:
            config_files.append(changed)
            continue
        if is_document:
            documents.append(changed)
            continue
        additions, deletions, binary = stats.get(path, (0, 0, False))
        changed.additions = additions
        changed.deletions = deletions
        changed.binary = binary
        candidates.append(changed)

    eligible_changed_lines = sum(item.additions + item.deletions for item in candidates)
    selected_scope, omitted_counts, resource_warnings = _resource_selection(
        candidates, stats, eligible_changed_lines, config
    )
    warnings.extend(resource_warnings)
    if selected_scope is not None:
        candidates = [item for item in candidates if item.path in selected_scope]

    reading = _Reading(excluded, omitted_counts, warnings)
    selected = _read_texts(
        repo,
        base_commit,
        head_commit,
        candidates,
        config.rules.max_file_bytes,
        limits.MAX_SOURCE_BLOB_BYTES,
        reading,
    )
    for changed in selected:
        if changed.status.startswith("C"):
            changed.hunks = [
                SourceHunk(
                    id="hunk-001",
                    old_start=0,
                    old_count=0,
                    new_start=1,
                    new_count=max(1, len((changed.new_text or "").splitlines())),
                )
            ]

    pending = [item for item in selected if not item.status.startswith("C")]
    hunks_by_path = _hunks_by_path(repo, base_commit, head_commit, [item.path for item in pending])
    for changed in pending:
        hunks = hunks_by_path.get(changed.path)
        changed.hunks = (
            hunks
            if hunks is not None
            else _file_hunks(repo, base_commit, head_commit, changed.path)
        )
    read_documents, read_config_files = _read_documents(
        repo, base_commit, head_commit, documents, config_files, config, reading
    )
    return DiffCollection(
        files=selected,
        changed_files_total=len(files),
        changed_lines=changed_lines,
        excluded_counts=dict(sorted(excluded.items())),
        warnings=warnings,
        omitted_counts=omitted_counts,
        truncated=selected_scope is not None or reading.truncated,
        documents=read_documents,
        config_files=read_config_files,
    )
