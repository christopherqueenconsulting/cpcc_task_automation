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
        assert job.tab_screenshots() == []


def _tab_driver(handle, title, handles, shot="png"):
    driver = MagicMock()
    driver.current_window_handle = handle
    driver.title = title
    driver.window_handles = handles
    driver.get_screenshot_as_base64.return_value = shot
    return driver


@pytest.mark.unit
class TestTabScreenshots:
    def test_each_tab_keeps_its_latest_screenshot_in_open_order(self):
        job = AttendanceJob(tracker_url=None, bridge=MfaBridge())
        job._capture(_tab_driver("faculty", "Faculty", ["faculty"], "f1"))
        job._capture(_tab_driver("course", "Course", ["faculty", "course"], "c1"))
        job._capture(_tab_driver("faculty", "Faculty", ["faculty", "course"], "f2"))

        tabs = job.tab_screenshots()
        assert [(t.handle, t.screenshot_b64, t.active) for t in tabs] == [
            ("faculty", "f2", True), ("course", "c1", False)]
        assert job.latest_screenshot() == "f2"

    def test_closed_tabs_are_dropped(self):
        job = AttendanceJob(tracker_url=None, bridge=MfaBridge())
        job._capture(_tab_driver("faculty", "Faculty", ["faculty"]))
        job._capture(_tab_driver("course", "Course", ["faculty", "course"]))
        job._forget_closed_tabs(_tab_driver("faculty", "Faculty", ["faculty"]))
        assert [t.handle for t in job.tab_screenshots()] == ["faculty"]
        assert not any(t.active for t in job.tab_screenshots())

    def test_given_screenshot_is_used_without_taking_another(self):
        job = AttendanceJob(tracker_url=None, bridge=MfaBridge())
        driver = _tab_driver("faculty", "Faculty", ["faculty"])
        job._capture(driver, "given")
        driver.get_screenshot_as_base64.assert_not_called()
        assert job.latest_screenshot() == "given"

    def test_screenshot_failure_is_ignored(self):
        job = AttendanceJob(tracker_url=None, bridge=MfaBridge())
        driver = _tab_driver("faculty", "Faculty", ["faculty"])
        driver.get_screenshot_as_base64.side_effect = RuntimeError("tab crashed")
        job._capture(driver)
        assert job.latest_screenshot() is None

    def test_unreadable_tab_still_keeps_the_screenshot(self):
        job = AttendanceJob(tracker_url=None, bridge=MfaBridge())
        driver = MagicMock()
        driver.get_screenshot_as_base64.return_value = "shot"
        type(driver).current_window_handle = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("no window")))
        job._capture(driver)
        assert job.latest_screenshot() == "shot"
        assert job.tab_screenshots() == []

    def test_forget_closed_tabs_tolerates_a_dead_browser(self):
        job = AttendanceJob(tracker_url=None, bridge=MfaBridge())
        driver = MagicMock()
        type(driver).window_handles = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("gone")))
        job._forget_closed_tabs(driver)

    def test_wait_retry_screenshots_and_says_what_it_waits_for(self):
        job = AttendanceJob(tracker_url=None, bridge=MfaBridge())
        job.on_wait_retry(_tab_driver("course", "Course", ["course"], "stuck"),
                          "Waiting for Deadline Dates")
        assert job.latest_progress() == "Still waiting for deadline dates. Retrying..."
        assert job.latest_screenshot() == "stuck"
        job.on_progress("Course 1 of 2: CSC-134")
        assert job.latest_progress() == "Course 1 of 2: CSC-134"

    def test_run_sets_the_browser_observer(self, browser):
        from cqc_cpcc.utilities.selenium_util import current_browser_observer

        _driver, _wait, mc, seen = browser
        mc.get_course_info.side_effect = lambda: seen.setdefault(
            "observer", current_browser_observer())
        job = AttendanceJob(tracker_url=None)
        job.start()
        _wait_for(job, PHASE_AWAITING_PLAN)
        job.cancel()
        assert job.done.wait(5)
        assert seen["observer"] is job
        assert current_browser_observer() is None


@pytest.mark.unit
def test_missing_browser_setting_fails_fast_instead_of_prompting(monkeypatch):
    from cqc_cpcc.utilities import selenium_util

    monkeypatch.setattr(selenium_util, "unattended_browser_problem",
                        lambda: "BROWSER_TYPE is not set")
    monkeypatch.setattr(selenium_util, "get_session_driver",
                        lambda: pytest.fail("must not open a browser"))
    job = AttendanceJob(tracker_url=None)
    job.start()
    assert job.done.wait(5)
    assert job.phase == PHASE_FAILED
    assert "BROWSER_TYPE is not set" in job.error


@pytest.mark.unit
class TestUnattendedBrowserProblem:
    @pytest.fixture(autouse=True)
    def plain_env(self, monkeypatch):
        from cqc_cpcc.utilities import selenium_util as su

        for name in ("IS_GITHUB_ACTION", "HEADLESS_BROWSER", "USE_VIRTUAL_DISPLAY"):
            monkeypatch.setattr(su, name, False)
        self.su = su

    @pytest.mark.parametrize(
        "browser, docker, expect",
        [
            (None, None, "BROWSER_TYPE"),
            ("DOCKER_CHROME", None, "DOCKER_TYPE is not set"),
            ("DOCKER_CHROME", "REMOTE", "DOCKER_TYPE=REMOTE"),
            ("DOCKER_CHROME", "LOCAL", None),
            ("LOCAL_CHROME", None, None),
            ("BROWSERLESS", None, None),
        ],
    )
    def test_settings(self, monkeypatch, browser, docker, expect):
        monkeypatch.setattr(self.su, "BROWSER_TYPE", browser)
        monkeypatch.setattr(self.su, "DOCKER_TYPE", docker)
        problem = self.su.unattended_browser_problem()
        if expect is None:
            assert problem is None
        else:
            assert expect in problem

    def test_headless_never_prompts(self, monkeypatch):
        monkeypatch.setattr(self.su, "BROWSER_TYPE", None)
        monkeypatch.setattr(self.su, "HEADLESS_BROWSER", True)
        assert self.su.unattended_browser_problem() is None


@pytest.mark.unit
def test_open_browser_wraps_the_driver_for_screenshots(monkeypatch):
    from cqc_cpcc.utilities import selenium_util

    raw_driver, wait = MagicMock(), MagicMock()
    monkeypatch.setattr(selenium_util, "unattended_browser_problem", lambda: None)
    monkeypatch.setattr(selenium_util, "get_session_driver", lambda: (raw_driver, wait))
    job = AttendanceJob(tracker_url=None)
    with patch("selenium.webdriver.support.event_firing_webdriver.EventFiringWebDriver") as efd, \
            patch("cqc_cpcc.my_colleges.MyColleges") as mc:
        driver, got_wait, got_mc = job._open_browser()
    assert got_wait is wait
    assert driver is efd.return_value
    assert efd.call_args.args[0] is raw_driver
    mc.assert_called_once_with(efd.return_value, wait)
    assert got_mc is mc.return_value

    # The listener files each event screenshot under the tab it came from.
    listener = efd.call_args.args[1]
    raw_driver.current_window_handle = "tab-1"
    raw_driver.title = "Faculty"
    raw_driver.window_handles = ["tab-1"]
    listener.screenshot_holder("shot")
    assert [(t.handle, t.title, t.screenshot_b64) for t in job.tab_screenshots()] == [
        ("tab-1", "Faculty", "shot")]
    raw_driver.window_handles = []
    listener.after_close(raw_driver)
    assert job.tab_screenshots() == []


@pytest.mark.unit
def test_warnings_are_kept_in_order_for_the_page():
    job = AttendanceJob(tracker_url=None, bridge=MfaBridge())
    assert job.warnings() == []
    job.on_warning("first")
    job.on_warning("second")
    assert job.warnings() == ["first", "second"]
