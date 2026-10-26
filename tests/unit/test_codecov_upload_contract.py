"""codecov.yml after_n_builds must equal the coverage uploads the three workflows make.

If they disagree, Codecov either never posts codecov/patch (waits for uploads that never
come) or posts it early from a partial report. Matrix jobs count once per matrix entry.
"""

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
COVERAGE_WORKFLOWS = ("unit-tests.yml", "integration-coverage.yml", "e2e-coverage.yml")


def _matrix_size(job: dict) -> int:
    matrix = (job.get("strategy") or {}).get("matrix") or {}
    size = 1
    for key, values in matrix.items():
        if key in ("include", "exclude"):
            continue
        assert isinstance(values, list), f"matrix.{key} must be a literal list to be counted"
        size *= len(values)
    return size


def _uploads(workflow: str) -> int:
    doc = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text())
    total = 0
    for job in doc["jobs"].values():
        n = sum(
            1
            for step in job.get("steps", [])
            if str(step.get("uses", "")).startswith("codecov/codecov-action@")
        )
        total += n * _matrix_size(job)
    return total


def test_after_n_builds_matches_upload_count():
    cfg = yaml.safe_load((ROOT / "codecov.yml").read_text())
    expected = cfg["codecov"]["notify"]["after_n_builds"]
    assert cfg["comment"]["after_n_builds"] == expected
    counts = {w: _uploads(w) for w in COVERAGE_WORKFLOWS}
    assert sum(counts.values()) == expected, counts


def test_unit_shard_uploads_are_distinctly_named():
    doc = yaml.safe_load((ROOT / ".github/workflows/unit-tests.yml").read_text())
    step = next(
        s for s in doc["jobs"]["shard"]["steps"] if str(s.get("uses", "")).startswith("codecov/codecov-action@")
    )
    assert "matrix.shard" in step["with"]["name"]
    assert step["with"]["flags"] == "unit"


def test_gate_job_keeps_the_required_check_name():
    doc = yaml.safe_load((ROOT / ".github/workflows/unit-tests.yml").read_text())
    gate = doc["jobs"]["unit-tests"]
    assert "name" not in gate, "a custom name would change the required check context"
    assert gate["needs"] == "shard"
    assert str(gate["if"]).strip() == "always()"
