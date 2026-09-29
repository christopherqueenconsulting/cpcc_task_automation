#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""The Take Attendance page asks the console's questions and shows the MFA number."""

import datetime as DT
from pathlib import Path

import pytest

from cqc_cpcc.attendance_job import (
    PHASE_AWAITING_PLAN,
    PHASE_STARTING,
    PHASE_SUCCEEDED,
    AttendanceJob,
)
from cqc_cpcc.utilities.selenium_util import MfaChallenge

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

PAGE = str(
    Path(__file__).resolve().parents[2] / "src" / "cqc_streamlit_app" / "pages" / "1_Take_Attendance.py"
)

TODAY = DT.datetime.now()
COURSES = {
    "url-now": {"name": "CSC-151-N855: JAVA", "start_date": TODAY - DT.timedelta(days=10),
                "end_date": TODAY + DT.timedelta(days=60)},
    "url-old": {"name": "CSC-134-N801: C++", "start_date": TODAY - DT.timedelta(days=800),
                "end_date": TODAY - DT.timedelta(days=700)},
}


class FakeJob(AttendanceJob):
    """A job that never starts a thread; the test sets its phase."""

    def __init__(self, phase, tracker_url="https://tracker"):
        super().__init__(tracker_url=tracker_url)
        self._phase = phase
        self.course_information = dict(COURSES)

    def submit_plan(self, plan):
        self.plan = plan
        self._set_phase(PHASE_SUCCEEDED)


def _app(job=None):
    app = AppTest.from_file(PAGE, default_timeout=30)
    app.session_state["instructor_user_id"] = "instructor"
    app.session_state["instructor_password"] = "pw"
    app.session_state["attendance_tracker_url"] = "https://tracker"
    if job is not None:
        app.session_state["attendance_job"] = job
    return app


def _button(app, label):
    return next(button for button in app.button if button.label == label)


@pytest.mark.unit
class TestTakeAttendancePage:
    def test_idle_page_has_one_log_output_and_a_start_button(self):
        app = _app()
        app.run()
        assert not app.exception, app.exception
        assert [h.value for h in app.subheader].count("Log Output") == 1
        assert len(app.text_area) == 1
        assert _button(app, "Start Attendance")

    def test_form_builds_the_plan_from_the_selections(self):
        job = FakeJob(PHASE_AWAITING_PLAN)
        app = _app(job)
        app.run()
        assert not app.exception, app.exception

        courses = app.multiselect[0]
        assert courses.label == "Courses to process"
        assert courses.value == ["url-now"]  # active course pre-selected
        radio = app.radio[0]
        assert radio.label == "Attendance start date"

        radio.set_value("custom").run()
        app.date_input[0].set_value(DT.date(2026, 9, 1)).run()
        _button(app, "▶ Continue").click().run()
        assert not app.exception, app.exception

        plan = job.plan
        assert plan.course_urls == ["url-now"]
        assert plan.attendance_start_date == DT.datetime(2026, 9, 1)
        assert plan.process_withdrawals is True
        assert "Attendance finished for 1 course(s)" in app.success[0].value

    def test_mfa_number_is_shown_while_signing_in(self):
        job = FakeJob(PHASE_STARTING)
        job.bridge.on_challenge(MfaChallenge(context="microsoft", number="47"))
        # The page polls while signing in; finish the job during the first
        # render so the test sees one frame instead of looping.
        app = _app(job)
        original = job.latest_progress

        def progress_then_finish():
            job._set_phase(PHASE_SUCCEEDED)
            return original()

        job.latest_progress = progress_then_finish
        app.run()
        assert not app.exception, app.exception
        assert any("47" in block.value for block in app.markdown)
        assert any("Two-factor approval needed" in h.value for h in app.subheader)


@pytest.mark.unit
class TestScreenshotSection:
    """A stock photo holds the spot until browser screenshots replace it."""

    def test_placeholder_until_the_first_screenshot(self, monkeypatch):
        from cqc_streamlit_app import pexels_helper

        photo = type("Photo", (), {"original": "https://images.example/landscape.jpg"})()
        monkeypatch.setattr(pexels_helper, "get_photo", lambda query: photo)
        app = _app()
        app.run()
        assert not app.exception, app.exception
        assert "Attendance Screenshot" in [h.value for h in app.subheader]
        assert app.get("image")[0].proto.imgs[0].url == photo.original

    def test_screenshot_replaces_the_placeholder(self, monkeypatch):
        import base64

        from cqc_streamlit_app import pexels_helper

        monkeypatch.setattr(pexels_helper, "get_photo",
                            lambda query: pytest.fail("placeholder not needed"))
        job = FakeJob(PHASE_AWAITING_PLAN)
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        job._store_screenshot(base64.b64encode(png).decode())
        app = _app(job)
        app.run()
        assert not app.exception, app.exception
        url = app.get("image")[0].proto.imgs[0].url
        assert "landscape" not in url

    def test_no_pexels_key_shows_a_caption_instead(self, monkeypatch):
        from cqc_streamlit_app import pexels_helper

        monkeypatch.delenv("PEXELS_API_KEY", raising=False)
        monkeypatch.setattr(pexels_helper, "_api", None)
        app = _app()
        app.run()
        assert not app.exception, app.exception
        assert any("Screenshots of the browser appear here" in c.value for c in app.caption)
