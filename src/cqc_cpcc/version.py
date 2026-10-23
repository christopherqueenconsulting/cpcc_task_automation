#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""The version of this code, for stamping on grading output ("which code graded this?").

``pyproject.toml`` is the single source of truth. An installed package reports it through
``importlib.metadata``. A source checkout or a Streamlit Community Cloud deploy may not have
the package installed, so the fallback reads ``pyproject.toml`` next to ``src/``. If neither
works the version is ``"unknown"``: stamping must never stop a grading run.

The version is deliberately not part of the grading run key (``grading_run_key.py``): the
key caches results, and a release would invalidate every cached result.
"""

from __future__ import annotations

import tomllib
from functools import lru_cache
from importlib import metadata
from pathlib import Path

PACKAGE_NAME = "cpcc-task-automation"
UNKNOWN = "unknown"
_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _from_metadata() -> str | None:
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return None


def _from_pyproject() -> str | None:
    try:
        data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
        poetry = data["tool"]["poetry"]
        return str(poetry["version"]) if poetry["name"] == PACKAGE_NAME else None
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return None


@lru_cache(maxsize=1)
def get_version() -> str:
    """The code version, e.g. ``0.1.2`` or ``0.2.0-rc1``; ``"unknown"`` when it cannot be read.

    The checkout's ``pyproject.toml`` wins over installed metadata: after a ``git pull`` on an
    editable install the metadata is stale until the next ``poetry install``.
    """
    return _from_pyproject() or _from_metadata() or UNKNOWN
