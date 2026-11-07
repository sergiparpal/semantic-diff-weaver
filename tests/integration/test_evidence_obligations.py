"""A finding's obligations quote its evidence, and name the code that reads a module variable."""

from __future__ import annotations

from semantic_diff_weaver.service import analyze

ROOTS_OLD = "ROOTS = ('core',)\n\n\ndef select(name):\n    return name in ROOTS\n"
ROOTS_NEW = "ROOTS = ('core', 'tools')\n\n\ndef select(name):\n    return name in ROOTS\n"
TEST = "from pkg.roots import select\n\n\ndef test_select():\n    assert select('core')\n"


def _obligations(repo_factory, old: dict[str, str], new: dict[str, str]) -> list[dict]:
    repo, base, head = repo_factory(old, new)
    envelope = analyze(
        {"repo_path": str(repo), "base_ref": base, "head_ref": head, "output_format": "both"}
    )
    return envelope["analysis"]["test_obligations"]


def test_a_module_variable_s_obligation_names_the_code_that_reads_it(repo_factory) -> None:
    obligations = _obligations(
        repo_factory,
        {"src/pkg/__init__.py": "", "src/pkg/roots.py": ROOTS_OLD, "tests/test_roots.py": TEST},
        {"src/pkg/roots.py": ROOTS_NEW},
    )
    (obligation,) = obligations
    assert obligation["title"] == (
        "Clarify and characterize what `select` does with the changed `ROOTS`"
    )
    assert obligation["when"] == "`select` is called with input the changed value decides"


def test_without_a_call_graph_the_variable_s_readers_stay_unnamed(repo_factory) -> None:
    # With no test to follow and one changed symbol, the analysis builds no call graph.
    obligations = _obligations(
        repo_factory,
        {"src/pkg/__init__.py": "", "src/pkg/roots.py": ROOTS_OLD},
        {"src/pkg/roots.py": ROOTS_NEW},
    )
    (obligation,) = obligations
    assert obligation["title"] == "Clarify and characterize the code that reads `ROOTS`"


def test_a_call_that_only_computes_joins_the_obligation_of_its_symbol_s_condition(
    repo_factory,
) -> None:
    old = "def label(name):\n    if name:\n        return name.title()\n    return ''\n"
    new = (
        "def label(name):\n    if name.strip():\n        return name.strip().title()\n"
        "    return ''\n"
    )
    obligations = _obligations(repo_factory, {"src/label.py": old}, {"src/label.py": new})
    titles = [item["title"] for item in obligations]
    # The changed `strip` call says nothing a test could check that the condition does not.
    assert "Exercise both outcomes of the changed condition in `label`" in titles
    assert not any("changed call" in title for title in titles)
    linked = {item for obligation in obligations for item in obligation["behavior_change_ids"]}
    assert len(linked) >= 2


def test_an_assignment_inside_a_function_has_no_readers_to_name(repo_factory) -> None:
    old = "def scale(value):\n    factor = 2\n    return value * factor\n"
    test = "from scale import scale\n\n\ndef test_scale():\n    assert scale(1)\n"
    obligations = _obligations(
        repo_factory,
        {"scale.py": old, "tests/test_scale.py": test},
        {"scale.py": old.replace("factor = 2", "factor = 3")},
    )
    (obligation,) = obligations
    assert obligation["title"] == "Assert what `scale` computes from its changed assignment"


def test_a_new_private_helper_joins_the_obligation_of_the_code_calling_it(repo_factory) -> None:
    old = "def label(name):\n    if name:\n        return name\n    return ''\n"
    new = (
        "def _clean(name):\n    return name.strip()\n\n\n"
        "def label(name):\n    if _clean(name):\n        return name\n    return ''\n"
    )
    test = "from label import label\n\n\ndef test_label():\n    assert label('a')\n"
    obligations = _obligations(
        repo_factory, {"label.py": old, "tests/test_label.py": test}, {"label.py": new}
    )
    titles = [item["title"] for item in obligations]
    assert "Exercise both outcomes of the changed condition in `label`" in titles
    assert not any("_clean" in title for title in titles)
    (linked,) = [item for item in obligations if item["title"].startswith("Exercise both outcomes")]
    assert len(linked["behavior_change_ids"]) == 2


def test_a_removed_private_class_joins_the_code_that_stopped_calling_it(repo_factory) -> None:
    old = (
        "class _Handler:\n    def __init__(self, stream):\n        self.stream = stream\n\n\n"
        "def log(stream, message):\n    _Handler(stream)\n    stream.write(message)\n"
    )
    new = "def log(stream, message):\n    stream.write(message)\n"
    test = "from log import log\n\n\ndef test_log():\n    assert log is not None\n"
    obligations = _obligations(
        repo_factory, {"log.py": old, "tests/test_log.py": test}, {"log.py": new}
    )
    titles = [item["title"] for item in obligations]
    assert not any("removed `_Handler" in title for title in titles)
    assert any("`log`" in title for title in titles)
