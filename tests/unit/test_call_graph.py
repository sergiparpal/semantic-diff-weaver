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


STORE = {
    "src/store/__init__.py": "from .cart import Cart\n",
    "src/store/cart.py": (
        "class Cart:\n"
        "    def total(self):\n"
        "        return 0\n\n"
        "    def save(self):\n"
        "        return 1\n\n\n"
        "class BigCart(Cart, dict):\n"
        "    def total(self):\n"
        "        return self.save()\n"
    ),
    "src/store/checkout.py": (
        "from store.cart import Cart\n"
        "from store.gift import GiftCart\n\n\n"
        "def checkout():\n"
        "    cart = Cart()\n"
        "    return cart.total()\n\n\n"
        "def pay(cart: Cart):\n"
        "    return cart.total()\n\n\n"
        "def render(store):\n"
        "    return store.total()\n\n\n"
        "def wrap():\n"
        "    gift = GiftCart()\n"
        "    return gift.save() + gift.total()\n\n\n"
        "def note(order):\n"
        "    return order.Cart\n"
    ),
    "src/store/shop.py": (
        "from store import Cart\n\n\n"
        "class Shop:\n"
        "    def __init__(self):\n"
        "        self.cart = Cart()\n\n"
        "    def checkout(self):\n"
        "        return self.cart.total()\n"
    ),
    "src/store/gift.py": (
        "from store import cart\n\n\n"
        "class GiftCart(cart.Cart):\n"
        "    def total(self):\n"
        "        return self.save()\n"
    ),
    "src/store/default.py": (
        "from store.cart import Cart\n\n"
        "CART = Cart()\n\n\n"
        "def current_total():\n"
        "    return CART.total()\n\n\n"
        "def any_total(thing):\n"
        "    return thing.total()\n"
    ),
    "src/store/catalog.py": (
        "from dataclasses import dataclass\n\n"
        "from store.cart import Cart\n\n\n"
        "def fresh_cart() -> Cart:\n"
        "    return Cart()\n\n\n"
        "@dataclass\n"
        "class Order:\n"
        "    cart: Cart\n\n\n"
        "class Shelf:\n"
        "    def take(self) -> Cart:\n"
        "        return Cart()\n"
    ),
    "src/store/browse.py": (
        "from store.catalog import Order, Shelf, fresh_cart\n\n\n"
        "def quote():\n"
        "    return fresh_cart().total()\n\n\n"
        "def settle(order: Order):\n"
        "    return order.cart.total()\n\n\n"
        "def pick(shelf: Shelf):\n"
        "    return shelf.take().total()\n\n\n"
        "def count():\n"
        "    cart = fresh_cart()\n"
        "    total = 0\n"
        "    return cart, total\n"
    ),
}
CART_TOTAL = ("src/store/cart.py", "Cart.total")


@pytest.mark.parametrize("caller", ["checkout", "pay"])
def test_a_call_on_an_instance_reaches_the_method_of_a_class_from_another_module(
    caller: str,
) -> None:
    """`checkout` makes the `Cart` that `cart.py` defines, and `pay` is handed one."""
    assert ("src/store/checkout.py", caller) in graph_of(STORE).reaching(CART_TOTAL)


def test_a_method_reaches_what_another_method_of_its_class_keeps_on_self() -> None:
    """`Shop.__init__` keeps a `Cart`, imported through the package, that `checkout` totals."""
    assert ("src/store/shop.py", "Shop.checkout") in graph_of(STORE).reaching(CART_TOTAL)


def test_a_module_level_instance_is_known_to_the_functions_that_use_it() -> None:
    """`any_total` shares a module with `CART` and totals whatever its caller hands it."""
    reaching = graph_of(STORE).reaching(CART_TOTAL)
    assert ("src/store/default.py", "current_total") in reaching
    assert ("src/store/default.py", "any_total") not in reaching


def test_an_attribute_is_never_what_an_import_binds() -> None:
    """`order.Cart` reads an attribute of `order`, not the `Cart` that `checkout.py` imports."""
    assert ("src/store/checkout.py", "note") not in graph_of(STORE).reaching(
        ("src/store/cart.py", "Cart")
    )


def test_an_instance_reaches_the_methods_its_class_inherits() -> None:
    """`GiftCart` inherits `save` from a base that another module defines."""
    reaching = graph_of(STORE).reaching(("src/store/cart.py", "Cart.save"))
    assert ("src/store/gift.py", "GiftCart.total") in reaching
    assert ("src/store/checkout.py", "wrap") in reaching


@pytest.mark.parametrize(
    "caller",
    [
        "quote",  # `fresh_cart()` is annotated to return a `Cart`
        "settle",  # an `Order` holds a `Cart` in a field
        "pick",  # `Shelf.take()`, a method, is annotated to return one
    ],
)
def test_an_instance_is_followed_through_return_annotations_and_fields(caller: str) -> None:
    assert ("src/store/browse.py", caller) in graph_of(STORE).reaching(CART_TOTAL)


def test_only_an_attribute_calls_a_method() -> None:
    """`count` holds a `Cart` and a local variable named `total`, which calls nothing."""
    assert ("src/store/browse.py", "count") not in graph_of(STORE).reaching(CART_TOTAL)


def test_an_object_of_no_known_class_reaches_no_method() -> None:
    """`render` gets `store` from its caller, and `pay` a `Cart` that may be a `GiftCart`."""
    graph = graph_of(STORE)
    assert ("src/store/checkout.py", "render") not in graph.reaching(CART_TOTAL)
    assert ("src/store/checkout.py", "pay") not in graph.reaching(
        ("src/store/gift.py", "GiftCart.total")
    )


def test_a_method_is_reached_through_its_class_and_every_class_that_inherits_it() -> None:
    """`dict`, the other base of `BigCart`, is no class of the repository and is left out."""
    graph = graph_of(STORE)
    assert graph.receivers(("src/store/cart.py", "Cart.save")) == {
        ("src/store/cart.py", "Cart"),
        ("src/store/cart.py", "BigCart"),
        ("src/store/gift.py", "GiftCart"),
    }
    assert graph.receivers(CART_TOTAL) == graph.receivers(("src/store/cart.py", "Cart.save"))
    assert graph.receivers(("src/store/checkout.py", "checkout")) == frozenset()
    assert graph.receivers(("src/store/missing.py", "Cart.total")) == frozenset()
    assert CART_TOTAL in graph.methods


KITCHEN = {
    "src/kitchen/__init__.py": "from .stores import pantry\n",
    "src/kitchen/storage.py": (
        "from typing import Protocol\n\n\n"
        "class Snapshot:\n    def count(self):\n        return 0\n\n\n"
        "class Pantry(Protocol):\n    def load(self): ...\n\n\n"
        "class JsonPantry:\n"
        "    def load(self):\n        return []\n\n"
        "    def snapshot(self) -> Snapshot:\n        return Snapshot()\n"
    ),
    "src/kitchen/stores.py": (
        "from kitchen.storage import JsonPantry, Pantry\n\n"
        "pantry: Pantry = JsonPantry()\n"
        "spare = pantry\n"
        "stock = pantry.snapshot()\n"
    ),
    "src/kitchen/handlers.py": (
        "import kitchen\n"
        "from kitchen import stores\n"
        "from kitchen.stores import pantry, spare, stock\n\n"
        "MIRROR = pantry\n"
        "UNKNOWN = mystery\n"
        "DEEP = pantry.shelf.items()\n\n\n"
        "def list_items():\n    return pantry.load()\n\n\n"
        "def list_mirror():\n    return MIRROR.load()\n\n\n"
        "def list_spares():\n    return spare.load()\n\n\n"
        "def list_from_module():\n    return stores.pantry.load()\n\n\n"
        "def list_from_package():\n    return kitchen.pantry.load()\n\n\n"
        "def count_stock():\n    return stock.count()\n\n\n"
        "def describe(item):\n    return item.load()\n"
    ),
}
PANTRY = ("src/kitchen/storage.py", "Pantry")
JSON_PANTRY = ("src/kitchen/storage.py", "JsonPantry")


@pytest.mark.parametrize(
    "caller",
    [
        "list_items",  # imports the instance `stores.py` makes
        "list_spares",  # imports a variable assigned that instance
        "list_mirror",  # uses its own variable assigned the imported instance
        "list_from_module",  # reaches it through the module that makes it
        "list_from_package",  # through the package that re-exports it
    ],
)
def test_a_call_on_an_imported_instance_reaches_the_method_of_its_class(caller: str) -> None:
    reaching = graph_of(KITCHEN).reaching(("src/kitchen/storage.py", "JsonPantry.load"))
    assert ("src/kitchen/handlers.py", caller) in reaching


def test_an_imported_variable_holds_what_its_assignment_makes_or_returns() -> None:
    """`stock` holds what `pantry.snapshot()` returns, and a class or a module holds nothing."""
    graph = graph_of(KITCHEN)
    assert graph.instances("kitchen.pantry") == {PANTRY, JSON_PANTRY}
    assert graph.instances("kitchen.stores.stock") == {("src/kitchen/storage.py", "Snapshot")}
    assert graph.instances("kitchen.storage.JsonPantry") == frozenset()
    assert graph.instances("kitchen.storage") == frozenset()
    # Nothing says what an unknown name or an attribute of an attribute holds.
    assert graph.instances("kitchen.handlers.UNKNOWN") == frozenset()
    assert graph.instances("kitchen.handlers.DEEP") == frozenset()
    assert ("src/kitchen/handlers.py", "count_stock") in graph.reaching(
        ("src/kitchen/storage.py", "Snapshot.count")
    )
    assert ("src/kitchen/handlers.py", "describe") not in graph.reaching(
        ("src/kitchen/storage.py", "JsonPantry.load")
    )


def test_variables_that_hold_each_other_hold_nothing_else() -> None:
    graph = graph_of({"src/loop.py": "first = second\nsecond = first\n"})
    assert graph.instances("loop.first") == frozenset()


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
