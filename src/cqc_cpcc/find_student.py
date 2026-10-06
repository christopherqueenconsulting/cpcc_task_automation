#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import re
import threading

from cqc_cpcc.my_colleges import MyColleges
from cqc_cpcc.utilities.logger import logger
from cqc_cpcc.utilities.selenium_util import get_session_driver

PHASE_RUNNING = "running"
PHASE_SUCCEEDED = "succeeded"
PHASE_FAILED = "failed"
PHASE_CANCELLED = "cancelled"
FINISHED_PHASES = (PHASE_SUCCEEDED, PHASE_FAILED, PHASE_CANCELLED)


class FindStudents:
    def __init__(self, active_courses_only: bool = True, student_info: dict | None = None):
        """Gather the roster from MyColleges, or search an already gathered one."""
        self._running = True
        self.driver = self.wait = None
        if student_info is None:
            self.driver, self.wait = get_session_driver()
            mc = MyColleges(self.driver, self.wait)
            mc.process_student_info(active_courses_only)
            student_info = mc.get_student_info()
        self.student_info = student_info
        self._running = False

    def is_running(self):
        return self._running

    def get_student_info_items(self):
        return self.student_info.items()

    def get_student_by_email(self, email: str):
        found_students = []

        #  Search through the student_info list for the student with the matching email
        for student_id, (student_name, student_email, course_name) in self.get_student_info_items():
            if student_email == email:
                # append the student info back to found students list
                found_students.append((student_id, student_name, student_email, course_name))

        return found_students

    def get_student_by_student_id(self, id: str):
        found_students = []

        #  Search through the student_info list for the student with the matching student_id
        for student_id, (student_name, student_email, course_name) in self.get_student_info_items():

            if student_id == id:
                # append the student info back to found students list
                found_students.append((student_id, student_name, student_email, course_name))

        return found_students

    def get_student_by_name(self, searchfor_student_name: str):
        found_students = []

        #  Search through the student_info list for the student with the matching student_id
        for student_id, (student_name, student_email, course_name) in self.get_student_info_items():
            # Split the student name by comma and spaces into a list
            student_name_list = re.split(r'[,\s]+', student_name)

            # If at least 2 of the entries from the student_name_list are in the searchfor_student_
            if sum([name in searchfor_student_name for name in student_name_list]) >= 2:
                # append the student info back to found students list
                found_students.append((student_id, student_name, student_email, course_name))

        return found_students

    def terminate(self):
        self._running = False
        if self.driver is not None:
            self.driver.quit()
        logger.debug("Find Students Process Terminated")


class FindStudentJob:
    """Gather every student of the instructor's courses on a background thread.

    The page polls it, so the sign-in's Authenticator number (published to the
    ``MfaBridge`` through ``mfa_handler_scope``) shows on the page while the run
    waits for approval. The thread never touches Streamlit.
    """

    def __init__(self, active_courses_only: bool = True, bridge=None):
        if bridge is None:
            from cqc_cpcc.utilities.selenium_util import MfaBridge
            bridge = MfaBridge()
        self.active_courses_only = active_courses_only
        self.bridge = bridge
        self.finder: FindStudents | None = None
        self.error: str | None = None
        self._phase = PHASE_RUNNING
        self._progress: list[str] = []
        self._lock = threading.Lock()
        self._cancelled = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="find-students")

    def start(self) -> None:
        self._thread.start()

    def cancel(self) -> None:
        self._cancelled.set()
        self.bridge.cancel()

    @property
    def phase(self) -> str:
        with self._lock:
            return self._phase

    def latest_progress(self) -> str | None:
        with self._lock:
            return self._progress[-1] if self._progress else None

    # Browser observer (see ``selenium_util.browser_observer_scope``).
    def on_progress(self, message: str) -> None:
        with self._lock:
            self._progress.append(message)

    def on_wait_retry(self, driver, wait_text: str) -> None:
        self.on_progress("Still %s. Retrying..." % (wait_text.lower() if wait_text else "waiting"))

    def _set_phase(self, phase: str) -> None:
        with self._lock:
            self._phase = phase

    def _run(self) -> None:
        from cqc_cpcc.utilities.selenium_util import (
            browser_observer_scope,
            unattended_browser_problem,
        )
        from cqc_cpcc.utilities.utils import MfaCancelled, mfa_handler_scope

        try:
            # No console on this thread: a browser-choice prompt would hang it.
            problem = unattended_browser_problem()
            if problem:
                raise RuntimeError(problem + " Add it to .env (or the app's secrets).")
            with mfa_handler_scope(self.bridge), browser_observer_scope(self):
                self.on_progress("Signing in to MyColleges and reading your course rosters...")
                driver, wait = get_session_driver()
                try:
                    mc = MyColleges(driver, wait)
                    mc.process_student_info(self.active_courses_only)
                    info = mc.get_student_info()
                finally:
                    try:
                        driver.quit()
                    except Exception:  # noqa: BLE001
                        logger.debug("Driver quit failed.", exc_info=True)
            self.finder = FindStudents(student_info=info)
            self.on_progress("Found %d student(s)." % len(info))
            self._set_phase(PHASE_SUCCEEDED)
        except Exception as error:  # noqa: BLE001 - surfaced to the page
            if isinstance(error, MfaCancelled) or self._cancelled.is_set():
                self._set_phase(PHASE_CANCELLED)
            else:
                self.error = "%s: %s" % (type(error).__name__, error)
                logger.exception("Find Student run failed")
                self._set_phase(PHASE_FAILED)
