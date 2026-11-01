from __future__ import annotations

import pytest

import semantic_diff_weaver.call_graph as call_graph
from semantic_diff_weaver.ast_diff import module_references
from semantic_diff_weaver.call_graph import (
    CallGraph,
    build_call_graph,
    importable_name,
    package_of,
    source_roots,
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
    roots = source_roots(sources)
    modules = {
        path: module_references(source, package_of(path, roots)) for path, source in sources.items()
    }
    return CallGraph(modules, incomplete=incomplete, roots=roots)


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


def test_a_src_that_is_a_package_keeps_its_name() -> None:
    """`meal-manager`'s `src/__init__.py` makes `src` a package that relative imports climb to."""
    roots = source_roots(["__init__.py", "src/__init__.py", "src/dish.py"])
    assert roots == {"lib"}
    assert importable_name("src/dish.py", roots) == "src.dish"
    assert importable_name("src/__init__.py", roots) == "src"
    assert package_of("src/handlers/add.py", roots) == "src.handlers"
    assert package_of("__init__.py", roots) == ""


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


def test_loading_a_module_that_makes_an_instance_is_no_call_to_its_class() -> None:
    """`default.py` makes `CART` on import, which hands the code importing it no instance."""
    graph = graph_of(STORE)
    cart = ("src/store/cart.py", "Cart")
    assert ("src/store/default.py", "<module>") in graph.reaching(cart)
    assert ("src/store/default.py", "<module>") not in graph.callers(cart)
    assert ("src/store/checkout.py", "checkout") in graph.callers(cart)


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
    assert graph.contents("loop.first") == frozenset()


RULES = {
    "src/rules/__init__.py": "from .table import CLASSIFIERS\n",
    "src/rules/extra.py": "def classify_extra(delta, strict):\n    return None\n",
    "src/rules/table.py": (
        "from functools import partial\n\n"
        "from rules.extra import classify_extra\n\n\n"
        "def classify_error(delta):\n    return None\n\n\n"
        "def classify_condition(delta):\n    return None\n\n\n"
        "BASE = (classify_error,)\n"
        "CLASSIFIERS = (*BASE, classify_condition, partial(classify_extra, strict=True))\n\n\n"
        "def classify(delta):\n    return [rule(delta) for rule in CLASSIFIERS]\n"
    ),
    "src/rules/service.py": (
        "from rules import CLASSIFIERS\n\n\n"
        "def run(delta):\n    return [rule(delta) for rule in CLASSIFIERS]\n"
    ),
}
CONDITION = ("src/rules/table.py", "classify_condition")
ERROR = ("src/rules/table.py", "classify_error")
EXTRA = ("src/rules/extra.py", "classify_extra")


@pytest.mark.parametrize(
    "caller",
    [
        ("src/rules/table.py", "classify"),  # reads its own module's table
        ("src/rules/service.py", "run"),  # imports it through the package that re-exports it
    ],
)
@pytest.mark.parametrize(
    "rule",
    [
        CONDITION,  # named in the table
        ERROR,  # in a table it copies
        EXTRA,  # imported, and handed to the call that makes an entry
    ],
)
def test_code_reading_a_table_of_functions_calls_each_of_them(
    caller: tuple[str, str], rule: tuple[str, str]
) -> None:
    """No call names `classify_condition`; `classify` runs each rule its table holds."""
    assert caller in graph_of(RULES).callers(rule)


def test_a_table_names_what_its_value_refers_to_without_calling_it() -> None:
    """`CART = Cart()` called `Cart` when its module loaded: totalling it later calls a method
    of the cart, never its class. A function, a module, or a cycle of re-exports names nothing."""
    graph = graph_of(RULES)
    assert graph.contents("rules.CLASSIFIERS") == {CONDITION, ERROR, EXTRA}
    assert graph.contents("rules.table.BASE") == {ERROR}
    assert graph.contents("rules.table.classify") == frozenset()
    assert graph.contents("rules.table") == frozenset()
    assert graph.contents("rules.missing") == frozenset()
    store = graph_of(STORE)
    assert store.contents("store.default.CART") == frozenset()
    assert ("src/store/default.py", "current_total") in store.callers(CART_TOTAL)
    assert ("src/store/default.py", "current_total") not in store.callers(
        ("src/store/cart.py", "Cart")
    )
    looping = graph_of({"src/one.py": "from two import x\n", "src/two.py": "from one import x\n"})
    assert looping.contents("one.x") == frozenset()


NET = {
    "src/net/__init__.py": "from .limits import TIMEOUT\n",
    "src/net/limits.py": (
        "TIMEOUT = 30\n"
        "CODES = (502, 503)\n"
        "DOUBLE = TIMEOUT * 2\n\n\n"
        "def retry():\n    return CODES\n\n\n"
        "def reset():\n    global TIMEOUT\n    TIMEOUT = 30\n\n\n"
        "def handler():\n    return None\n\n\n"
        "handler = wrap(handler)\n"
    ),
    "src/net/client.py": (
        "import net.limits\n"
        "from net import TIMEOUT, limits\n"
        "from net.limits import CODES\n\n\n"
        "def fetch():\n    return TIMEOUT\n\n\n"
        "def should_retry(status):\n    return status in limits.CODES\n\n\n"
        "def count():\n    return net.limits.CODES.count(502)\n\n\n"
        "def direct():\n    return CODES\n\n\n"
        "def shadowed(TIMEOUT):\n    return TIMEOUT\n\n\n"
        "def local():\n    CODES = ()\n    return CODES\n\n\n"
        "def renamed(limits):\n    return limits.CODES\n\n\n"
        "def wrapper():\n    return fetch()\n"
    ),
}
LIMITS, CLIENT = "src/net/limits.py", "src/net/client.py"


def test_code_reading_a_module_variable_is_found_wherever_it_reads_it() -> None:
    """In the variable's own module, through `from … import`, through a package that re-exports
    it, or through the module it lives in, its attributes too; a name the code binds itself is
    its own, and top-level code is no reader."""
    graph = graph_of(NET)
    assert graph.readers(LIMITS, "TIMEOUT") == {(LIMITS, "reset"), (CLIENT, "fetch")}
    assert graph.readers(LIMITS, "CODES") == {
        (LIMITS, "retry"),
        (CLIENT, "should_retry"),
        (CLIENT, "count"),
        (CLIENT, "direct"),
    }
    assert graph.readers(LIMITS, "DOUBLE") == frozenset()
    assert graph.readers(CLIENT, "TIMEOUT") == frozenset()


def test_a_dotted_name_names_a_module_variable_through_re_exports() -> None:
    graph = graph_of(NET)
    assert graph.variable("net.TIMEOUT") == (LIMITS, "TIMEOUT")
    assert graph.variable("net.limits.CODES.count") == (LIMITS, "CODES")
    assert graph.variable("net.limits") is None
    # A name the module also defines a function by is the function, reassigned or not.
    assert graph.variable("net.limits.handler") is None
    assert graph.variable("net.missing") is None
    assert graph.variable("elsewhere.TIMEOUT") is None
    looping = graph_of({"src/one.py": "from two import x\n", "src/two.py": "from one import x\n"})
    assert looping.variable("one.x") is None


def test_code_that_calls_a_reader_reaches_the_variable_too() -> None:
    assert graph_of(NET).reading(LIMITS, "TIMEOUT") == {
        (LIMITS, "reset"),
        (CLIENT, "fetch"),
        (CLIENT, "wrapper"),
    }
    assert graph_of(NET).reading(LIMITS, "DOUBLE") == frozenset()


VAULT = {
    "src/vault/lock.py": (
        "class Key:\n    def turn(self):\n        return 1\n\n\n"
        "class Lock:\n"
        "    @property\n    def path(self):\n        return '/'\n\n"
        "    def release(self):\n        return None\n\n\n"
        "class Vaulted:\n    def key(self) -> Key:\n        return Key()\n"
    ),
    "src/vault/store.py": (
        "from vault.lock import Lock, Vaulted\n\n\n"
        "class Store(Vaulted):\n"
        "    def __init__(self, path):\n        self.path = path\n        self.lock = Lock()\n\n"
        "    def read(self):\n        return self.path\n\n"
        "    def where(self):\n        return self.lock.path\n\n"
        "    def unlock(self):\n        return self.key().turn()\n"
    ),
}


def test_an_attribute_of_self_is_a_member_of_its_own_class() -> None:
    """`Store` keeps a `Lock`, whose `path` is not the `self.path` every method reads.

    Read through `self.lock`, it is; `self.key()` is inherited and returns a `Key`.
    """
    graph = graph_of(VAULT)
    lock_path = graph.reaching(("src/vault/lock.py", "Lock.path"))
    assert ("src/vault/store.py", "Store.read") not in lock_path
    assert ("src/vault/store.py", "Store.where") in lock_path
    assert ("src/vault/store.py", "Store.unlock") in graph.reaching(
        ("src/vault/lock.py", "Key.turn")
    )


PLUGIN = {
    "__init__.py": "from .src.repositories import configure\n",
    "src/__init__.py": "from .filelock import data_lock\n",
    "src/filelock.py": (
        "class DataDirLock:\n    def configure(self, path):\n        self.path = path\n\n\n"
        "data_lock = DataDirLock()\n"
    ),
    "src/repositories/__init__.py": (
        "from .. import data_lock\n\n\ndef configure(path):\n    data_lock.configure(path)\n"
    ),
}


def test_an_import_that_climbs_to_a_src_package_reaches_what_it_re_exports() -> None:
    """`from .. import data_lock` names the lock `src/__init__.py` re-exports.

    Named without `src`, the package was `""`, the name of the plugin's own `__init__.py`.
    """
    graph = graph_of(PLUGIN)
    assert ("src/repositories/__init__.py", "configure") in graph.reaching(
        ("src/filelock.py", "DataDirLock.configure")
    )
    assert graph.locate("src.filelock") == ("src/filelock.py", [])
    # Still found without `src`, as code that puts `src` on its path imports it.
    assert graph.locate("filelock") == ("src/filelock.py", [])


def test_a_name_with_a_leading_dot_is_relative_to_the_root_package() -> None:
    """A test loading the root package it lives in names `""`, and its `configure`, which the
    root `__init__.py` re-exports, `.configure`."""
    graph = graph_of(PLUGIN)
    assert graph.locate("") == ("__init__.py", [])
    assert graph.resolve(".configure") == ("src/repositories/__init__.py", "configure")
    assert graph.locate(".src.filelock") == ("src/filelock.py", [])
    assert graph.resolve(".src.filelock.DataDirLock") == ("src/filelock.py", "DataDirLock")


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
