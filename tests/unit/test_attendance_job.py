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

    def test_eva_setup_pauses_worker_until_streamlit_submits_response(self):
        job = AttendanceJob(tracker_url=None)
        result = {}
        request = {"request_id": "req-1", "course_name": "CSC-151-N855"}
        waiter = threading.Thread(target=lambda: result.update(job.request_eva_setup(request)))
        waiter.start()
        _wait_for(job, "awaiting_eva_setup")
        assert job.current_eva_setup_request() == request
        job.submit_eva_setup({"request_id": "req-1", "skip": True})
        waiter.join(2)
        assert not waiter.is_alive()
        assert result == {"request_id": "req-1", "skip": True}
        assert job.phase == PHASE_RUNNING

    def test_cancel_unblocks_eva_setup_wait(self):
        job = AttendanceJob(tracker_url=None)
        errors = []
        waiter = threading.Thread(
            target=lambda: self._capture_error(errors, lambda: job.request_eva_setup(
                {"request_id": "req-2"})))
        waiter.start()
        _wait_for(job, "awaiting_eva_setup")
        job.cancel()
        waiter.join(2)
        assert not waiter.is_alive()
        assert errors and isinstance(errors[0], BaseException)

    @staticmethod
    def _capture_error(errors, action):
        try:
            action()
        except BaseException as error:  # cancellation intentionally subclasses BaseException
            errors.append(error)

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


@pytest.mark.unit
def test_eva_flags_replace_per_course_and_list_past_eva_first():
    import datetime as DT

    from cqc_cpcc.eva_check import flag_no_attendance

    roster = [{"id": "1", "text": "Zed, Z 1"}, {"id": "2", "text": "Amy, A 2"}]
    eva = DT.date(2026, 8, 26)
    job = AttendanceJob(tracker_url=None, bridge=MfaBridge())
    soon = flag_no_attendance("CSC-151", {"1": 0}, roster, eva, DT.date(2026, 8, 20))
    past = flag_no_attendance("CSC-134", {"2": 0}, roster, eva, DT.date(2026, 10, 6))
    job.on_eva_flags("CSC-151", soon)
    job.on_eva_flags("CSC-134", past)
    assert [f.course_section for f in job.eva_flags()] == ["CSC-134", "CSC-151"]
    job.on_eva_flags("CSC-134", [])
    assert [f.course_section for f in job.eva_flags()] == ["CSC-151"]


@pytest.mark.unit
class TestPause:
    """The page pauses the run; the worker holds before its next browser command."""

    @staticmethod
    def _job(phase):
        job = AttendanceJob(tracker_url=None)
        job._phase = phase
        return job

    @pytest.mark.parametrize("phase", ["starting", "running"])
    def test_pause_and_resume_while_the_browser_works(self, phase):
        job = self._job(phase)
        assert job.pausable
        job.pause()
        assert job.paused
        assert job.paused_since is not None
        job.resume()
        assert not job.paused
        assert job.paused_since is None

    @pytest.mark.parametrize("phase", [
        "awaiting_plan", "awaiting_eva_setup", "succeeded", "failed", "cancelled"])
    def test_no_pause_on_a_form_or_after_the_run(self, phase):
        job = self._job(phase)
        assert not job.pausable
        job.pause()
        assert not job.paused

    def test_no_pause_while_an_mfa_number_waits_for_approval(self):
        from cqc_cpcc.utilities.selenium_util import MfaChallenge

        job = self._job("running")
        job.bridge.on_challenge(MfaChallenge(context="microsoft", number="47"))
        assert not job.pausable
        job.pause()
        assert not job.paused

        job.bridge.on_resolved()
        assert job.pausable

    def test_no_pause_once_cancelled(self):
        job = self._job("running")
        job.cancel()
        assert not job.pausable

    def test_cancel_frees_a_paused_worker(self):
        job = self._job("running")
        job._gate._poll_interval = 0.001
        job.pause()
        errors = []

        def worker():
            try:
                job._gate.wait_if_paused()
            except BaseException as error:  # noqa: BLE001 - AttendanceJobCancelled is a BaseException
                errors.append(error)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(0.05)
        assert thread.is_alive()

        job.cancel()
        thread.join(2)
        assert not thread.is_alive()
        assert len(errors) == 1 and type(errors[0]).__name__ == "AttendanceJobCancelled"

    def test_a_run_that_ends_while_paused_is_not_left_paused(self, browser):
        job = AttendanceJob(tracker_url=None)
        with patch("cqc_cpcc.attendance.run_attendance_plan"):
            job.start()
            _wait_for(job, PHASE_AWAITING_PLAN)
            job._gate.pause()
            job.cancel()
            assert job.done.wait(5)
        assert not job.paused


@pytest.mark.unit
class TestLiveViewerWiring:
    @staticmethod
    def _open(monkeypatch, viewer):
        from cqc_cpcc.utilities import selenium_util

        raw_driver, wait = MagicMock(), MagicMock()
        monkeypatch.setattr(selenium_util, "unattended_browser_problem", lambda: None)
        monkeypatch.setattr(selenium_util, "get_session_driver", lambda: (raw_driver, wait))
        monkeypatch.setattr(selenium_util, "live_viewer_url", lambda: viewer)
        job = AttendanceJob(tracker_url=None)
        with patch("selenium.webdriver.support.event_firing_webdriver.EventFiringWebDriver") as efd, \
                patch("cqc_cpcc.my_colleges.MyColleges"):
            job._open_browser()
        return job, raw_driver, efd.call_args.args[1]

    def test_viewer_url_is_kept_and_screenshots_narrow_only_while_the_live_view_is_open(self, monkeypatch):
        job, _raw, listener = self._open(monkeypatch, "http://localhost:17900/?x=1")

        assert job.viewer_url == "http://localhost:17900/?x=1"
        assert listener._tab_change_only() is False  # the page opens on Tab snapshots
        job.live_view_open = True
        assert listener._tab_change_only() is True
        job.live_view_open = False  # the instructor went back to snapshots: events refresh them again
        assert listener._tab_change_only() is False

    def test_without_a_viewer_every_event_screenshots(self, monkeypatch):
        job, _raw, listener = self._open(monkeypatch, None)

        assert job.viewer_url is None
        job.live_view_open = True  # no viewer, nothing to narrow for
        assert listener._tab_change_only() is False

    def test_listener_and_keepalive_are_wired_to_the_jobs_gate(self, monkeypatch):
        job, raw_driver, listener = self._open(monkeypatch, None)

        assert listener.pause_gate is job._gate
        # The keepalive talks to the raw driver, so it never re-enters the blocked listener.
        raw_driver.current_url = "https://example.test/"
        assert job._gate.keepalive() == "https://example.test/"

    def test_a_viewer_check_that_errors_means_screenshots(self, monkeypatch):
        from cqc_cpcc.utilities import selenium_util

        def boom():
            raise RuntimeError("probe broke")

        monkeypatch.setattr(selenium_util, "live_viewer_url", boom)
        assert AttendanceJob._find_viewer_url() is None
