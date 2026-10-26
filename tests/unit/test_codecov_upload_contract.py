"""Contract checks for coverage uploads and explicit Codecov finalization."""

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ".github/workflows/unit-tests.yml"
UPLOAD_ACTIONS = ("codecov/codecov-action@",)


def _workflow() -> dict:
    return yaml.safe_load((ROOT / WORKFLOW).read_text())


def _matrix_size(job: dict) -> int:
    matrix = (job.get("strategy") or {}).get("matrix") or {}
    size = 1
    for key, values in matrix.items():
        if key in ("include", "exclude"):
            continue
        assert isinstance(values, list), f"matrix.{key} must be a literal list to be counted"
        size *= len(values)
    return size


def _is_coverage_upload(step: dict) -> bool:
    if not str(step.get("uses", "")).startswith(UPLOAD_ACTIONS):
        return False
    settings = step.get("with") or {}
    if settings.get("run_command", "upload-coverage") != "upload-coverage":
        return False
    return settings.get("report_type", "coverage") == "coverage"


def test_codecov_notifications_require_explicit_finalization():
    cfg = yaml.safe_load((ROOT / "codecov.yml").read_text())
    notify = cfg["codecov"]["notify"]
    assert notify["manual_trigger"] is True
    assert notify["wait_for_ci"] is True
    assert "after_n_builds" not in notify
    assert "after_n_builds" not in cfg["comment"]


def test_finalizer_waits_for_every_coverage_producer_and_unit_gate():
    jobs = _workflow()["jobs"]
    finalizer = jobs["codecov-finalize"]
    assert set(finalizer["needs"]) == {
        "unit-tests", "compiler-tests", "integration-tests", "e2e-tests"
    }
    assert "always()" not in str(finalizer.get("if", ""))
    send = next(
        step
        for step in finalizer["steps"]
        if str(step.get("uses", "")).startswith(UPLOAD_ACTIONS)
    )
    assert send["with"]["run_command"] == "send-notifications"
    assert send["with"]["fail_ci_if_error"] is True


def test_coverage_uploads_are_in_the_orchestrated_workflow():
    jobs = _workflow()["jobs"]
    counts = {
        name: sum(_is_coverage_upload(step) for step in job.get("steps", []))
        * _matrix_size(job)
        for name, job in jobs.items()
    }
    assert counts == {
        "shard": 2,
        "compiler-tests": 1,
        "unit-tests": 0,
        "integration-tests": 1,
        "e2e-tests": 1,
        "codecov-finalize": 0,
    }


def test_test_results_upload_is_not_counted_as_coverage():
    results = {"uses": "codecov/codecov-action@v7", "with": {"report_type": "test_results"}}
    coverage = {"uses": "codecov/codecov-action@v7", "with": {"flags": "unit"}}
    explicit = {"uses": "codecov/codecov-action@v7", "with": {"report_type": "coverage"}}
    assert not _is_coverage_upload(results)
    assert _is_coverage_upload(coverage)
    assert _is_coverage_upload(explicit)


def test_unit_shard_uploads_are_distinctly_named():
    doc = _workflow()
    step = next(s for s in doc["jobs"]["shard"]["steps"] if _is_coverage_upload(s))
    assert "matrix.shard" in step["with"]["name"]
    assert step["with"]["flags"] == "unit"


def test_gate_job_keeps_the_required_check_name():
    gate = _workflow()["jobs"]["unit-tests"]
    assert "name" not in gate, "a custom name would change the required check context"
    assert set(gate["needs"]) == {"shard", "compiler-tests"}
    assert str(gate["if"]).strip() == "always()"


def test_exhaustive_compiler_checks_are_separate_and_covered_by_the_unit_gate():
    jobs = _workflow()["jobs"]
    command = next(
        step["run"]
        for step in jobs["compiler-tests"]["steps"]
        if step.get("name") == "Run exhaustive real-compiler checks"
    )
    assert '-m "unit and compiler"' in command
    assert "--cov=src" in command
