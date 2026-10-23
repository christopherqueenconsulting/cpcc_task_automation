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
import re
import tempfile
import time
from pathlib import Path
from typing import Optional

from cqc_cpcc.rubric_models import RubricAssessmentResult
from cqc_cpcc.utilities.logger import logger

MAX_RUNS = 20
MAX_RUN_FILE_BYTES = 20 * 1024 * 1024  # a saved run bigger than this is not read back by a job's save
_RUN_KEY_RE = re.compile(r"[0-9A-Za-z]{1,64}")


_ON = ("1", "true", "on", "yes")
_OFF = ("0", "false", "off", "no")


def persistence_enabled() -> bool:
    """Whether results are kept on disk. Session-only on Streamlit Community Cloud, where
    one shared host would keep every instructor's student results (decision 2026-10-23);
    on the Mac they stay on disk as before.

    ``CQC_RESULTS_PERSIST=on`` or ``off`` decides outright. Unset, an explicit
    ``CQC_GRADING_RESULTS_DIR`` means on, and a Community Cloud host (it mounts the app
    under ``/mount/src``) means off.

    Ruling: decision 3 in the 2026-10-23 decisions taken for Christopher's review (to be recorded
    in docs/REVIEW-2026-10-26.md).
    Any other shared host defaults to ON, so set ``CQC_RESULTS_PERSIST=off`` there: with it on,
    every instructor's student data sits on one disk (see docs/PRIVACY_AND_TELEMETRY.md).
    """
    raw = os.environ.get("CQC_RESULTS_PERSIST", "").strip().lower()
    if raw in _ON:
        return True
    if raw in _OFF:
        return False
    if os.environ.get("CQC_GRADING_RESULTS_DIR"):
        return True
    return not Path("/mount/src").exists()


def results_dir() -> Path:
    return Path(os.environ.get("CQC_GRADING_RESULTS_DIR")
                or Path.home() / ".cqc_cpcc" / "grading_results")


def _path(run_key: str) -> Path:
    # Real run keys are 64 hex characters (SHA-256); the looser ASCII form keeps older saved
    # files loading. Nothing with a separator, a dot or a non-ASCII character gets through.
    if not _RUN_KEY_RE.fullmatch(run_key):
        raise ValueError("run key must be 1-64 ASCII letters or digits")
    return results_dir() / f"{run_key}.json"


def save_run(run_key: str, mode: str, results: list, failures: Optional[list] = None,
             greeting_names: Optional[dict] = None, meta: Optional[dict] = None) -> None:
    """Write one run's results atomically (0600 file in a 0700 folder) and prune old runs.

    ``meta`` holds PII-free facts about how the run was graded (solution given, checklist
    size and hash, prompt version, model). Omitted, the saved file's existing meta is kept.
    Does nothing when :func:`persistence_enabled` is false (session-only hosts).
    """
    if not persistence_enabled():
        return
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
    if not persistence_enabled() or not instructions_hash or not directory.is_dir():
        return None
    for path in sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            meta = json.loads(path.read_text(encoding="utf-8")).get("meta") or {}
        except (OSError, ValueError):
            continue
        if meta.get("instructions_hash") == instructions_hash:
            from cqc_cpcc.utilities.AI.model_registry import KNOWN_EFFORTS
            effort = meta.get("effort")
            # A saved file is not trusted: only a known effort name is handed back.
            return effort if effort in KNOWN_EFFORTS else None
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


def load_run(run_key: str) -> Optional[tuple]:
    """``(results, failures)`` as last saved for ``run_key``, or ``None`` (not saved, off, unreadable)."""
    if not persistence_enabled():
        return None
    try:
        path = _path(run_key)
        if path.stat().st_size > MAX_RUN_FILE_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("mode") == "errors_only":
            return None
        results = [(sid, RubricAssessmentResult.model_validate({**r, "llm_request": None}))
                   for sid, r in data["results"]]
        return results, list(data.get("failures") or [])
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
        return None


def load_runs() -> list[dict]:
    """Every saved run, newest first, with results rebuilt as model objects."""
    directory = results_dir()
    if not persistence_enabled() or not directory.is_dir():
        return []
    runs = []
    for path in sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data.get("run_key"), str) or not _RUN_KEY_RE.fullmatch(data["run_key"]):
                data["run_key"] = path.stem  # the file name decides, not what the file claims
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
