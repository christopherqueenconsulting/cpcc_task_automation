#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Grading results kept on this computer across app restarts.

Christopher's decision (2026-10-06): results survive a restart, so the Needs review list
and the downloads are still there next session. They contain student names and scores,
so they are stored only locally, owner-only, outside the repository:
``~/.cqc_cpcc/grading_results/<run_key>.json`` (``CQC_GRADING_RESULTS_DIR`` overrides).
Only the most recent ``MAX_RUNS`` runs are kept.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

from cqc_cpcc.rubric_models import RubricAssessmentResult
from cqc_cpcc.utilities.logger import logger

MAX_RUNS = 20


def results_dir() -> Path:
    return Path(os.environ.get("CQC_GRADING_RESULTS_DIR")
                or Path.home() / ".cqc_cpcc" / "grading_results")


def _path(run_key: str) -> Path:
    if not run_key.isalnum():
        raise ValueError("run key must be alphanumeric")
    return results_dir() / f"{run_key}.json"


def save_run(run_key: str, mode: str, results: list, failures: Optional[list] = None) -> None:
    """Write one run's results atomically (0600 file in a 0700 folder) and prune old runs."""
    directory = results_dir()
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    if mode == "errors_only":
        payload_results = [[sid, summary] for sid, summary in results]
    else:
        payload_results = [[sid, r.model_dump(mode="json")] for sid, r in results]
    payload = {"run_key": run_key, "mode": mode, "saved_at": time.time(),
               "results": payload_results, "failures": list(failures or [])}
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".run.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, _path(run_key))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    _prune(directory)


def _prune(directory: Path) -> None:
    runs = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in runs[MAX_RUNS:]:
        old.unlink(missing_ok=True)


def delete_run(run_key: str) -> None:
    try:
        _path(run_key).unlink(missing_ok=True)
    except ValueError:
        pass


def load_runs() -> list[dict]:
    """Every saved run, newest first, with results rebuilt as model objects."""
    directory = results_dir()
    if not directory.is_dir():
        return []
    runs = []
    for path in sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("mode") != "errors_only":
                data["results"] = [(sid, RubricAssessmentResult.model_validate(r))
                                   for sid, r in data["results"]]
            else:
                data["results"] = [(sid, summary) for sid, summary in data["results"]]
            runs.append(data)
        except Exception as e:  # noqa: BLE001 - one bad file must not hide the rest
            logger.warning("Skipping unreadable saved results %s: %s", path.name, e)
    return runs
