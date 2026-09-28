#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""One private, self-cleaning temp directory for everything the app writes.

Downloaded submission ZIPs, extracted student folders, MFA/login screenshots and
generated feedback documents are all written through :mod:`tempfile`. Left in the
shared system temp directory they persist indefinitely and are readable by other
local users. :func:`configure_app_tempdir` points :mod:`tempfile` at
``<system tmp>/cqc_cpcc/`` (mode 0700) and deletes anything in it older than
``CQC_TEMP_RETENTION_HOURS`` (default 24), so every existing call site is covered
without changing each one.

Call it once from each entrypoint (the Streamlit pages' ``init_session_state`` and
``cqc_cpcc.main``), not at import: tests and libraries keep their own temp dirs.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time

from cqc_cpcc.utilities.logger import logger

APP_TEMP_DIRNAME = "cqc_cpcc"
DEFAULT_TEMP_RETENTION_HOURS = 24

_configured_root: str | None = None


def _retention_hours() -> float:
    raw_value = os.getenv("CQC_TEMP_RETENTION_HOURS")
    try:
        return max(0.0, float(raw_value)) if raw_value not in (None, "") else DEFAULT_TEMP_RETENTION_HOURS
    except ValueError:
        return DEFAULT_TEMP_RETENTION_HOURS


def purge_stale(root: str, max_age_hours: float, now: float | None = None) -> int:
    """Delete direct children of ``root`` last modified more than ``max_age_hours`` ago."""
    if max_age_hours <= 0 or not os.path.isdir(root):
        return 0
    cutoff = (now if now is not None else time.time()) - max_age_hours * 3600
    removed = 0
    for name in os.listdir(root):
        path = os.path.join(root, name)
        try:
            if os.path.getmtime(path) >= cutoff:
                continue
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
            removed += 1
        except OSError:
            continue
    return removed


def configure_app_tempdir(base_dir: str | None = None) -> str:
    """Route :mod:`tempfile` into the private app directory and purge stale files.

    Idempotent: later calls return the configured root without purging again.
    """
    global _configured_root
    if _configured_root is not None:
        return _configured_root

    system_tmp = base_dir or tempfile.gettempdir()
    root = os.path.join(system_tmp, APP_TEMP_DIRNAME)
    try:
        os.makedirs(root, mode=0o700, exist_ok=True)
        os.chmod(root, 0o700)
    except OSError as error:
        logger.warning("Could not create private temp directory (%s); using the system default.",
                       type(error).__name__)
        _configured_root = system_tmp
        return _configured_root

    removed = purge_stale(root, _retention_hours())
    if removed:
        logger.info("Removed %d temp item(s) older than %s hour(s).", removed, _retention_hours())

    tempfile.tempdir = root
    _configured_root = root
    return root


def _reset_for_tests() -> None:
    global _configured_root
    _configured_root = None
