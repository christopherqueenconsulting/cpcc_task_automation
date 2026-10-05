#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Attendance run for the web app: the console flow, with the prompts moved to a form.

The console run logs in, lists the courses, asks its questions, then works through
the plan. :class:`AttendanceJob` does the same on a background thread, pausing
after the course list is read (``PHASE_AWAITING_PLAN``) until the page submits a
:class:`~cqc_cpcc.run_plan.RunPlan` built from the instructor's answers.

The thread never touches Streamlit. It writes plain attributes and the thread-safe
``MfaBridge``, and the page polls them. Every login on the thread (MyColleges,
BrightSpace, the tracker) publishes its MFA number to the bridge through
``mfa_handler_scope``, so the number shows on the page instead of a desktop
screenshot nobody on the web can see.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from cqc_cpcc.run_plan import RunPlan
from cqc_cpcc.utilities.AI import posthog_telemetry as telemetry
from cqc_cpcc.utilities.logger import logger

PHASE_STARTING = "starting"
PHASE_AWAITING_PLAN = "awaiting_plan"
PHASE_RUNNING = "running"
PHASE_SUCCEEDED = "succeeded"
PHASE_FAILED = "failed"
PHASE_CANCELLED = "cancelled"

FINISHED_PHASES = (PHASE_SUCCEEDED, PHASE_FAILED, PHASE_CANCELLED)

# Seconds between event screenshots; the page refreshes about this often anyway.
SCREENSHOT_MIN_INTERVAL = 0.75


@dataclass(frozen=True)
class TabScreenshot:
    """The latest screenshot of one open browser tab."""

    handle: str
    title: str
    screenshot_b64: str
    active: bool = False


class AttendanceJobCancelled(BaseException):
    """The instructor cancelled the run from the page.

    A ``BaseException`` so run telemetry records it as interrupted, not failed.
    """


class AttendanceJob:
    """Log in, list courses, wait for the form's plan, then take attendance."""

    def __init__(self, tracker_url: str | None, bridge=None):
        if bridge is None:
            from cqc_cpcc.utilities.selenium_util import MfaBridge
            bridge = MfaBridge()
        self.tracker_url = tracker_url
        self.bridge = bridge
        self.course_information: dict = {}
        self.plan: RunPlan | None = None
        self.courses_processed = 0
        self.error: str | None = None
        self.done = threading.Event()
        self._phase = PHASE_STARTING
        self._progress: list[str] = []
        self._warnings: list[str] = []
        self._screenshot_b64: str | None = None
        # handle -> (title, screenshot); insertion order is the order tabs opened.
        self._tabs: dict[str, tuple[str, str]] = {}
        self._active_handle: str | None = None
        self._lock = threading.Lock()
        self._plan_ready = threading.Event()
        self._cancelled = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="attendance-job")

    # -- UI side ----------------------------------------------------------------

    def start(self) -> None:
        self._thread.start()

    def submit_plan(self, plan: RunPlan) -> None:
        """Hand the form's answers to the waiting run."""
        if self.phase != PHASE_AWAITING_PLAN:
            raise RuntimeError("The attendance run is not waiting for a plan.")
        self.plan = plan
        # Leave the form at once; the worker confirms with its own progress message.
        self._set_phase(PHASE_RUNNING)
        self._plan_ready.set()

    def cancel(self) -> None:
        self._cancelled.set()
        self.bridge.cancel()
        self._plan_ready.set()

    @property
    def phase(self) -> str:
        with self._lock:
            return self._phase

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def latest_progress(self) -> str | None:
        with self._lock:
            return self._progress[-1] if self._progress else None

    def warnings(self) -> list[str]:
        """Problems the instructor should act on (for example, a course to re-run)."""
        with self._lock:
            return list(self._warnings)

    def latest_screenshot(self) -> str | None:
        """The most recent browser screenshot, base64 PNG."""
        with self._lock:
            return self._screenshot_b64

    def tab_screenshots(self) -> list[TabScreenshot]:
        """The last screenshot of each open browser tab, in the order they opened."""
        with self._lock:
            return [
                TabScreenshot(handle, title, shot, active=handle == self._active_handle)
                for handle, (title, shot) in self._tabs.items()
            ]

    # -- worker side ------------------------------------------------------------

    def _set_phase(self, phase: str, message: str | None = None) -> None:
        with self._lock:
            self._phase = phase
            if message:
                self._progress.append(message)

    def _record(self, message: str) -> None:
        with self._lock:
            self._progress.append(message)

    def _store_screenshot(self, screenshot_b64: str, handle: str | None = None,
                          title: str = "", open_handles=None) -> None:
        """Keep the newest screenshot, and the newest one per tab when the tab is known.

        ``open_handles`` (the driver's current tabs) drops tabs that have closed.
        """
        with self._lock:
            self._screenshot_b64 = screenshot_b64
            if handle is None:
                return
            self._tabs[handle] = (title, screenshot_b64)
            self._active_handle = handle
            if open_handles is not None:
                self._prune_tabs(open_handles)

    def _prune_tabs(self, open_handles) -> None:
        open_handles = set(open_handles)
        for closed in [handle for handle in self._tabs if handle not in open_handles]:
            del self._tabs[closed]
        if self._active_handle not in self._tabs:
            self._active_handle = None

    def _forget_closed_tabs(self, driver) -> None:
        try:
            open_handles = driver.window_handles
        except Exception:  # noqa: BLE001 - the browser may be gone
            return
        with self._lock:
            self._prune_tabs(open_handles)

    def _capture(self, driver, screenshot_b64: str | None = None) -> None:
        """Screenshot the current tab (unless given one) and file it under that tab."""
        try:
            if screenshot_b64 is None:
                screenshot_b64 = driver.get_screenshot_as_base64()
            if not screenshot_b64:
                return
        except Exception:  # noqa: BLE001 - screenshots are best-effort
            logger.debug("Screenshot failed.", exc_info=True)
            return
        handle = title = open_handles = None
        try:
            handle = driver.current_window_handle
            title = driver.title or ""
            open_handles = driver.window_handles
        except Exception:  # noqa: BLE001
            logger.debug("Could not read the tab for a screenshot.", exc_info=True)
        self._store_screenshot(screenshot_b64, handle, title or "", open_handles)

    # Browser observer (see ``selenium_util.browser_observer_scope``).

    def on_wait_retry(self, driver, wait_text: str) -> None:
        self._record("Still %s. Retrying..." % (wait_text.lower() if wait_text else "waiting"))
        self._capture(driver)

    def on_progress(self, message: str) -> None:
        self._record(message)

    def on_warning(self, message: str) -> None:
        with self._lock:
            self._warnings.append(message)

    def _open_browser(self):
        """A driver that screenshots each step for the page, and the MyColleges client."""
        from selenium.webdriver.support.event_firing_webdriver import (
            EventFiringWebDriver,
        )

        from cqc_cpcc.my_colleges import MyColleges
        from cqc_cpcc.screenshot_listener import ScreenshotListener
        from cqc_cpcc.utilities.selenium_util import (
            get_session_driver,
            unattended_browser_problem,
        )

        # This thread has no console: a browser-choice prompt would hang the run
        # (and the page would poll forever), so fail with the setting to add.
        problem = unattended_browser_problem()
        if problem:
            raise RuntimeError(problem + " Add it to .env (or the app's secrets).")

        raw_driver, wait = get_session_driver()
        listener = ScreenshotListener(
            lambda screenshot: self._capture(raw_driver, screenshot),
            on_tab_closed=lambda: self._forget_closed_tabs(raw_driver),
            min_interval=SCREENSHOT_MIN_INTERVAL,
        )
        driver = EventFiringWebDriver(raw_driver, listener)
        return driver, wait, MyColleges(driver, wait)

    def _raise_if_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise AttendanceJobCancelled()

    def _run(self) -> None:
        from cqc_cpcc.utilities.selenium_util import browser_observer_scope
        from cqc_cpcc.utilities.utils import mfa_handler_scope

        try:
            with mfa_handler_scope(self.bridge), browser_observer_scope(self):
                _run_tracked(self)
            self._set_phase(PHASE_SUCCEEDED, "Finished attendance.")
        except AttendanceJobCancelled:
            self._set_phase(PHASE_CANCELLED, "Cancelled.")
            logger.info("Attendance run cancelled from the page.")
        except Exception as error:  # noqa: BLE001 - surfaced to the page
            from cqc_cpcc.utilities.utils import MfaCancelled

            if isinstance(error, MfaCancelled) or self._cancelled.is_set():
                self._set_phase(PHASE_CANCELLED, "Cancelled.")
            else:
                self.error = "%s: %s" % (type(error).__name__, error)
                self._set_phase(PHASE_FAILED, "Attendance failed.")
                logger.exception("Attendance run failed")
        finally:
            self.done.set()

    def _work(self) -> None:
        from cqc_cpcc.attendance import run_attendance_plan

        self._record("Opening the browser...")
        driver, wait, mc = self._open_browser()
        try:
            self._record("Signing in to MyColleges and reading your courses...")
            mc.get_course_info()
            self._raise_if_cancelled()
            self.course_information = dict(mc.course_information)

            self._set_phase(PHASE_AWAITING_PLAN, "Choose courses and a start date to continue.")
            self._plan_ready.wait()
            self._raise_if_cancelled()

            plan = self.plan
            if plan is None or not plan.course_urls:
                self._record("No courses selected. Nothing to process.")
                return

            self._set_phase(PHASE_RUNNING, "Taking attendance for %d course(s)..." % len(plan.course_urls))
            bs_courses = run_attendance_plan(driver, wait, mc, plan)
            self.courses_processed = len(bs_courses or [])
        finally:
            try:
                driver.quit()
            except Exception as error:  # noqa: BLE001
                logger.debug("Driver quit failed: %s", type(error).__name__)


@telemetry.tracked_run("attendance", surface="streamlit")
def _run_tracked(job: AttendanceJob) -> None:
    job._work()
