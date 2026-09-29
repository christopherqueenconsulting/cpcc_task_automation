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
        self._screenshot_b64: str | None = None
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

    def latest_screenshot(self) -> str | None:
        """The most recent browser screenshot, base64 PNG."""
        with self._lock:
            return self._screenshot_b64

    # -- worker side ------------------------------------------------------------

    def _set_phase(self, phase: str, message: str | None = None) -> None:
        with self._lock:
            self._phase = phase
            if message:
                self._progress.append(message)

    def _record(self, message: str) -> None:
        with self._lock:
            self._progress.append(message)

    def _store_screenshot(self, screenshot_b64: str) -> None:
        with self._lock:
            self._screenshot_b64 = screenshot_b64

    def _open_browser(self):
        """A driver that screenshots each step for the page, and the MyColleges client."""
        from selenium.webdriver.support.event_firing_webdriver import (
            EventFiringWebDriver,
        )

        from cqc_cpcc.my_colleges import MyColleges
        from cqc_cpcc.screenshot_listener import ScreenshotListener
        from cqc_cpcc.utilities.selenium_util import get_session_driver

        raw_driver, wait = get_session_driver()
        driver = EventFiringWebDriver(raw_driver, ScreenshotListener(self._store_screenshot))
        return driver, wait, MyColleges(driver, wait)

    def _raise_if_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise AttendanceJobCancelled()

    def _run(self) -> None:
        from cqc_cpcc.utilities.utils import mfa_handler_scope

        try:
            with mfa_handler_scope(self.bridge):
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
