#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Decide whether a SAVED suite result is fit to be a baseline (docs/PROMPTS.md, "Stale baselines").

A master run that hit an account limit (HTTP 403 "Budget limit exceeded" / 402) saved failed calls as
if they were outputs: scored 0, priced at the worst-case estimate. Every later $0 re-score, the
request-identical path and the "newest prior artifact" fallback then read those rows as master's
baseline. :func:`saved_output_is_unreliable` is the one pure check all of them share. It reads only
fields older code already stored (``ok`` / ``error_kind`` / ``error_detail`` on a call record;
``ok_rate`` / ``errors`` / ``status`` / ``health.failures`` / ``graders`` on a scorecard or history
row), so old rows load unchanged.
"""

from __future__ import annotations

import json
import math
import os
import re
from fractions import Fraction
from pathlib import Path
from typing import Any, Optional

#: A saved suite result whose failed share of attempted calls is ABOVE this is unreliable
#: (exactly this share is still reliable). Calls skipped for the local budget do not count as attempted.
MAX_FAILED_SHARE = Fraction(1, 5)  # 20%
ENV_MAX_FAILED_SHARE = "CQC_BASELINE_MAX_FAILED_SHARE"

#: ``error_kind`` of a call that ended on an account/key spending limit (PR #466); older code stored
#: such a call as ``transport`` with the HTTP status in ``error_detail``.
ACCOUNT_LIMIT_KIND = "account_limit"
_LIMIT_WORDS = ("budget", "limit", "credit", "quota", "spend", "insufficient", "payment required")


class BaselineGuardError(ValueError):
    """A bad override of the failed-share threshold (never silently ignored)."""


def validate_share(value: Any, name: str = "max_failed_share") -> Fraction:
    """``value`` as an exact fraction, or :class:`BaselineGuardError` unless it is at least 0 and below 1."""
    try:
        share = Fraction(str(value).strip()) if not isinstance(value, Fraction) else value
    except (ValueError, ZeroDivisionError):
        raise BaselineGuardError(f"{name}={value!r} is not a number") from None
    if not 0 <= share < 1:
        raise BaselineGuardError(f"{name}={value!r} must be at least 0 and below 1")
    return share


def max_failed_share() -> Fraction:
    """The threshold: ``CQC_BASELINE_MAX_FAILED_SHARE`` (a number from 0 up to, not including, 1) or 20%."""
    raw = os.environ.get(ENV_MAX_FAILED_SHARE)
    if raw is None or not raw.strip():
        return MAX_FAILED_SHARE
    return validate_share(raw, ENV_MAX_FAILED_SHARE)


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _is_account_limit(record: Any) -> bool:
    if _get(record, "error_kind") == ACCOUNT_LIMIT_KIND:
        return True
    for d in _get(record, "error_detail") or []:
        if not isinstance(d, dict):
            continue
        text = " ".join(str(d.get(k) or "") for k in ("message", "code", "type")).lower()
        if ACCOUNT_LIMIT_KIND in text:
            return True
        if d.get("status") == 402:
            return True
        if d.get("status") == 403 and any(w in text for w in _LIMIT_WORDS):
            return True
    return False


def _share_text(failed: int, attempted: int) -> str:
    return f"{failed} of {attempted} calls failed ({failed / attempted:.0%})"


def _from_records(records: list, threshold: Fraction) -> Optional[str]:
    if not records:
        return "no saved calls"
    if any(_is_account_limit(r) for r in records):
        return "a call hit the account or budget limit"
    attempted = [r for r in records if _get(r, "error_kind") != "budget"]
    if not attempted:
        return "no call was attempted (budget stop)"
    failed = sum(1 for r in attempted if not _get(r, "ok", False))
    if Fraction(failed, len(attempted)) > threshold:
        return _share_text(failed, len(attempted))
    return None


_OK_RATE_FAILURE = re.compile(r"\bok_rate\s+(-?\d+(?:\.\d+)?)\b")


def _from_summary(row: dict, threshold: Fraction) -> Optional[str]:
    """A scorecard model entry or a history row (old ones carry no ``unreliable`` field)."""
    if row.get("unreliable"):
        return str(row["unreliable"])
    status = str(row.get("status") or "")
    if status.startswith("aborted"):
        return f"the run ended {status}"
    errors = row.get("errors") or {}
    if isinstance(errors, dict) and errors.get(ACCOUNT_LIMIT_KIND):
        return "a call hit the account or budget limit"
    ok_rate = row.get("ok_rate")
    from_gate_text = ok_rate is None
    if from_gate_text:  # an old history row: the gate text still carries the rate it failed on
        for text in (row.get("health") or {}).get("failures") or []:
            m = _OK_RATE_FAILURE.search(str(text))
            if m:
                ok_rate = m.group(1)
                break
    if ok_rate is not None:
        try:
            rate = float(ok_rate)
            if isinstance(ok_rate, bool) or not math.isfinite(rate):
                raise ValueError(ok_rate)
            share = Fraction(str(round(1 - rate, 6)))
        except (ValueError, TypeError):
            return "ok_rate unreadable"  # unknown reliability is treated as unreliable
        if share > threshold:
            return f"{share:.0%} of calls failed (ok rate {rate:.3f})"
        return None
    graders = row.get("graders") or {}
    values = [v for v in graders.values() if isinstance(v, (int, float))]
    if values and row.get("composite") == 0 and all(v == 0 for v in values):
        return "every grader scored 0 (failed calls)"
    return None


def saved_output_is_unreliable(saved: Any, threshold: Optional[Fraction] = None) -> Optional[str]:
    """A reason when ``saved`` must not serve as a baseline, else ``None``.

    ``saved`` is any of: a list of call records (objects or dicts), ``{model label: [records]}``, a
    scorecard (``{"models": {...}}``), one model's scorecard entry, or a history row. Unreliable when
    more than the threshold (20%) of the attempted calls failed, or any call hit the account/budget
    limit (``error_kind`` ``account_limit``, or HTTP 402 / a 403 naming a limit in ``error_detail``).
    """
    threshold = max_failed_share() if threshold is None else validate_share(threshold)
    if isinstance(saved, (list, tuple)):
        return _from_records(list(saved), threshold)
    if isinstance(saved, dict):
        if saved and all(isinstance(v, (list, tuple)) for v in saved.values()):
            return next((f"{label}: {why}" for label, recs in saved.items()
                         if (why := _from_records(list(recs), threshold))), None)
        if isinstance(saved.get("models"), dict):
            if str(saved.get("status") or "").startswith("aborted"):
                return f"the run ended {saved['status']}"
            return next((f"{label}: {why}" for label, m in saved["models"].items()
                         if (why := _from_summary(m, threshold))), None)
        return _from_summary(saved, threshold)
    return None


def raw_outputs_unreliable(path: Path, threshold: Optional[Fraction] = None) -> Optional[str]:
    """:func:`saved_output_is_unreliable` for a ``raw_outputs.jsonl`` file (one JSON record per line)."""
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                records.append(json.loads(line))
            except ValueError:
                return "an unreadable record"
    return saved_output_is_unreliable(records, threshold)


def suite_dir_under(master_dir: Path, suite_id: str, known: Any) -> Optional[Path]:
    """``master_dir/suite_id`` when ``suite_id`` is a known suite and the resolved path stays inside
    ``master_dir``; else ``None`` (a name such as ``../x`` or an unknown suite is never touched)."""
    if suite_id not in known:
        return None
    root = Path(master_dir).resolve()
    target = (root / suite_id).resolve()
    return target if target.parent == root else None


def pick_baseline(suite_id: str, raw_dirs: list) -> tuple:
    """``(raw_outputs path or None, [notes])``: the newest ``raw_dirs`` entry (newest first) whose saved
    outputs of ``suite_id`` are reliable. ``None`` means "no baseline" (the suite needs a model run)."""
    notes = []
    for directory in raw_dirs:
        raw = Path(directory) / suite_id / "raw_outputs.jsonl"
        if not raw.exists():
            continue
        why = raw_outputs_unreliable(raw)
        if why is None:
            return raw, notes
        notes.append(f"skipped {directory}: unreliable saved outputs ({why})")
    return None, notes
