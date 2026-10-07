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

from pydantic import BaseModel, ValidationError

from cqc_cpcc.utilities.logger import logger

DEFAULT_PATH = Path.home() / ".cqc_cpcc" / "app_settings.json"


class AppSettings(BaseModel):
    # Show pages on the deprecation path (currently: Exams (Legacy)).
    show_legacy_pages: bool = False


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
    settings = load_settings().model_copy(update=changes)
    save_settings(AppSettings.model_validate(settings.model_dump()))
    return settings
