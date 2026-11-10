"""Unit-test sharding: every test lands in exactly one shard, heavy files are split (synthetic data)."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.unit import conftest as shard_conftest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
UNIT_FILES = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "tests/unit").rglob("test_*.py"))


def _items(files, per_file=3):
    return [SimpleNamespace(nodeid=f"{f}::test_{i}") for f in files for i in range(per_file)]


def _run_hook(monkeypatch, items, shards, shard):
    if shards is None:
        monkeypatch.delenv("UNIT_SHARDS", raising=False)
    else:
        monkeypatch.setenv("UNIT_SHARDS", str(shards))
    if shard is None:
        monkeypatch.delenv("UNIT_SHARD", raising=False)
    else:
        monkeypatch.setenv("UNIT_SHARD", str(shard))
    selected = list(items)
    deselected = []
    hook = SimpleNamespace(pytest_deselected=lambda items: deselected.extend(items))
    shard_conftest.pytest_collection_modifyitems(SimpleNamespace(hook=hook), selected)
    return selected, deselected


def test_unset_env_keeps_every_test(monkeypatch):
    items = _items(UNIT_FILES)
    kept, dropped = _run_hook(monkeypatch, items, None, None)
    assert kept == items and dropped == []


@pytest.mark.parametrize("shards", [2, 3])
def test_every_test_lands_in_exactly_one_shard(monkeypatch, shards):
    items = _items(UNIT_FILES)
    seen = []
    for shard in range(1, shards + 1):
        kept, dropped = _run_hook(monkeypatch, items, shards, shard)
        assert len(kept) + len(dropped) == len(items)
        seen.extend(i.nodeid for i in kept)
    assert sorted(seen) == sorted(i.nodeid for i in items)
    assert len(seen) == len(set(seen))


def test_a_file_is_never_split_across_shards(monkeypatch):
    items = _items(UNIT_FILES, per_file=5)
    owners = {}
    for shard in (1, 2):
        kept, _ = _run_hook(monkeypatch, items, 2, shard)
        for item in kept:
            owners.setdefault(item.nodeid.split("::")[0], set()).add(shard)
    assert all(len(s) == 1 for s in owners.values())


def test_heavy_files_are_split_and_exist():
    pins = shard_conftest.HEAVY_FILE_PINS
    assert set(pins) <= set(UNIT_FILES), "a pinned heavy file was renamed or deleted"
    assignment = {f: shard_conftest.shard_for_file(f, 2) for f in pins}
    assert set(assignment.values()) == {1, 2}
    assert max(list(assignment.values()).count(1), list(assignment.values()).count(2)) <= 3


def test_split_is_stable_and_reasonably_even():
    first = {f: shard_conftest.shard_for_file(f, 2) for f in UNIT_FILES}
    again = {f: shard_conftest.shard_for_file(f, 2) for f in UNIT_FILES}
    assert first == again
    ones = sum(1 for v in first.values() if v == 1)
    assert 0.3 * len(first) <= ones <= 0.7 * len(first)


def test_shard_number_out_of_range_is_an_error(monkeypatch):
    with pytest.raises(ValueError):
        _run_hook(monkeypatch, _items(UNIT_FILES[:2]), 2, 3)
