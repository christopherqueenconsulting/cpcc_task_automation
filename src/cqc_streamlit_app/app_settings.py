#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Local app preferences that survive restarts and ``git pull``.

Stored outside the repository at ``~/.cqc_cpcc/app_settings.json`` (the same folder as
the attendance ledger and the PII alias key), or at ``CQC_APP_SETTINGS_PATH``. Holds
preferences only, never credentials: keys stay in ``.env`` / the Settings page.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ValidationError

from cqc_cpcc.utilities.logger import logger

DEFAULT_PATH = Path.home() / ".cqc_cpcc" / "app_settings.json"


class AppSettings(BaseModel):
    # Show pages on the deprecation path (currently: Exams (Legacy)).
    show_legacy_pages: bool = False
    # Grade assignment: last choices, so a weekly re-run starts pre-filled.
    last_grading_mode: Optional[str] = None
    last_course_id: Optional[str] = None
    last_rubric_id: Optional[str] = None
    last_assignment_id: Optional[str] = None
    # Take attendance: courses chosen last time (MyColleges course URLs).
    last_attendance_courses: list[str] = []


def remember(**choices) -> None:
    """Save changed last-used choices; a no-op when nothing changed (no disk write)."""
    current = load_settings()
    changed = {k: v for k, v in choices.items() if getattr(current, k) != v}
    if changed:
        try:
            update_settings(**changed)
        except OSError as e:  # remembering is a convenience; never break grading over it
            logger.warning("Could not save last-used choices: %s", e)


def settings_path() -> Path:
    return Path(os.environ.get("CQC_APP_SETTINGS_PATH") or DEFAULT_PATH)


def load_settings() -> AppSettings:
    """Read the settings file; a missing or unreadable file gives the defaults."""
    path = settings_path()
    try:
        return AppSettings.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return AppSettings()
    except (OSError, ValidationError, ValueError) as e:
        logger.warning("Ignoring unreadable app settings at %s: %s", path, e)
        return AppSettings()


def save_settings(settings: AppSettings) -> None:
    """Write atomically (temp file + rename), owner read/write only."""
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".app_settings.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings.model_dump(), f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def update_settings(**changes) -> AppSettings:
    """Load, apply ``changes``, save and return the new settings."""
    settings = AppSettings.model_validate({**load_settings().model_dump(), **changes})
    save_settings(settings)
    return settings
