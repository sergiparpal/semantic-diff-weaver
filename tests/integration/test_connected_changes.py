"""Findings that calls connect are grouped, and the brief says what to test them through."""

from __future__ import annotations

from semantic_diff_weaver.service import analyze

MONEY = (
    "def parse_amount(text):\n    return int(text) if len(text) < 10 else 0\n\n\n"
    "def round_total(value):\n    return round(value, 2)\n"
)
CART = (
    "from shop.money import parse_amount, round_total\n\n\n"
    "def checkout(text):\n    total = parse_amount(text)\n"
    "    if total < 100:\n        return round_total(total)\n    return 0\n\n\n"
    "def refund(text):\n    amount = parse_amount(text)\n"
    "    if amount > 0:\n        return amount\n    return None\n"
)
MAIL = "def send_email(user):\n    if user.active > 0:\n        return True\n    return False\n"
LOOP = (
    "def ping(n):\n    return pong(n - 1) if n > 0 else 0\n\n\n"
    "def pong(n):\n    return ping(n - 1) if n > 0 else 1\n"
)


def _changed(text: str) -> str:
    return (
        text.replace("< 10", "<= 10")
        .replace("round(value, 2)", "round(value, 3)")
        .replace("< 100", "<= 100")
        .replace("> 0", ">= 0")
    )


def _analysis(repo_factory, files: dict[str, str]):
    repo, base, head = repo_factory(
        {"src/shop/__init__.py": "", **files},
        {"src/shop/__init__.py": "", **{path: _changed(text) for path, text in files.items()}},
    )
    envelope = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "both"}
    )
    assert envelope["success"] is True
    return envelope["analysis"], envelope["markdown"]


def _symbols(analysis: dict) -> dict[str, str]:
    return {item["id"]: item["evidence"][0]["symbol"] for item in analysis["behavior_changes"]}


def test_findings_calls_connect_are_grouped_by_their_entry_points(repo_factory) -> None:
    analysis, markdown = _analysis(
        repo_factory,
        {"src/shop/money.py": MONEY, "src/shop/cart.py": CART, "src/shop/mail.py": MAIL},
    )
    assert analysis["schema_version"] == "1.3"
    symbols = _symbols(analysis)
    groups = [
        (
            [symbols[item] for item in group["behavior_change_ids"]],
            [symbols[item] for item in group["entry_points"]],
        )
        for group in analysis["change_groups"]
    ]
    assert groups == [
        (["checkout", "parse_amount", "round_total"], ["checkout"]),
        (["refund", "parse_amount"], ["refund"]),
    ]
    section = markdown[markdown.index("### Connected changes") :]
    section = section[: section.index("### Prioritized test obligations")]
    lines = [line for line in section.splitlines() if line.startswith("- ")]
    assert len(lines) == 2
    assert lines[0].startswith("- `checkout` (src/shop/cart\\.py, `bc-")
    assert lines[0].endswith(": a test through `checkout` can exercise all three.")
    assert "reaches `parse\\_amount` (src/shop/money\\.py, `bc-" in lines[0]
    assert lines[1].endswith(": a test through `refund` can exercise both.")
    # Nothing reaches `send_email`, nor does it reach anything that changed.
    assert "send\\_email" not in section


def test_changes_that_reach_each_other_are_said_to(repo_factory) -> None:
    analysis, markdown = _analysis(repo_factory, {"src/shop/loop.py": LOOP})
    (group,) = analysis["change_groups"]
    assert len(group["entry_points"]) == 2 == len(group["behavior_change_ids"])
    assert "reach each other: a test through any of them can exercise both." in markdown


def test_entry_points_that_reach_each_other_lead_what_they_reach_together(repo_factory) -> None:
    loop = (
        "def ping(n):\n"
        "    return pong(n - 1) + echo(n) + shout(n) + whisper(n) if n > 0 else 0\n\n\n"
        "def pong(n):\n    return ping(n - 1) if n > 0 else 1\n\n\n"
        "def echo(n):\n    return n if n > 0 else 0\n\n\n"
        "def shout(n):\n    return n * 2 if n > 0 else 0\n\n\n"
        "def whisper(n):\n    return n // 2 if n > 0 else 0\n"
    )
    analysis, markdown = _analysis(repo_factory, {"src/shop/loop.py": loop})
    (group,) = analysis["change_groups"]
    symbols = _symbols(analysis)
    assert {symbols[item] for item in group["entry_points"]} == {"ping", "pong"}
    assert len(group["behavior_change_ids"]) == 5
    (line,) = (line for line in markdown.splitlines() if "which reach each other" in line)
    assert ", which reach each other, reach `" in line
    assert ", and `" in line
    assert line.endswith(": a test through any of them can exercise all five.")


def test_a_change_no_call_connects_has_no_group_and_no_section(repo_factory) -> None:
    analysis, markdown = _analysis(
        repo_factory, {"src/shop/mail.py": MAIL, "src/shop/money.py": MONEY}
    )
    assert len(analysis["behavior_changes"]) >= 2
    assert analysis["change_groups"] == []
    assert "### Connected changes" not in markdown
