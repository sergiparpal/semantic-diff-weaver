from __future__ import annotations

import pytest

import semantic_diff_weaver.call_graph as call_graph
from semantic_diff_weaver.ast_diff import module_references
from semantic_diff_weaver.call_graph import (
    CallGraph,
    build_call_graph,
    importable_name,
    package_of,
)
from semantic_diff_weaver.errors import ErrorCode, WeaverError
from semantic_diff_weaver.git_diff import GitTreeEntry
from semantic_diff_weaver.models import WeaverConfig

SHOP = {
    "src/shop/__init__.py": "from .pricing import discount\n",
    "src/shop/pricing.py": (
        "def discount(total):\n    return _rate(total)\n\n\ndef _rate(total):\n    return 0.1\n"
    ),
    "src/shop/cart.py": (
        "from shop import pricing\n"
        "from shop import discount as reduce\n"
        "import shop.tax\n\n\n"
        "class Cart:\n"
        "    def __init__(self):\n"
        "        self.items = []\n\n"
        "    def total(self):\n"
        "        return pricing.discount(1) + reduce(2) + shop.tax.vat(3)\n"
    ),
    "src/shop/tax.py": "RATES = {'standard': vat}\n\n\ndef vat(amount):\n    return amount\n",
}


def graph_of(sources: dict[str, str], *, incomplete: bool = False) -> CallGraph:
    modules = {
        path: module_references(source, package_of(path)) for path, source in sources.items()
    }
    return CallGraph(modules, incomplete=incomplete)


@pytest.mark.parametrize(
    ("path", "name", "package"),
    [
        ("src/shop/__init__.py", "shop", "shop"),
        ("src/shop/cart.py", "shop.cart", "shop"),
        ("lib/tools/cli.py", "tools.cli", "tools"),
        ("main.py", "main", ""),
    ],
)
def test_a_file_is_imported_by_its_module_name(path: str, name: str, package: str) -> None:
    assert importable_name(path) == name
    assert package_of(path) == package


def test_a_call_through_an_imported_module_reaches_into_it() -> None:
    reaching = graph_of(SHOP).reaching(("src/shop/pricing.py", "_rate"))
    assert ("src/shop/pricing.py", "discount") in reaching
    assert ("src/shop/cart.py", "Cart.total") in reaching


def test_a_name_is_followed_through_the_package_that_re_exports_it() -> None:
    graph = graph_of(SHOP)
    assert graph.resolve("shop.discount") == ("src/shop/pricing.py", "discount")
    assert graph.resolve("shop.pricing") is None
    assert graph.locate("src.shop.cart") == ("src/shop/cart.py", [])


def test_a_dotted_import_chain_reaches_its_symbol() -> None:
    assert ("src/shop/cart.py", "Cart.total") in graph_of(SHOP).reaching(("src/shop/tax.py", "vat"))


def test_making_an_instance_runs_its_protocol_methods() -> None:
    reaching = graph_of(SHOP).reaching(("src/shop/cart.py", "Cart.__init__"))
    assert ("src/shop/cart.py", "Cart") in reaching


def test_loading_a_module_runs_the_top_level_code_of_what_it_imports() -> None:
    """`cart` imports `tax`, whose top-level code puts `vat` in a table that calls it later."""
    assert ("src/shop/cart.py", "<module>") in graph_of(SHOP).reaching(("src/shop/tax.py", "vat"))


def test_an_attribute_of_some_other_object_is_not_a_same_named_function() -> None:
    """`store.get(...)` calls whatever `store` is, not `shop.cache.get`, which is imported."""
    graph = graph_of(
        {
            "src/shop/cache.py": "def get(key):\n    return key\n",
            "src/shop/view.py": "import shop.cache\n\n\ndef render(store):\n    return store.get(1)\n",
        }
    )
    assert ("src/shop/view.py", "render") not in graph.reaching(("src/shop/cache.py", "get"))


class FakeRepository:
    def __init__(self, files: dict[str, str]) -> None:
        self.objects = {path: f"{index + 1:040x}" for index, path in enumerate(sorted(files))}
        self.texts = {self.objects[path]: text for path, text in files.items()}
        self.read: set[str] = set()

    def list_files(self, commit: str) -> list[str]:
        return sorted(self.objects)

    def tree_entries(self, commit: str, paths: list[str]) -> dict[str, GitTreeEntry]:
        return {
            path: GitTreeEntry(mode="100644", object_id=self.objects[path])
            for path in paths
            if path in self.objects
        }

    def read_blob_objects(
        self, object_ids: set[str], max_bytes: int, *, max_total_bytes: int
    ) -> dict[str, str | None]:
        by_id = {object_id: path for path, object_id in self.objects.items()}
        self.read.update(by_id[object_id] for object_id in object_ids)
        return {object_id: self.texts[object_id] for object_id in object_ids}


def test_the_graph_reads_what_tests_import_and_follows_imports_from_there() -> None:
    repository = FakeRepository(
        {
            **SHOP,
            "src/shop/unused.py": "def idle():\n    return 0\n",
            "tests/test_cart.py": "import shop.cart\n",
        }
    )
    graph = build_call_graph(
        repository, "a" * 40, WeaverConfig(), imports=["shop.cart.Cart"], changed=["README.md"]
    )
    assert set(graph.modules) == set(SHOP)
    assert repository.read == set(SHOP)
    assert graph.incomplete is False
    assert graph.warnings == []


def test_a_graph_past_its_file_bound_is_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(call_graph, "MAX_CALL_GRAPH_FILES", 2)
    graph = build_call_graph(
        FakeRepository(SHOP), "a" * 40, WeaverConfig(), imports=["shop.cart"], changed=[]
    )
    assert len(graph.modules) == 2
    assert graph.incomplete is True
    assert graph.warnings[0].startswith("Following calls across modules was capped at 2 modules")


def test_a_module_that_does_not_parse_leaves_the_graph_incomplete() -> None:
    graph = build_call_graph(
        FakeRepository({**SHOP, "src/shop/tax.py": "def vat(:\n"}),
        "a" * 40,
        WeaverConfig(),
        imports=[],
        changed=["src/shop/cart.py"],
    )
    assert "src/shop/tax.py" not in graph.modules
    assert "src/shop/cart.py" in graph.modules
    assert graph.incomplete is True
    assert "could not be read within bounds" in graph.warnings[0]


def test_a_repository_listing_past_its_bound_yields_an_empty_incomplete_graph() -> None:
    class FailingRepository(FakeRepository):
        def list_files(self, commit: str) -> list[str]:
            raise WeaverError(ErrorCode.DIFF_TOO_LARGE, "too large", "narrow it")

    graph = build_call_graph(
        FailingRepository(SHOP), "a" * 40, WeaverConfig(), imports=["shop"], changed=[]
    )
    assert graph.modules == {}
    assert graph.incomplete is True
