#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Code version helper: pyproject is the source, metadata and "unknown" are fallbacks."""

import tomllib
from importlib import metadata
from pathlib import Path

import pytest

from cqc_cpcc import version

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _fresh_cache():
    version.get_version.cache_clear()
    yield
    version.get_version.cache_clear()


@pytest.mark.unit
def test_matches_pyproject_in_a_source_checkout():
    expected = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["poetry"]["version"]
    assert version.get_version() == expected


@pytest.mark.unit
def test_falls_back_to_installed_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(version, "_PYPROJECT", tmp_path / "missing.toml")
    monkeypatch.setattr(metadata, "version", lambda name: "9.9.9")
    assert version.get_version() == "9.9.9"


@pytest.mark.unit
def test_unknown_when_neither_source_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(version, "_PYPROJECT", tmp_path / "missing.toml")

    def absent(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(metadata, "version", absent)
    assert version.get_version() == version.UNKNOWN


@pytest.mark.unit
def test_ignores_a_pyproject_of_another_package(monkeypatch, tmp_path):
    other = tmp_path / "pyproject.toml"
    other.write_text('[tool.poetry]\nname = "other"\nversion = "1.2.3"\n')
    monkeypatch.setattr(version, "_PYPROJECT", other)
    monkeypatch.setattr(metadata, "version", lambda name: "9.9.9")
    assert version.get_version() == "9.9.9"


@pytest.mark.unit
def test_malformed_pyproject_falls_through(monkeypatch, tmp_path):
    bad = tmp_path / "pyproject.toml"
    bad.write_text("not [toml")
    monkeypatch.setattr(version, "_PYPROJECT", bad)
    monkeypatch.setattr(metadata, "version", lambda name: "9.9.9")
    assert version.get_version() == "9.9.9"
