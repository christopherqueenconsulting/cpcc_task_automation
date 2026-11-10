"""Unit-suite shard selection.

CI runs the unit suite as two jobs (see ``.github/workflows/unit-tests.yml`` and
``docs/CI_SHARDING.md``). Each job sets ``UNIT_SHARDS=2`` and ``UNIT_SHARD=<1|2>``; this
hook keeps only the files assigned to that shard. With the variables unset, every test runs,
so a plain local ``pytest`` is unchanged.

Sharding is by FILE, never by test: module- and class-scoped fixtures run once, and
``--dist loadfile`` inside a shard still means one worker per file.
"""

from __future__ import annotations

import hashlib
import os

# Files that dominate the suite's wall clock (seconds measured on CI before sharding): pinned
# so they cannot all hash into one shard. Values are 1-based shard numbers for a 2-shard run;
# with more shards they wrap. Pins: shard 1 = 132 + 80 s, shard 2 = 114 + 47 + 36 s. Every
# other file is placed by a stable hash. A stale pin is harmless (an unmatched path is never
# looked up).
HEAVY_FILE_PINS: dict[str, int] = {
    "tests/unit/test_request_capture.py": 1,
    "tests/unit/test_eval_prompt_cache.py": 1,
    "tests/unit/test_prompt_automation.py": 2,
    "tests/unit/test_grading_solution_suite.py": 2,
    "tests/unit/test_model_eval.py": 2,
}


def shard_for_file(rel_path: str, shards: int) -> int:
    """Return the 1-based shard that owns ``rel_path`` (a rootdir-relative posix path).

    Hashing the relative path, not the absolute one, keeps the split identical on every
    checkout directory.
    """
    if shards <= 1:
        return 1
    pinned = HEAVY_FILE_PINS.get(rel_path)
    if pinned is not None:
        return (pinned - 1) % shards + 1
    return int(hashlib.sha256(rel_path.encode()).hexdigest(), 16) % shards + 1


def pytest_collection_modifyitems(config, items):
    """Keep only the shard named by UNIT_SHARD / UNIT_SHARDS. Unset selects everything."""
    shards = int(os.getenv("UNIT_SHARDS", "1"))
    if shards <= 1:
        return
    shard = int(os.getenv("UNIT_SHARD", "1"))
    if not 1 <= shard <= shards:
        raise ValueError(f"UNIT_SHARD={shard} is outside 1..{shards}")
    kept, dropped = [], []
    for item in items:
        rel = item.nodeid.split("::", 1)[0]
        (kept if shard_for_file(rel, shards) == shard else dropped).append(item)
    items[:] = kept
    config.hook.pytest_deselected(items=dropped)
