from __future__ import annotations

from pathlib import Path

import pytest

from scripts import check_action_pins

WORKFLOW_TEMPLATE = """\
name: ci

on:
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@{checkout} # {checkout_version}
      - run: python -m pytest
"""

DOC_TEMPLATE = """\
# Usage

Copy this into `.github/workflows/review.yml`:

```yaml
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@{checkout} # {checkout_version}
      - uses: sergiparpal/semantic-diff-weaver@{own}
```
"""

CHECKOUT_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
OTHER_SHA = "9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0"


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _repository(
    root: Path,
    *,
    workflow_sha: str = CHECKOUT_SHA,
    workflow_version: str = "v7.0.1",
    doc_sha: str = CHECKOUT_SHA,
    doc_version: str = "v7.0.1",
    own: str = "v0.2.0",
) -> Path:
    _write(
        root,
        ".github/workflows/ci.yml",
        WORKFLOW_TEMPLATE.format(checkout=workflow_sha, checkout_version=workflow_version),
    )
    _write(
        root,
        "docs/github-action.md",
        DOC_TEMPLATE.format(checkout=doc_sha, checkout_version=doc_version, own=own),
    )
    return root


def test_matching_workflow_and_documentation_pins_pass(tmp_path: Path) -> None:
    _repository(tmp_path)

    assert check_action_pins.main(["--root", str(tmp_path)]) == 0


def test_documentation_left_behind_by_a_bump_is_reported(tmp_path: Path, capsys) -> None:
    """The regression this script exists for: Dependabot moves the workflow, docs stay put."""
    _repository(tmp_path, doc_sha=OTHER_SHA, doc_version="v7.0.0")

    assert check_action_pins.main(["--root", str(tmp_path)]) == 1

    error = capsys.readouterr().err
    assert "actions/checkout is pinned inconsistently" in error
    assert ".github/workflows/ci.yml:10" in error
    assert "docs/github-action.md:10" in error


def test_a_third_party_tag_reference_is_rejected(tmp_path: Path, capsys) -> None:
    _repository(tmp_path, workflow_sha="v7", workflow_version="v7.0.1")

    assert check_action_pins.main(["--root", str(tmp_path)]) == 1
    assert "must be pinned to a full 40-character commit SHA" in capsys.readouterr().err


def test_a_missing_version_comment_is_rejected(tmp_path: Path, capsys) -> None:
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        f"jobs:\n  test:\n    steps:\n      - uses: actions/checkout@{CHECKOUT_SHA}\n",
    )

    assert check_action_pins.main(["--root", str(tmp_path)]) == 1
    assert "missing the trailing '# vX.Y.Z' comment" in capsys.readouterr().err


def test_an_unreadable_version_comment_is_rejected(tmp_path: Path, capsys) -> None:
    _repository(tmp_path, workflow_version="latest", doc_version="latest")

    assert check_action_pins.main(["--root", str(tmp_path)]) == 1
    assert "unreadable version comment" in capsys.readouterr().err


def test_the_project_action_must_use_a_release_tag(tmp_path: Path, capsys) -> None:
    _repository(tmp_path, own=CHECKOUT_SHA)

    assert check_action_pins.main(["--root", str(tmp_path)]) == 1
    assert "must be referenced by an immutable release tag" in capsys.readouterr().err


def test_the_project_action_tag_must_agree_across_examples(tmp_path: Path, capsys) -> None:
    _repository(tmp_path)
    _write(
        tmp_path,
        "README.md",
        DOC_TEMPLATE.format(checkout=CHECKOUT_SHA, checkout_version="v7.0.1", own="v0.1.0"),
    )

    assert check_action_pins.main(["--root", str(tmp_path)]) == 1
    assert "sergiparpal/semantic-diff-weaver is pinned inconsistently" in capsys.readouterr().err


@pytest.mark.parametrize("fence", ["", "bash", "text"])
def test_uses_outside_a_yaml_fence_is_not_a_pin(tmp_path: Path, fence: str) -> None:
    """`docs/release-checklist.md` describes the policy with a `@vX.Y.Z` placeholder."""
    _repository(tmp_path)
    _write(
        tmp_path,
        "docs/release-checklist.md",
        "- [x] Bump the `uses: sergiparpal/semantic-diff-weaver@vX.Y.Z` examples.\n"
        f"\n```{fence}\n      - uses: actions/checkout@v7\n```\n",
    )

    assert check_action_pins.main(["--root", str(tmp_path)]) == 0


def test_local_and_docker_references_are_skipped(tmp_path: Path) -> None:
    _repository(tmp_path)
    _write(
        tmp_path,
        ".github/workflows/pr-review.yml",
        "jobs:\n  review:\n    steps:\n      - uses: ./\n      - uses: docker://alpine:3.20\n",
    )

    assert check_action_pins.main(["--root", str(tmp_path)]) == 0


def test_an_empty_scan_fails_loudly(tmp_path: Path, capsys) -> None:
    """A silent pass would be indistinguishable from the globs having gone stale."""
    assert check_action_pins.main(["--root", str(tmp_path)]) == 2
    assert "scan patterns are stale" in capsys.readouterr().err


def test_a_version_comment_that_names_the_wrong_commit_is_caught(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    _repository(tmp_path)
    monkeypatch.setattr(
        check_action_pins,
        "_request",
        lambda url, token: {"object": {"type": "commit", "sha": OTHER_SHA}},
    )

    assert check_action_pins.main(["--root", str(tmp_path), "--verify-remote"]) == 1

    error = capsys.readouterr().err
    assert "claims v7.0.1 but that tag points at" in error


def test_annotated_tags_are_dereferenced_to_their_commit(tmp_path: Path, monkeypatch) -> None:
    _repository(tmp_path)
    responses = {
        "git/ref/tags/v7.0.1": {"object": {"type": "tag", "sha": "annotated"}},
        "git/tags/annotated": {"object": {"sha": CHECKOUT_SHA}},
        "git/ref/tags/v0.2.0": {"object": {"type": "commit", "sha": OTHER_SHA}},
    }

    def _fake(url: str, token: str | None) -> dict:
        for suffix, payload in responses.items():
            if url.endswith(suffix):
                return payload
        raise AssertionError(f"unexpected request: {url}")

    monkeypatch.setattr(check_action_pins, "_request", _fake)

    assert check_action_pins.main(["--root", str(tmp_path), "--verify-remote"]) == 0


def test_an_unpublished_project_tag_is_reported(tmp_path: Path, capsys, monkeypatch) -> None:
    """A doc-only tag can advertise a release that was never pushed."""
    _repository(tmp_path)

    def _fake(url: str, token: str | None) -> dict:
        if url.endswith("git/ref/tags/v0.2.0"):
            raise check_action_pins.urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]
        return {"object": {"type": "commit", "sha": CHECKOUT_SHA}}

    monkeypatch.setattr(check_action_pins, "_request", _fake)

    assert check_action_pins.main(["--root", str(tmp_path), "--verify-remote"]) == 1
    assert "does not resolve to a published tag" in capsys.readouterr().err


def test_an_outdated_pin_is_reported_only_by_the_freshness_check(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    _repository(tmp_path)
    monkeypatch.setattr(
        check_action_pins,
        "_request",
        lambda url, token: {"tag_name": "v9.9.9"},
    )

    assert check_action_pins.main(["--root", str(tmp_path)]) == 0
    assert check_action_pins.main(["--root", str(tmp_path), "--check-latest"]) == 1
    assert "is pinned to v7.0.1 but v9.9.9 is available" in capsys.readouterr().err


def test_the_request_helper_refuses_a_non_github_host() -> None:
    with pytest.raises(ValueError, match="non-GitHub URL"):
        check_action_pins._request("https://example.invalid/repos/a/b", None)


def test_this_repository_satisfies_its_own_policy() -> None:
    """Guards the real tree, so a drifting pin fails here as well as in the workflow."""
    assert check_action_pins.main([]) == 0
