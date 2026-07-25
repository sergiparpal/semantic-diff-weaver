from __future__ import annotations

import semantic_diff_weaver.test_mapper as test_mapper
from semantic_diff_weaver.ast_diff import StructuralDelta
from semantic_diff_weaver.errors import ErrorCode, WeaverError
from semantic_diff_weaver.git_diff import GitTreeEntry
from semantic_diff_weaver.models import LineRange, MappingRule, WeaverConfig
from semantic_diff_weaver.semantic_candidates import build_candidates
from semantic_diff_weaver.test_mapper import (
    IndexedTest,
    TestIndex,
    _index_source,
    build_test_index,
    map_candidate_tests,
)


def boundary_candidate():
    return build_candidates(
        [
            StructuralDelta(
                path="src/api.py",
                symbol="allowed",
                kind="comparison_change",
                old="x < 5",
                new="x <= 5",
                old_lines=LineRange(start=2, end=2),
                new_lines=LineRange(start=2, end=2),
                hunk_id="src/api.py#hunk-001",
            )
        ]
    )[0]


def test_terminology_alone_cannot_create_candidate() -> None:
    index = TestIndex(
        tests=[
            IndexedTest(
                path="tests/test_other.py",
                symbol="test_boundary",
                imports=frozenset(),
                name_tokens=frozenset({"test", "boundary"}),
                body_tokens=frozenset({"threshold"}),
            )
        ],
        incomplete=False,
        warnings=[],
    )
    assert map_candidate_tests([boundary_candidate()], index, WeaverConfig())[0] == []


def test_structural_features_rank_and_cap_stably() -> None:
    tests = [
        IndexedTest(
            path=f"tests/test_api_{index}.py",
            symbol=f"test_allowed_{index}",
            imports=frozenset({"src.api"}),
            name_tokens=frozenset({"test", "allowed", str(index)}),
            body_tokens=frozenset({"allowed", "boundary"}),
        )
        for index in range(8)
    ]
    mapped = map_candidate_tests(
        [boundary_candidate()],
        TestIndex(tests=tests, incomplete=False, warnings=[]),
        WeaverConfig(),
    )[0]
    assert len(mapped) == 5
    assert all(item.verified is False for item in mapped)
    assert mapped == sorted(mapped, key=lambda item: (-item.match_score, item.path, item.symbol))


def test_test_index_has_aggregate_file_and_byte_caps(monkeypatch) -> None:
    class FakeRepository:
        def list_files(self, commit: str) -> list[str]:
            return ["tests/test_a.py", "tests/test_b.py"]

        def tree_entries(self, commit: str, paths: list[str]):
            return {
                path: GitTreeEntry(mode="100644", object_id=f"{index + 1:040x}")
                for index, path in enumerate(paths)
            }

        def read_blob_objects(
            self,
            object_ids: set[str],
            max_bytes: int,
            *,
            max_total_bytes: int,
        ) -> dict[str, str | None]:
            source = "def test_value():\n    assert True\n"
            return {
                object_id: source if len(source.encode()) <= max_total_bytes else None
                for object_id in object_ids
            }

    monkeypatch.setattr(test_mapper, "MAX_TEST_INDEX_FILES", 1)
    index = build_test_index(FakeRepository(), "a" * 40, WeaverConfig())
    assert index.incomplete is True
    assert len(index.tests) == 1
    assert any("capped" in warning for warning in index.warnings)

    monkeypatch.setattr(test_mapper, "MAX_TEST_INDEX_FILES", 500)
    monkeypatch.setattr(test_mapper, "MAX_TEST_INDEX_BYTES", 1)
    byte_limited = build_test_index(FakeRepository(), "a" * 40, WeaverConfig())
    assert byte_limited.incomplete is True
    assert byte_limited.tests == []
    assert any("bounded source limits" in warning for warning in byte_limited.warnings)


def test_test_index_batches_reads_and_honors_excludes() -> None:
    class FakeRepository:
        def __init__(self) -> None:
            self.tree_calls = 0
            self.blob_calls = 0

        def list_files(self, commit: str) -> list[str]:
            return ["tests/test_keep.py", "tests/generated/test_skip.py"]

        def tree_entries(self, commit: str, paths: list[str]):
            self.tree_calls += 1
            return {path: GitTreeEntry(mode="100644", object_id="a" * 40) for path in paths}

        def read_blob_objects(self, object_ids, max_bytes, *, max_total_bytes):
            self.blob_calls += 1
            return {"a" * 40: "def test_keep():\n    assert True\n"}

    repository = FakeRepository()
    config = WeaverConfig()
    config.paths.exclude.append("tests/generated/**")
    index = build_test_index(repository, "b" * 40, config)
    assert [item.path for item in index.tests] == ["tests/test_keep.py"]
    assert repository.tree_calls == 1
    assert repository.blob_calls == 1


def test_test_discovery_limit_degrades_to_incomplete_mapping() -> None:
    class FakeRepository:
        def list_files(self, commit: str) -> list[str]:
            raise WeaverError(ErrorCode.DIFF_TOO_LARGE, "safe", "narrow")

    index = build_test_index(FakeRepository(), "b" * 40, WeaverConfig())
    assert index.incomplete is True
    assert index.tests == []
    assert any("safe Git boundary" in warning for warning in index.warnings)


def test_index_source_handles_import_forms_classes_and_async_tests() -> None:
    indexed = _index_source(
        "tests/test_sample.py",
        "import package\n"
        "from . import helper\n"
        "class TestSample:\n"
        "    def helper(self):\n"
        "        return None\n"
        "    async def test_value(self):\n"
        "        assert helper\n",
    )
    assert [item.symbol for item in indexed] == ["TestSample.test_value"]
    assert indexed[0].imports == frozenset({"package", "helper"})


def test_configured_mapping_is_structural_and_scoped_to_its_own_rule() -> None:
    """An explicit mapping must promote only tests matching that rule's own test patterns."""
    index = TestIndex(
        tests=[
            IndexedTest(
                path="tests/contract/test_api_contract.py",
                symbol="test_threshold",
                imports=frozenset(),
                name_tokens=frozenset({"test", "threshold"}),
                body_tokens=frozenset({"boundary"}),
            ),
            IndexedTest(
                path="tests/unrelated/test_other.py",
                symbol="test_threshold",
                imports=frozenset(),
                name_tokens=frozenset({"test", "threshold"}),
                body_tokens=frozenset({"boundary"}),
            ),
        ],
        incomplete=False,
        warnings=[],
    )
    config = WeaverConfig()
    config.mapping = [
        MappingRule(source="src/api.py", tests=["tests/contract/**"]),
        # A rule that matches no candidate must not lend its tests to one that does.
        MappingRule(source="src/other.py", tests=["tests/unrelated/**"]),
    ]
    mapped = map_candidate_tests([boundary_candidate()], index, config)[0]
    assert [item.path for item in mapped] == ["tests/contract/test_api_contract.py"]
    assert "explicit configured mapping" in mapped[0].match_reasons
    # Terminology alone still cannot promote the unmapped peer.
    assert map_candidate_tests([boundary_candidate()], index, WeaverConfig())[0] == []


def test_unmatched_configured_mapping_leaves_candidates_unmapped() -> None:
    index = TestIndex(
        tests=[
            IndexedTest(
                path="tests/contract/test_api_contract.py",
                symbol="test_threshold",
                imports=frozenset(),
                name_tokens=frozenset({"test", "threshold"}),
                body_tokens=frozenset({"boundary"}),
            )
        ],
        incomplete=False,
        warnings=[],
    )
    config = WeaverConfig()
    config.mapping = [MappingRule(source="src/nothing.py", tests=["tests/contract/**"])]
    assert map_candidate_tests([boundary_candidate()], index, config)[0] == []


def test_symbol_suffix_import_is_matched_through_the_leaf_index() -> None:
    """``from src.api import allowed`` indexes as a dotted import whose leaf is the symbol.

    The import signal is resolved from a leaf index plus a per-module scan rather than a scan
    per (module, symbol) pair, so this pins the leaf half of that split. Import (0.25) plus
    category terminology (0.10) is exactly the 0.35 floor; the import is what makes it
    structural, and terminology alone could never promote the same test.
    """
    index = TestIndex(
        tests=[
            IndexedTest(
                path="tests/test_unrelated.py",
                symbol="test_case",
                imports=frozenset({"src.api.allowed"}),
                name_tokens=frozenset({"test", "case"}),
                body_tokens=frozenset({"boundary"}),
            )
        ],
        incomplete=False,
        warnings=[],
    )
    mapped = map_candidate_tests([boundary_candidate()], index, WeaverConfig())[0]
    assert [item.path for item in mapped] == ["tests/test_unrelated.py"]
    assert "direct module or symbol import" in mapped[0].match_reasons


def test_a_bare_import_never_matches_on_the_symbol_alone() -> None:
    """Identical to the case above except the import has no dot, so it has no leaf to match."""
    index = TestIndex(
        tests=[
            IndexedTest(
                path="tests/test_unrelated.py",
                symbol="test_case",
                imports=frozenset({"allowed"}),
                name_tokens=frozenset({"test", "case"}),
                body_tokens=frozenset({"boundary"}),
            )
        ],
        incomplete=False,
        warnings=[],
    )
    assert map_candidate_tests([boundary_candidate()], index, WeaverConfig())[0] == []
