"""API schemas are untrusted repository content: read under budgets, never constructed into
objects or followed anywhere they point, and redacted and escaped wherever they are shown."""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path

import pytest

from semantic_diff_weaver.schema_diff import analyze_schemas
from semantic_diff_weaver.service import analyze
from semantic_diff_weaver.source import SourceRevisionPair
from tests.conftest import git

HEAD = "openapi: 3.0.3\ninfo: {title: Shop, version: '1'}\n"
SIMPLE = HEAD + "paths:\n  /orders:\n    get: {responses: {'200': {description: ok}}}\n"


def _envelope(repo_factory, old: dict[str, str], new: dict[str, str]) -> dict:
    repo, base, head = repo_factory(old, new)
    envelope = analyze({"repo_path": str(repo), "base_ref": base, "head_ref": head})
    assert envelope["success"] is True
    return envelope


def _parse_incomplete(envelope: dict) -> bool:
    analysis = envelope["analysis"]
    return (
        [item["evidence"][0]["symbol"] for item in analysis["behavior_changes"]] == ["<document>"]
        and {"reason": "parse_incomplete_documents", "count": 1} in analysis["scope"]["omitted"]
        and analysis["summary"]["verdict"] != "pass"
    )


def test_an_alias_bomb_is_a_schema_left_unread_not_a_hang(repo_factory) -> None:
    """Nine laughs a level, nine levels: 72 aliases, which the alias budget allows, stand for
    nine to the ninth nodes, which the walk after loading refuses at its node budget."""
    laughs = "".join(
        f"    l{level}: &l{level} [{', '.join([f'*l{level - 1}'] * 9)}]\n" for level in range(1, 9)
    )
    bomb = (
        HEAD + "paths: {}\ncomponents:\n  schemas:\n"
        "    l0: &l0 [lol, lol, lol, lol, lol, lol, lol, lol, lol]\n" + laughs
    )
    assert _parse_incomplete(_envelope(repo_factory, {"api.yaml": SIMPLE}, {"api.yaml": bomb}))


@pytest.mark.parametrize(
    ("name", "text"),
    [
        (
            "api.json",
            '{"openapi": "3.0.3", "paths": {}, "x": ' + "[" * 100_000 + "]" * 100_000 + "}",
        ),
        ("api.yaml", HEAD + "paths: {}\nx: " + "[" * 10_000 + "]" * 10_000 + "\n"),
    ],
    ids=["json", "yaml"],
)
def test_deep_nesting_is_a_schema_left_unread_not_a_crash(
    repo_factory, name: str, text: str
) -> None:
    simple = json.dumps({"openapi": "3.0.3", "paths": {}}) if name.endswith(".json") else SIMPLE
    assert _parse_incomplete(_envelope(repo_factory, {name: simple}, {name: text}))


def test_a_python_tag_constructs_nothing(repo_factory, tmp_path: Path) -> None:
    marker = tmp_path / "constructed"
    tagged = SIMPLE + f"x-hook: !!python/object/apply:builtins.open [{str(marker)!r}, 'w']\n"
    envelope = _envelope(repo_factory, {"api.yaml": SIMPLE}, {"api.yaml": tagged})
    assert not marker.exists()
    assert _parse_incomplete(envelope)


SECRETS = {
    "token": "ghp_" + "Q" * 36,
    "key": "sk-" + "Z" * 32,
    "password": "hunter2-correct-horse",
    "bearer": "abcDEF123456789xyz",
}
CREDENTIALED = """openapi: 3.0.3
info: {{title: Shop, version: '1'}}
servers:
  - url: {server}
paths:
  /orders:
    get:
      parameters:
        - name: Authorization
          in: header
          schema: {{type: string, default: "{authorization}"}}
      responses: {{'200': {{description: ok}}}}
components:
  schemas:
    Settings:
      type: object
      example: {{token: "{github}"}}
      properties:
        token: {{type: string, default: "{github}"}}
        key: {{type: string, enum: ["{openai}"]}}
"""


def test_credentials_a_schema_holds_reach_no_output(repo_factory) -> None:
    old = CREDENTIALED.format(
        server=f"https://deploy:{SECRETS['password']}@api.example/v1",
        authorization=f"Bearer {SECRETS['bearer']}",
        github=SECRETS["token"],
        openai=SECRETS["key"],
    )
    new = CREDENTIALED.format(
        server="https://api.example/v2", authorization="none", github="none", openai="none"
    )
    envelope = _envelope(repo_factory, {"api.yaml": old}, {"api.yaml": new})
    kinds = {item["evidence"][0]["kind"] for item in envelope["analysis"]["behavior_changes"]}
    assert {"api_server_change", "schema_default_change", "schema_enum_change"} <= kinds
    shown = json.dumps(envelope)
    assert "[REDACTED]" in shown
    for secret in SECRETS.values():
        assert secret not in shown


def test_a_ref_that_names_a_url_is_never_fetched(repo_factory, monkeypatch) -> None:
    def refused(*args, **kwargs):
        raise AssertionError("an API schema made the analysis open a connection")

    monkeypatch.setattr(socket, "create_connection", refused)
    monkeypatch.setattr(socket, "getaddrinfo", refused)
    monkeypatch.setattr(socket.socket, "connect", refused)
    schema = HEAD + "paths: {}\ncomponents:\n  schemas:\n    Order: {$ref: '%s'}\n"
    envelope = _envelope(
        repo_factory,
        {"api.yaml": schema % "http://127.0.0.1:9/old.yaml#/Order"},
        {"api.yaml": schema % "https://attacker.invalid/new.yaml#/Order"},
    )
    (behavior,) = envelope["analysis"]["behavior_changes"]
    assert behavior["evidence"][0]["kind"] == "schema_ref_change"


def test_a_symlinked_document_is_not_read_and_costs_no_completeness(tmp_path: Path) -> None:
    outside = tmp_path / "secret.yaml"
    outside.write_text(SIMPLE + "x-secret: hunter2-correct-horse\n", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "api.py").write_text("def allowed(x):\n    return x < 5\n", encoding="utf-8")
    link = repo / "openapi.yaml"
    try:
        link.symlink_to(Path("..") / "secret.yaml")
    except OSError:
        pytest.skip("symlink creation is unavailable")
    git(repo, "add", "--all")
    git(repo, "commit", "-q", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    link.unlink()
    link.symlink_to(Path("..") / "repo" / ".." / "secret.yaml")
    git(repo, "add", "--all")
    git(repo, "commit", "-q", "-m", "head")
    envelope = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": "HEAD", "output_format": "json"}
    )
    scope = envelope["scope"]
    assert scope["excluded_counts"] == {"unreadable_document": 1}
    assert scope["truncated"] is False and scope["analyzed_files"] == []
    assert envelope["summary"]["verdict"] == "pass"
    assert "hunter2" not in json.dumps(envelope)


def test_controls_and_markdown_in_a_route_are_shown_escaped(repo_factory) -> None:
    route = "/orders/\u001b[31m\u202e**bold**[link](javascript:alert)````"
    old = json.dumps(
        {"openapi": "3.0.3", "paths": {route: {"get": {"responses": {"200": {}}}}}}, indent=2
    )
    new = json.dumps({"openapi": "3.0.3", "paths": {}}, indent=2)
    envelope = _envelope(repo_factory, {"api.json": old}, {"api.json": new})
    (behavior,) = envelope["analysis"]["behavior_changes"]
    assert behavior["evidence"][0]["symbol"] == f"GET {route}"
    markdown = envelope["markdown"]
    assert "\u001b" not in markdown and "\u202e" not in markdown
    assert r"\u001b" in markdown and r"\u202e" in markdown
    # The symbol is a code span its own four backticks cannot close, and the evidence's fence
    # below stays whole: neither lets the route's Markdown out.
    shown = route.replace(chr(0x1B), r"\u001b").replace(chr(0x202E), r"\u202e")
    (line,) = (line for line in markdown.splitlines() if "Evidence `ev-" in line)
    assert line.endswith(f"— ````` GET {shown} `````")
    fences = [item.strip() for item in markdown.splitlines() if item.lstrip().startswith("````")]
    assert fences == ["````text", "````"]


def test_the_search_for_a_schema_marker_is_linear() -> None:
    """A document that does not parse is searched for the key a schema opens with. Blank lines
    by the hundred thousand once made that search quadratic: minutes, where it now takes
    milliseconds."""
    text = "\n" * 300_000 + "["
    pair = SourceRevisionPair(
        path="api.yaml",
        old_path="api.yaml",
        new_path="api.yaml",
        old_text="{}",
        new_text=text,
        hunks=(),
        has_old_side=True,
    )
    started = time.perf_counter()
    result = analyze_schemas([pair])
    assert time.perf_counter() - started < 5
    assert result.not_schema == 1
