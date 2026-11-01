"""Configuration files are untrusted repository content, and the ones a repository names hold its
secrets more often than any other file: values reach the output redacted, documents are read under
budgets and constructed into nothing, and a link is never followed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from semantic_diff_weaver.service import analyze
from tests.conftest import git

NAMED = "version: 1\npaths:\n  config_files: ['config/**', 'settings.toml']\n"
ESC, RLO = chr(0x1B), chr(0x202E)


def _envelope(repo_factory, old: dict[str, str], new: dict[str, str]) -> dict:
    repo, base, head = repo_factory(
        {".semantic-diff-weaver.yaml": NAMED, **old}, {".semantic-diff-weaver.yaml": NAMED, **new}
    )
    envelope = analyze({"repo_path": str(repo), "base_ref": base, "head_ref": head})
    assert envelope["success"] is True
    return envelope


SECRETS = {
    "password": "hunter2-correct-horse",
    "uri": "s3cr3t-in-a-dsn",
    "github": "ghp_" + "Q" * 36,
    "bearer": "abcDEF123456789xyz",
}


def test_secrets_a_configuration_file_holds_reach_no_output(repo_factory) -> None:
    old_yaml = (
        "database:\n"
        f"  password: {SECRETS['password']}\n"
        f"  dsn: postgres://app:{SECRETS['uri']}@db.internal/app\n"
        "http:\n"
        f"  authorization: Bearer {SECRETS['bearer']}\n"
    )
    new_yaml = "database:\n  password: other\n  dsn: postgres://db.internal/app\nhttp: {}\n"
    old_toml = f"[github]\ntoken = '{SECRETS['github']}'\n"
    new_toml = "[github]\ntoken = 'none'\n"
    envelope = _envelope(
        repo_factory,
        {"config/app.yaml": old_yaml, "settings.toml": old_toml},
        {"config/app.yaml": new_yaml, "settings.toml": new_toml},
    )
    symbols = {item["evidence"][0]["symbol"] for item in envelope["analysis"]["behavior_changes"]}
    assert {"database.password", "database.dsn", "http.authorization", "github.token"} <= symbols
    shown = json.dumps(envelope)
    assert "[REDACTED]" in shown
    for secret in SECRETS.values():
        assert secret not in shown


@pytest.mark.parametrize(
    ("name", "text"),
    [
        (
            "config/bomb.yaml",
            "l0: &l0 [lol, lol, lol, lol, lol, lol, lol, lol, lol]\n"
            + "".join(
                f"l{level}: &l{level} [{', '.join([f'*l{level - 1}'] * 9)}]\n"
                for level in range(1, 9)
            ),
        ),
        ("settings.toml", "a = " + "[" * 50_000 + "]" * 50_000 + "\n"),
        ("config/deep.json", "[" * 100_000 + "]" * 100_000),
        ("config/tagged.yaml", "hook: !!python/object/apply:os.getcwd []\n"),
    ],
    ids=["alias-bomb", "deep-toml", "deep-json", "python-tag"],
)
def test_a_hostile_configuration_file_is_left_unread_not_run(
    repo_factory, name: str, text: str
) -> None:
    empty = "{}" if name.endswith(".json") else "a = 1\n" if name.endswith(".toml") else "a: 1\n"
    analysis = _envelope(repo_factory, {name: empty}, {name: text})["analysis"]
    assert [item["evidence"][0]["symbol"] for item in analysis["behavior_changes"]] == [
        "<document>"
    ]
    assert {"reason": "parse_incomplete_config_files", "count": 1} in analysis["scope"]["omitted"]
    assert analysis["summary"]["verdict"] != "pass"


def test_a_symlinked_configuration_file_is_not_read_and_is_not_complete(tmp_path: Path) -> None:
    outside = tmp_path / "secret.yaml"
    outside.write_text("password: hunter2-correct-horse\n", encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    git(repo, "init", "-q")
    (repo / ".semantic-diff-weaver.yaml").write_text(NAMED, encoding="utf-8")
    link = repo / "config" / "app.yaml"
    try:
        link.symlink_to(Path("..") / ".." / "secret.yaml")
    except OSError:
        pytest.skip("symlink creation is unavailable")
    git(repo, "add", "--all")
    git(repo, "commit", "-q", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    link.unlink()
    link.symlink_to(Path("..") / ".." / "repo" / ".." / "secret.yaml")
    git(repo, "add", "--all")
    git(repo, "commit", "-q", "-m", "head")
    envelope = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": "HEAD", "output_format": "json"}
    )
    assert envelope["scope"]["excluded_counts"] == {"unreadable_config_file": 1}
    assert envelope["scope"]["truncated"] is True
    assert "hunter2" not in json.dumps(envelope)


def test_controls_and_markdown_in_a_key_are_shown_escaped(repo_factory) -> None:
    key = f"flags{ESC}[31m{RLO}**bold**[link](javascript:alert)````"
    envelope = _envelope(
        repo_factory,
        {"config/app.json": json.dumps({key: False})},
        {"config/app.json": json.dumps({key: True})},
    )
    (behavior,) = envelope["analysis"]["behavior_changes"]
    assert behavior["evidence"][0]["symbol"] == key
    markdown = envelope["markdown"]
    assert ESC not in markdown and RLO not in markdown
    # The key is a code span its own four backticks cannot close, with its controls made
    # visible, and the evidence's fence below stays whole.
    shown = key.replace(ESC, r"\u001b").replace(RLO, r"\u202e")
    (line,) = (line for line in markdown.splitlines() if "Evidence `ev-" in line)
    assert line.endswith(f"— ````` {shown} `````")
    fences = [item.strip() for item in markdown.splitlines() if item.lstrip().startswith("````")]
    assert fences == ["````text", "````"]
