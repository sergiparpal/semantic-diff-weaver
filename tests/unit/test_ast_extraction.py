from __future__ import annotations

from semantic_diff_weaver.ast_diff import extract_symbols


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
