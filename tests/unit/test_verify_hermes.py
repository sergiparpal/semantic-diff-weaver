"""Offline checks of how `scripts/verify_hermes.py` names the Hermes release it verified.

Releases after 0.19.0 exist only as Git tags, and a Hermes built from one reports `0.0.0` as its
distribution version, so the label falls back to the name Hermes gives itself.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from scripts.verify_hermes import UNKNOWN_SOURCE_BUILD, hermes_version_label


def _never_read() -> SimpleNamespace:
    raise AssertionError("a release from PyPI is named by its metadata alone")


def test_a_release_from_pypi_is_named_by_its_metadata() -> None:
    assert hermes_version_label("0.14.0", version_info=_never_read) == "0.14.0"


def test_a_source_build_is_named_as_hermes_names_itself() -> None:
    info = SimpleNamespace(base_version="0.23.0", commit="2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19")

    label = hermes_version_label("0.0.0", version_info=lambda: info)

    assert label == "0.23.0 (source checkout 2bd378ed1b8d)"


def test_a_source_build_from_an_unstamped_archive_reads_as_unknown() -> None:
    info = SimpleNamespace(base_version="unknown", commit=None)

    assert hermes_version_label("0.0.0", version_info=lambda: info) == UNKNOWN_SOURCE_BUILD


def test_a_failure_to_read_the_version_leaves_it_unknown_rather_than_failing() -> None:
    def broken() -> SimpleNamespace:
        raise RuntimeError("git is not on PATH")

    assert hermes_version_label("0.0.0", version_info=broken) == UNKNOWN_SOURCE_BUILD


def test_a_source_build_without_the_version_module_reads_as_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "hermes_cli", None)

    assert hermes_version_label("0.0.0") == UNKNOWN_SOURCE_BUILD
