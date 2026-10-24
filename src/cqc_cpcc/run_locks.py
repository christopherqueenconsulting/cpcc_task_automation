#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""One lock per grading run, so changes to a run's stored results never interleave.

Every change to a run (the grading job's saves, Reinstate, a re-run merge, "Keep" after an
effort comparison) swaps the run's results and rewrites the whole saved file. Two such
changes at once could lose one of them, so each takes ``run_lock(run_key)`` first.
No Streamlit import: the grading job's worker thread uses it too.
"""

from __future__ import annotations

import threading

_RUN_LOCKS: dict[str, threading.RLock] = {}
_RUN_LOCKS_GUARD = threading.Lock()


def run_lock(run_key: str) -> threading.RLock:
    """The lock for ``run_key`` (re-entrant: a change may call another change)."""
    with _RUN_LOCKS_GUARD:
        return _RUN_LOCKS.setdefault(run_key, threading.RLock())
