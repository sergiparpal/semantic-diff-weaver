"""A coverage report is untrusted input, like every other artifact this tool reads.

A hostile report can name `../../etc/passwd`, absolute system paths, control characters, or a
secret-looking filename. None of it may escape the repository scope, reach output unredacted,
or cause a read outside the authorized roots. Nothing in the report is ever opened — entries
are lookup keys only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from semantic_diff_weaver.coverage_map import load_coverage
from semantic_diff_weaver.errors import ErrorCode, WeaverError
from semantic_diff_weaver.models import LineRange, WeaverConfig
from semantic_diff_weaver.path_policy import ALLOWED_ROOTS_ENV
from semantic_diff_weaver.plugin import handle_analyze_semantic_diff

HOSTILE_PATHS = [
    "../../etc/passwd",
    "../../../../../../etc/shadow",
    "/etc/passwd",
    "/root/.ssh/id_rsa",
    "C:\\Windows\\System32\\config\\SAM",
    "\\\\server\\share\\secret.py",
    "src/../../../outside.py",
    "src/\x01\x02control.py",
    ".git/config",
    ".env",
]


def _load(tmp_path: Path, files: dict[str, object]) -> object:
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps({"files": files}), encoding="utf-8")
    return load_coverage(path, WeaverConfig())


@pytest.mark.parametrize("hostile", HOSTILE_PATHS)
def test_a_hostile_entry_never_matches_a_repository_path(tmp_path: Path, hostile: str) -> None:
    coverage = _load(
        tmp_path, {hostile: {"executed_lines": [1]}, "src/api.py": {"missing_lines": [1]}}
    )
    # The genuine entry still resolves, and the hostile one cannot claim it.
    assert coverage.status_for("src/api.py", LineRange(start=1, end=1)) == "uncovered"


def test_traversal_is_stripped_rather_than_followed(tmp_path: Path) -> None:
    """Entries are lookup keys; the parser never opens or resolves them."""
    coverage = _load(tmp_path, {"../../etc/passwd": {"executed_lines": [1]}})
    assert coverage.status_for("etc/passwd", LineRange(start=1, end=1)) == "covered"
    # Crucially, the traversal prefix is gone from the key rather than acted on.
    assert all(".." not in key for key in coverage.files)


def test_absolute_entries_become_relative_lookup_keys(tmp_path: Path) -> None:
    coverage = _load(tmp_path, {"/etc/passwd": {"executed_lines": [1]}})
    assert all(not key.startswith("/") for key in coverage.files)


def test_a_null_byte_entry_is_discarded(tmp_path: Path) -> None:
    coverage = _load(tmp_path, {"src/ok.py": {"executed_lines": [1]}, "src/a\x00b.py": {}})
    assert "src/ok.py" in coverage.files
    assert all("\x00" not in key for key in coverage.files)


def test_a_report_outside_the_authorized_roots_is_refused(
    repo_factory, tmp_path, monkeypatch
) -> None:
    repo, base, head = repo_factory(
        {"api.py": "def allowed(x):\n    return x < 5\n"},
        {"api.py": "def allowed(x):\n    return x <= 5\n"},
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    report = outside / "coverage.json"
    report.write_text(json.dumps({"files": {"api.py": {"missing_lines": [2]}}}), encoding="utf-8")
    monkeypatch.setenv(ALLOWED_ROOTS_ENV, str(repo))
    payload = json.loads(
        handle_analyze_semantic_diff(
            {
                "repo_path": str(repo),
                "base_ref": base,
                "head_ref": head,
                "coverage_report": str(report),
            }
        )
    )
    assert payload["success"] is False
    assert payload["error"] == "path_outside_repository"


def test_report_contents_never_reach_output_unredacted(repo_factory, monkeypatch) -> None:
    """A hostile filename in the report must not be echoed into the brief."""
    marker = "sk-live-COVERAGE-PATH-LEAK-0123456789"
    repo, base, head = repo_factory(
        {"api.py": "def allowed(x):\n    return x < 5\n"},
        {"api.py": "def allowed(x):\n    return x <= 5\n"},
    )
    report = Path(repo) / "coverage.json"
    report.write_text(
        json.dumps(
            {
                "files": {
                    "api.py": {"executed_lines": [], "missing_lines": [1, 2]},
                    f"../../{marker}/leak.py": {"executed_lines": [1]},
                    "\u202eevil.py": {"executed_lines": [1]},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(ALLOWED_ROOTS_ENV, str(repo))
    rendered = handle_analyze_semantic_diff(
        {
            "repo_path": str(repo),
            "base_ref": base,
            "head_ref": head,
            "output_format": "both",
            "coverage_report": str(report),
        }
    )
    assert marker not in rendered
    assert "\u202e" not in rendered
    payload = json.loads(rendered)
    assert payload["analysis"]["success"] is True
    # The absolute report path is host configuration and must not be echoed either.
    assert str(report) not in rendered


def test_an_unreadable_report_fails_closed_with_a_stable_code(repo_factory, monkeypatch) -> None:
    """A reviewer who asked for grounded coverage is never handed ungrounded output instead."""
    repo, base, head = repo_factory(
        {"api.py": "def allowed(x):\n    return x < 5\n"},
        {"api.py": "def allowed(x):\n    return x <= 5\n"},
    )
    report = Path(repo) / "coverage.json"
    report.write_text("<?xml version='1.0'?><coverage/>", encoding="utf-8")
    monkeypatch.setenv(ALLOWED_ROOTS_ENV, str(repo))
    payload = json.loads(
        handle_analyze_semantic_diff(
            {
                "repo_path": str(repo),
                "base_ref": base,
                "head_ref": head,
                "coverage_report": str(report),
            }
        )
    )
    assert payload["success"] is False
    assert payload["error"] == ErrorCode.COVERAGE_UNREADABLE.value


def test_a_billion_laughs_payload_is_refused_not_expanded(tmp_path: Path) -> None:
    """XML is excluded on purpose; an entity-expansion payload is simply unparseable input."""
    payload = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
        "<coverage>&lol2;</coverage>"
    )
    path = tmp_path / "cobertura.xml"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(WeaverError) as raised:
        load_coverage(path, WeaverConfig())
    assert raised.value.code is ErrorCode.COVERAGE_UNREADABLE


def test_a_symlinked_report_out_of_the_repository_is_refused(
    repo_factory, tmp_path, monkeypatch
) -> None:
    repo, base, head = repo_factory(
        {"api.py": "def allowed(x):\n    return x < 5\n"},
        {"api.py": "def allowed(x):\n    return x <= 5\n"},
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "coverage.json"
    target.write_text(json.dumps({"files": {"api.py": {"missing_lines": [2]}}}), encoding="utf-8")
    link = Path(repo) / "coverage.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):  # pragma: no cover - unprivileged Windows
        pytest.skip("symlink creation is unavailable on this platform")
    monkeypatch.setenv(ALLOWED_ROOTS_ENV, str(repo))
    payload = json.loads(
        handle_analyze_semantic_diff(
            {
                "repo_path": str(repo),
                "base_ref": base,
                "head_ref": head,
                "coverage_report": str(link),
            }
        )
    )
    assert payload["success"] is False
    assert payload["error"] == "path_outside_repository"


def test_no_analyzed_repository_content_is_executed(repo_factory, monkeypatch) -> None:
    """The no-execute invariant holds: ingesting a report runs nothing."""
    repo, base, head = repo_factory(
        {"api.py": "def allowed(x):\n    return x < 5\n"},
        {
            "api.py": "def allowed(x):\n    return x <= 5\n",
            "conftest.py": "raise SystemExit('analyzed code must never run')\n",
        },
    )
    report = Path(repo) / "coverage.json"
    report.write_text(json.dumps({"files": {"api.py": {"missing_lines": [2]}}}), encoding="utf-8")
    monkeypatch.setenv(ALLOWED_ROOTS_ENV, str(repo))
    payload = json.loads(
        handle_analyze_semantic_diff(
            {
                "repo_path": str(repo),
                "base_ref": base,
                "head_ref": head,
                "coverage_report": str(report),
            }
        )
    )
    assert payload["success"] is True
