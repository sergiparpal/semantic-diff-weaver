"""Bounded collection of committed Python diffs."""

from __future__ import annotations

from collections import Counter

from ..errors import ErrorCode, WeaverError
from ..models import WeaverConfig
from ..path_policy import critical_weight, first_exclusion, is_included
from . import limits
from .parse import parse_hunks, parse_name_status, parse_numstat
from .repository import GitRepository
from .types import ChangedFile, DiffCollection, Hunk


def _git_error(code: ErrorCode, message: str, remediation: str) -> WeaverError:
    return WeaverError(code, message, remediation)


def _file_hunks(repo: GitRepository, base: str, head: str, path: str) -> list[Hunk]:
    output = repo.run(
        [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-color",
            "--text",
            "--unified=0",
            base,
            head,
            "--",
            f":(literal){path}",
        ],
        max_bytes=4 * 1024 * 1024,
    )
    return parse_hunks(output)


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


def collect_diff(
    repo: GitRepository,
    base_commit: str,
    head_commit: str,
    config: WeaverConfig,
) -> DiffCollection:
    """Collect bounded changed Python blobs without checking out or executing code."""
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
    selected: list[ChangedFile] = []
    warnings: list[str] = []
    for changed in files:
        reason = first_exclusion((changed.old_path, changed.new_path))
        if reason:
            excluded[reason] += 1
            continue
        path = changed.path
        if not is_included(path, config.paths.include, config.paths.exclude):
            excluded["path_filter"] += 1
            continue
        if not path.endswith(".py"):
            excluded["unsupported_extension"] += 1
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

    base_entries = repo.tree_entries(
        base_commit,
        [item.old_path for item in candidates if item.old_path and not item.status.startswith("C")],
    )
    head_entries = repo.tree_entries(
        head_commit, [item.new_path for item in candidates if item.new_path]
    )
    object_ids = {
        entry.object_id
        for entries in (base_entries, head_entries)
        for entry in entries.values()
        if entry.mode not in {"120000", "160000"}
    }
    blob_batch = repo.read_blob_batch(object_ids, config.rules.max_file_bytes)
    source_truncated = False

    for changed in candidates:
        is_copy = changed.status.startswith("C")
        old_entry = base_entries.get(changed.old_path) if changed.old_path and not is_copy else None
        new_entry = head_entries.get(changed.new_path) if changed.new_path else None
        modes = {entry.mode for entry in (old_entry, new_entry) if entry is not None}
        if modes & {"120000", "160000"}:
            excluded["symlink_or_gitlink"] += 1
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
            if "binary" in failure_classes:
                excluded["binary"] += 1
            elif "aggregate_source_limit" in failure_classes:
                excluded["aggregate_source_limit"] += 1
                omitted_counts["aggregate_source_limit"] = (
                    omitted_counts.get("aggregate_source_limit", 0) + 1
                )
                source_truncated = True
            else:
                excluded["oversized_or_non_utf8"] += 1
            warnings.append(f"Skipped bounded source parsing for {changed.path!r}.")
            continue
        if is_copy:
            changed.hunks = [
                Hunk(
                    id="hunk-001",
                    old_start=0,
                    old_count=0,
                    new_start=1,
                    new_count=max(1, len((changed.new_text or "").splitlines())),
                )
            ]
        else:
            changed.hunks = _file_hunks(repo, base_commit, head_commit, changed.path)
        selected.append(changed)
    return DiffCollection(
        files=selected,
        changed_files_total=len(files),
        changed_lines=changed_lines,
        excluded_counts=dict(sorted(excluded.items())),
        warnings=warnings,
        omitted_counts=omitted_counts,
        truncated=selected_scope is not None or source_truncated,
    )
