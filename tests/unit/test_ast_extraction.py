from __future__ import annotations

from semantic_diff_weaver.ast_diff import extract_symbols, module_references


def test_extracts_nested_async_decorated_symbols() -> None:
    source = """
class Service:
    @classmethod
    async def fetch(cls, value: int = 3) -> dict[str, int]:
        def normalize(item):
            return item + 1
        if value < 5:
            return {"value": normalize(value)}
        raise ValueError(value)
"""
    symbols = {item.qualified_name: item for item in extract_symbols(source)}
    assert {"<module>", "Service", "Service.fetch", "Service.fetch.normalize"} <= symbols.keys()
    assert symbols["Service.fetch"].kind == "async_method"
    assert symbols["Service.fetch.normalize"].kind == "function"
    assert symbols["Service.fetch"].signature.endswith("-> dict[str, int]")
    assert symbols["Service.fetch"].default_map == {"value": "3"}
    assert symbols["Service.fetch"].decorators == ("classmethod",)
    assert symbols["Service.fetch"].features["comparisons"]
    assert symbols["Service.fetch"].features["raises"]


def test_decorator_arguments_are_never_retained_as_context() -> None:
    symbols = extract_symbols(
        '@route("/private/path", token="must-not-leak")\ndef endpoint():\n    return 1\n'
    )
    endpoint = next(item for item in symbols if item.qualified_name == "endpoint")
    assert endpoint.decorators == ("route",)
    serialized_features = repr(endpoint.features)
    assert "/private/path" not in serialized_features
    assert "must-not-leak" not in serialized_features
    assert endpoint.features["calls"] == ()


def test_docstrings_and_formatting_do_not_change_fingerprint() -> None:
    first = extract_symbols('def value(x):\n    """old"""\n    return x + 1\n')[0]
    second = extract_symbols('def value( x ):\n    """new docs"""\n    return (x + 1)\n')[0]
    assert first.fingerprint == second.fingerprint


def test_parsing_does_not_execute_source() -> None:
    symbols = extract_symbols('raise RuntimeError("must not execute")\ndef safe():\n    return 1\n')
    assert any(item.qualified_name == "safe" for item in symbols)


def test_an_added_class_or_module_is_shown_by_what_it_introduces() -> None:
    """Its signature alone read `bases()` for a dataclass and `module` for a `__main__.py`."""
    source = (
        '"""Entry point."""\n'
        "from dataclasses import dataclass\n"
        "from pkg import cli\n\n"
        "@dataclass(frozen=True)\n"
        "class RunArgs:\n"
        '    """One field per option."""\n'
        "    pr: int\n"
        "    rerun: str | None\n"
        "    LIMIT = 3\n\n"
        "    def run(self):\n"
        "        return self.pr\n\n"
        "class Failure(Exception, metaclass=type):\n"
        "    pass\n\n"
        "raise SystemExit(cli.main())\n"
    )
    symbols = {item.qualified_name: item for item in extract_symbols(source)}
    assert (
        symbols["RunArgs"].display == "@dataclass class RunArgs: pr: int, rerun: str | None, LIMIT"
    )
    assert symbols["RunArgs"].signature == "bases()"
    assert symbols["Failure"].display == "class Failure(Exception, metaclass=type)"
    assert symbols["RunArgs.run"].display == symbols["RunArgs.run"].signature
    assert symbols["<module>"].display == (
        "from dataclasses import dataclass\nfrom pkg import cli\nraise SystemExit(cli.main())"
    )


def test_a_long_class_or_module_display_ends_with_an_ellipsis() -> None:
    fields = "".join(f"    field_{index}: int\n" for index in range(25))
    statements = "".join(f"value_{index} = {index}\n" for index in range(8))
    symbols = {
        item.qualified_name: item for item in extract_symbols(f"class Wide:\n{fields}{statements}")
    }
    assert symbols["Wide"].display.endswith("field_19: int, …")
    assert symbols["<module>"].display == "\n".join(
        [*(f"value_{index} = {index}" for index in range(5)), "…"]
    )
    [module] = [
        item for item in extract_symbols("def f():\n    return 1\n") if item.kind == "module"
    ]
    assert module.display == "module"


def test_a_symbol_records_the_names_its_code_uses_called_or_not() -> None:
    """`type=pr_number` hands a function to argparse without calling it, and still reaches it."""
    source = (
        "import argparse\n"
        "HANDLERS = {'run': dispatch}\n\n"
        "@command\n"
        "def build():\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('--pr', type=pr_number)\n"
        "    return messages.render(parser)\n"
    )
    module = module_references(source, package="pkg")
    build = module.symbols["build"]
    assert {"argparse", "pr_number", "parser", "messages"} <= build.names
    assert {"ArgumentParser", "add_argument", "render"} <= build.attributes
    assert {"argparse.ArgumentParser", "messages.render", "parser.add_argument"} <= build.dotted
    assert build.decorated is True
    assert module.symbols["<module>"].names == {"HANDLERS", "dispatch"}
    assert module.symbols["<module>"].decorated is False


def test_each_symbol_records_its_kind_and_a_class_its_bases() -> None:
    """A call on an instance reaches its class's methods, and the ones its bases define."""
    source = (
        "import abc\n"
        "class Box(Base, abc.ABC, Generic[T], make_base(), metaclass=Meta):\n"
        "    def open(self):\n"
        "        def inner():\n"
        "            return 1\n"
        "        return inner()\n"
        "    async def close(self):\n"
        "        return 0\n"
        "def build():\n"
        "    return Box()\n"
    )
    symbols = module_references(source, package="pkg").symbols
    assert {name: item.kind for name, item in symbols.items()} == {
        "Box": "class",
        "Box.open": "method",
        "Box.open.inner": "function",
        "Box.close": "async_method",
        "build": "function",
        "<module>": "module",
    }
    assert symbols["Box"].bases == ("Base", "abc.ABC", "Generic")
    assert symbols["build"].bases == ()


def test_a_function_records_its_return_annotation_and_the_attributes_it_uses() -> None:
    """A caller holds what a function returns, and calls a method only as an attribute."""
    source = (
        "def build(order) -> list[shop.Cart] | None:\n    total = order.items\n    return total\n"
    )
    build = module_references(source, package="pkg").symbols["build"]
    assert build.returns == {"list", "shop", "shop.Cart"}
    assert "items" in build.attributes
    assert "total" not in build.attributes
    assert {"order", "total"} <= build.names


def test_a_method_records_apart_what_it_reads_on_its_own_instance() -> None:
    """`self.weight` names a member of the method's class; `other.size` could be anything's."""
    source = (
        "class Box:\n"
        "    def open(self, other):\n"
        "        return self.lid.lift() + other.size + self.weight\n"
        "    @classmethod\n"
        "    def make(cls):\n"
        "        return cls.default()\n"
        "    @staticmethod\n"
        "    def check(self):\n"
        "        return self.ok\n"
        "def free(self):\n"
        "    return self.loose\n"
    )
    symbols = module_references(source, package="pkg").symbols
    assert symbols["Box.open"].own_attributes == {"lid", "weight"}
    assert symbols["Box.open"].attributes == {"lift", "size"}
    assert symbols["Box.make"].own_attributes == {"default"}
    assert symbols["Box.check"].own_attributes == frozenset()
    assert symbols["Box.check"].attributes == {"ok"}
    assert symbols["free"].attributes == {"loose"}


def test_a_module_records_what_each_of_its_variables_holds() -> None:
    """A variable holds what its value is, not every name in it: `JsonStore(path)` is a store.

    `loaded` holds whatever `load` returns, and assignments inside a function or a class bind
    something else.
    """
    source = (
        "dish_repo: Store = JsonStore(PATH / 'x')\n"
        "loaded = dish_repo.load()\n"
        "a, b = A(), B()\n"
        "c, *rest = make()\n"
        "TABLE = {'x': X(), 'y': make_y()}\n"
        "TABLE['z'] = Z()\n"
        "pick = First() if FLAG else Second()\n"
        "items = [Item(n) for n in range(3)]\n"
        "GROUP = [Left(), Right()]\n"
        "fallback = cache or Cache()\n"
        "index = {key: Entry() for key in KEYS}\n"
        "combined = [*base_items, Extra()]\n"
        "named = (made := Made())\n"
        "here = Path(__file__).parent\n"
        "declared: Thing\n"
        "obj.attr = Other()\n"
        "obj.typed: int = 3\n"
        "if FLAG:\n    guarded = Guarded()\n"
        "try:\n    client = Client()\nexcept ImportError:\n    client = None\n"
        "def build():\n    local = Local()\n"
        "class Box:\n    inner = Inner()\n"
    )
    assert module_references(source, package="pkg").variables == {
        "dish_repo": {"Store", "JsonStore"},
        "loaded": {"dish_repo.load"},
        "a": {"A"},
        "b": {"B"},
        "c": {"make"},
        "rest": {"make"},
        "TABLE": {"X", "make_y", "Z"},
        "pick": {"First", "Second"},
        "items": {"Item"},
        "GROUP": {"Left", "Right"},
        "fallback": {"cache", "Cache"},
        "index": {"Entry"},
        "combined": {"base_items", "Extra"},
        "named": {"Made"},
        "here": set(),
        "declared": {"Thing"},
        "guarded": {"Guarded"},
        "client": {"Client"},
    }


def test_imports_bind_names_to_absolute_modules_and_symbols() -> None:
    source = (
        "import os.path\n"
        "import distiller.cli as cli\n"
        "from distiller import messages\n"
        "from . import evaluate\n"
        "from .report import run as report_run\n"
        "from .. import shared\n"
        "from helpers import *\n"
        "def main():\n"
        "    from .lazy import load\n"
        "    return load()\n"
    )
    module = module_references(source, package="distiller.commands")
    assert module.imports == {
        "os": "os",
        "cli": "distiller.cli",
        "messages": "distiller.messages",
        "evaluate": "distiller.commands.evaluate",
        "report_run": "distiller.commands.report.run",
        "shared": "distiller.shared",
        "load": "distiller.commands.lazy.load",
    }
    assert {"os.path", "distiller", "distiller.commands.report", "helpers"} <= module.loads
