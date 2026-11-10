#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""A pause switch for the browser worker thread.

The instructor pauses a run from the page to look closely at the browser. The run
lives on its own thread and keeps no Streamlit state, so pausing cannot interrupt it
mid-command. Instead the thread calls :meth:`PauseGate.wait_if_paused` before each
browser command (from the ``ScreenshotListener`` ``before_*`` hooks) and blocks there
until the page resumes it.

Selenium Grid deletes a session that sends no command for ``SE_NODE_SESSION_TIMEOUT``
seconds (600 in docker-compose.yml). While blocked, the gate therefore calls a
``keepalive`` (a cheap read-only command) on the worker thread every
``keepalive_interval`` seconds, so the session survives the pause.

A keepalive also defeats that timeout as a safety net: a pause whose tab was closed
could hold the grid's only browser slot forever. So a pause ends by itself after
``max_pause`` seconds (30 minutes): the run carries on, as it would have before
Pause existed.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from cqc_cpcc.utilities.logger import logger

KEEPALIVE_INTERVAL_SECONDS = 60.0
POLL_INTERVAL_SECONDS = 0.5
MAX_PAUSE_SECONDS = 30 * 60.0


class PauseGate:
    """Lets the page pause and resume the thread that calls :meth:`wait_if_paused`."""

    def __init__(
        self,
        keepalive: Callable[[], object] | None = None,
        keepalive_interval: float = KEEPALIVE_INTERVAL_SECONDS,
        raise_if_cancelled: Callable[[], None] | None = None,
        *,
        max_pause: float | None = MAX_PAUSE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        poll_interval: float = POLL_INTERVAL_SECONDS,
    ):
        self.keepalive = keepalive
        self.keepalive_interval = keepalive_interval
        self._raise_if_cancelled = raise_if_cancelled
        self.max_pause = max_pause
        self._clock = clock
        self._wall_clock = wall_clock
        self._poll_interval = poll_interval
        self._lock = threading.Lock()
        # Set while running; cleared while paused, so ``wait`` blocks exactly then.
        self._running = threading.Event()
        self._running.set()
        self._paused_since: float | None = None
        self._paused_at: float | None = None  # monotonic, for the cap
        self.keepalive_count = 0

    # -- page side --------------------------------------------------------------

    def pause(self) -> None:
        """Hold the worker before its next browser command. Pausing twice keeps the first time."""
        with self._lock:
            if self._paused_since is None:
                self._paused_since = self._wall_clock()
                self._paused_at = self._clock()
            self._running.clear()

    def resume(self) -> None:
        with self._lock:
            self._paused_since = None
            self._paused_at = None
            self._running.set()

    @property
    def paused(self) -> bool:
        return not self._running.is_set()

    @property
    def paused_since(self) -> float | None:
        """Epoch seconds when the current pause began, or ``None`` when running."""
        with self._lock:
            return self._paused_since

    # -- worker side ------------------------------------------------------------

    def wait_if_paused(self) -> None:
        """Return at once when running; otherwise block until resumed.

        Raises whatever ``raise_if_cancelled`` raises, so cancelling a paused run
        ends the wait instead of leaving the thread stuck. A pause older than
        ``max_pause`` ends here too, with a warning.
        """
        if self._running.is_set():
            return
        next_keepalive = self._clock() + self.keepalive_interval
        while True:
            if self._raise_if_cancelled is not None:
                self._raise_if_cancelled()
            if self._running.is_set():
                return
            if self._pause_expired():
                logger.warning("Attendance run was paused for over %d minutes; resuming it so "
                               "the browser is not held indefinitely.", int(self.max_pause // 60))
                self.resume()
                return
            self._running.wait(self._poll_interval)
            now = self._clock()
            if not self._running.is_set() and now >= next_keepalive:
                self._send_keepalive()
                next_keepalive = now + self.keepalive_interval

    def _pause_expired(self) -> bool:
        with self._lock:
            return (self.max_pause is not None and self._paused_at is not None
                    and self._clock() - self._paused_at >= self.max_pause)

    def _send_keepalive(self) -> None:
        if self.keepalive is None:
            return
        try:
            self.keepalive()
            self.keepalive_count += 1
        except Exception as error:  # noqa: BLE001 - a dead browser surfaces on the next real command
            logger.warning("Keepalive command failed while the run is paused (%s); the browser "
                           "session may have been lost.", type(error).__name__)
