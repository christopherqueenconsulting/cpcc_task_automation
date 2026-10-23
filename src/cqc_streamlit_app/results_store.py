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


def save_run(run_key: str, mode: str, results: list, failures: Optional[list] = None,
             greeting_names: Optional[dict] = None, meta: Optional[dict] = None) -> None:
    """Write one run's results atomically (0600 file in a 0700 folder) and prune old runs.

    ``meta`` holds PII-free facts about how the run was graded (solution given, checklist
    size and hash, prompt version, model). Omitted, the saved file's existing meta is kept.
    """
    directory = results_dir()
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    if mode == "errors_only":
        payload_results = [[sid, summary] for sid, summary in results]
    else:
        # The prompt (llm_request) holds the whole submission: it is never written to disk.
        payload_results = [[sid, r.model_dump(mode="json", exclude={"llm_request"})] for sid, r in results]
    if meta is None:
        meta = _saved_meta(run_key)
    payload = {"run_key": run_key, "mode": mode, "saved_at": time.time(),
               "results": payload_results, "failures": list(failures or []),
               # Name to greet each student by ("" for a loose file named after nothing).
               "greeting_names": greeting_names, "meta": meta}
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


def _saved_meta(run_key: str) -> Optional[dict]:
    try:
        return json.loads(_path(run_key).read_text(encoding="utf-8")).get("meta")
    except (OSError, ValueError):
        return None


def remembered_effort(instructions_hash: str) -> Optional[str]:
    """The reasoning effort most students were graded at the last time these instructions
    were graded (``meta["instructions_hash"]`` and ``meta["effort"]``), newest run first.
    ``None`` when no saved run matches or it ran at the default effort."""
    directory = results_dir()
    if not instructions_hash or not directory.is_dir():
        return None
    for path in sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            meta = json.loads(path.read_text(encoding="utf-8")).get("meta") or {}
        except (OSError, ValueError):
            continue
        if meta.get("instructions_hash") == instructions_hash:
            return meta.get("effort") or None
    return None


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
                # A saved file never holds the prompt; one that claims to is not trusted.
                data["results"] = [(sid, RubricAssessmentResult.model_validate({**r, "llm_request": None}))
                                   for sid, r in data["results"]]
            else:
                data["results"] = [(sid, summary) for sid, summary in data["results"]]
            runs.append(data)
        except Exception as e:  # noqa: BLE001 - one bad file must not hide the rest
            logger.warning("Skipping unreadable saved results %s: %s", path.name, e)
    return runs
