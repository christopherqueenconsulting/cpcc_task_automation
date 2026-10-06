#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""The only code that edits ``config/model_registry.json`` automatically.

It changes only the roles listed in ``model_policy.json`` ``auto_promote_roles``, the
model profiles, ``previous``, ``promotion`` and ``revision``. Output is validated by the
same schema the app loads, and written with ``json.dump`` only.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Optional

from cqc_cpcc.utilities.AI.model_registry import (
    MODEL_ID_PATTERN,
    ModelProfile,
    RegistryFile,
    load_policy,
)


def next_revision(current: str, today: Optional[dt.date] = None) -> str:
    today = (today or dt.date.today()).isoformat()
    day, _, n = current.partition(".")
    return f"{today}.{int(n) + 1 if day == today and n.isdigit() else 1}"


def _write(path: Path, data: dict) -> RegistryFile:
    parsed = RegistryFile.model_validate(data)  # refuse to write anything the app can't load
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return parsed


def promote(path: Path, model: str, effort: Optional[str], profile: ModelProfile, run_id: str,
            roles: Optional[list[str]] = None, today: Optional[dt.date] = None) -> RegistryFile:
    if not MODEL_ID_PATTERN.match(model):
        raise ValueError(f"invalid model id {model!r}")
    roles = roles or load_policy().auto_promote_roles
    data = json.loads(path.read_text(encoding="utf-8"))
    data["models"][model] = json.loads(profile.model_dump_json())
    for role in roles:
        cfg = data["roles"][role]
        if cfg["model"] != model:
            data["previous"][role] = cfg["model"]
        cfg["model"] = model
        if role == "grading":
            cfg["reasoning_effort"] = effort
        elif cfg.get("reasoning_effort") not in profile.reasoning_efforts:
            cfg["reasoning_effort"] = None
        cfg["max_output_tokens"] = min(cfg["max_output_tokens"], profile.max_completion_tokens)
        if cfg.get("fallback") == model:
            cfg["fallback"] = data["previous"].get(role)
    today = today or dt.date.today()
    data["revision"] = next_revision(data["revision"], today)
    data["promotion"] = {"last_promoted_month": today.strftime("%Y-%m"), "last_report_run_id": str(run_id)}
    return _write(path, data)


def rollback(path: Path, roles: Optional[list[str]] = None, today: Optional[dt.date] = None) -> RegistryFile:
    """Swap each role back to its ``previous`` model (and remember the one rolled back)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    roles = roles or list(data["previous"])
    changed = False
    for role in roles:
        prior = data["previous"].get(role)
        if not prior:
            continue
        cfg = data["roles"][role]
        cfg["model"], data["previous"][role] = prior, cfg["model"]
        effort = cfg.get("reasoning_effort")
        if effort and effort not in data["models"][prior].get("reasoning_efforts", []):
            cfg["reasoning_effort"] = None
        cfg["max_output_tokens"] = min(cfg["max_output_tokens"], data["models"][prior]["max_completion_tokens"])
        changed = True
    if not changed:
        raise ValueError("nothing to roll back: no previous model recorded for those roles")
    data["revision"] = next_revision(data["revision"], today)
    return _write(path, data)
