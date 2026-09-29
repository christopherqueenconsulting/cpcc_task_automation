#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""The web attendance run: sign in, wait for the form's plan, then take attendance."""

import datetime as DT
import threading
from unittest.mock import MagicMock, patch

import pytest

from cqc_cpcc.attendance_job import (
    PHASE_AWAITING_PLAN,
    PHASE_CANCELLED,
    PHASE_FAILED,
    PHASE_RUNNING,
    PHASE_SUCCEEDED,
    AttendanceJob,
)
from cqc_cpcc.run_plan import RunPlan
from cqc_cpcc.utilities.selenium_util import MfaBridge
from cqc_cpcc.utilities.utils import MfaCancelled, current_mfa_handler

COURSES = {
    "url-a": {"name": "CSC-151-N855", "start_date": DT.datetime(2026, 8, 17),
              "end_date": DT.datetime(2026, 12, 11)},
}


def _wait_for(job, phase, timeout=5):
    deadline = threading.Event()
    for _ in range(int(timeout / 0.02)):
        if job.phase == phase:
            return
        deadline.wait(0.02)
    raise AssertionError("job stayed in %s, expected %s" % (job.phase, phase))


@pytest.fixture
def browser(monkeypatch):
    driver, wait, mc = MagicMock(), MagicMock(), MagicMock()
    seen = {}

    def get_course_info():
        # Logins inside the run must see the job's bridge without it being passed.
        seen["handler"] = current_mfa_handler()
        mc.course_information = dict(COURSES)

    mc.get_course_info.side_effect = get_course_info
    monkeypatch.setattr(AttendanceJob, "_open_browser", lambda self: (driver, wait, mc))
    return driver, wait, mc, seen


@pytest.mark.unit
class TestAttendanceJob:
    def test_waits_for_the_plan_then_runs_it(self, browser):
        driver, wait, mc, seen = browser
        job = AttendanceJob(tracker_url="https://tracker")
        with patch("cqc_cpcc.attendance.run_attendance_plan", return_value=["c1"]) as run:
            job.start()
            _wait_for(job, PHASE_AWAITING_PLAN)
            assert job.course_information == COURSES
            assert seen["handler"] is job.bridge
            run.assert_not_called()

            plan = RunPlan(course_urls=["url-a"])
            job.submit_plan(plan)
            assert job.phase == PHASE_RUNNING
            assert job.done.wait(5)

        assert job.phase == PHASE_SUCCEEDED
        run.assert_called_once_with(driver, wait, mc, plan)
        assert job.courses_processed == 1
        driver.quit.assert_called_once()
        # The scope ends with the run.
        assert current_mfa_handler() is None

    def test_submit_plan_outside_the_form_step_is_refused(self):
        with pytest.raises(RuntimeError):
            AttendanceJob(tracker_url=None).submit_plan(RunPlan())

    def test_cancel_at_the_form_stops_without_running(self, browser):
        driver, *_ = browser
        job = AttendanceJob(tracker_url=None)
        with patch("cqc_cpcc.attendance.run_attendance_plan") as run:
            job.start()
            _wait_for(job, PHASE_AWAITING_PLAN)
            job.cancel()
            assert job.done.wait(5)
        assert job.phase == PHASE_CANCELLED
        assert job.bridge.cancelled
        run.assert_not_called()
        driver.quit.assert_called_once()

    def test_cancelled_mfa_reads_as_cancelled(self, browser):
        _driver, _wait, mc, _seen = browser
        mc.get_course_info.side_effect = MfaCancelled("cancelled")
        job = AttendanceJob(tracker_url=None)
        job.start()
        assert job.done.wait(5)
        assert job.phase == PHASE_CANCELLED
        assert job.error is None

    def test_failure_is_reported(self, browser):
        _driver, _wait, mc, _seen = browser
        mc.get_course_info.side_effect = RuntimeError("faculty page never loaded")
        job = AttendanceJob(tracker_url=None)
        job.start()
        assert job.done.wait(5)
        assert job.phase == PHASE_FAILED
        assert "faculty page never loaded" in job.error

    def test_empty_plan_finishes_without_running(self, browser):
        job = AttendanceJob(tracker_url=None)
        with patch("cqc_cpcc.attendance.run_attendance_plan") as run:
            job.start()
            _wait_for(job, PHASE_AWAITING_PLAN)
            job.submit_plan(RunPlan(course_urls=[]))
            assert job.done.wait(5)
        assert job.phase == PHASE_SUCCEEDED
        run.assert_not_called()

    def test_screenshots_are_kept_for_the_page(self):
        job = AttendanceJob(tracker_url=None, bridge=MfaBridge())
        job._store_screenshot("abc")
        assert job.latest_screenshot() == "abc"
