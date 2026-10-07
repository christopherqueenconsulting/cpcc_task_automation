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
    Path(__file__).resolve().parents[2] / "src" / "cqc_streamlit_app" / "app_pages" / "take_attendance.py"
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
        assert [t.label for t in app.tabs][:2] == ["Browser screenshot", "Log"]
        assert not app.text_area
        assert len(app.code) == 1
        assert _button(app, "Start attendance")

    def test_log_output_shows_new_lines_on_each_run(self):
        from cqc_streamlit_app.streamlit_logger import streamlit_handler

        app = _app()
        app.run()
        streamlit_handler.logs.append("Opened CSC-151-N855 roster")
        app.run()
        assert not app.exception, app.exception
        assert "Opened CSC-151-N855 roster" in app.code[0].value

    def test_running_job_refreshes_only_the_live_view(self):
        job = FakeJob(PHASE_STARTING)
        app = _app(job)
        app.run()
        assert not app.exception, app.exception
        assert app.session_state["attendance_rendered_phase"] == PHASE_STARTING
        assert any("Starting" in block.value for block in app.info)

    def test_warnings_stay_on_the_page_while_running_and_after(self):
        job = FakeJob(PHASE_STARTING)
        job.on_warning("Attendance is not working in MyColleges for CSC-134-N801")
        app = _app(job)
        app.run()
        assert any("CSC-134-N801" in w.value for w in app.warning)
        job._set_phase(PHASE_SUCCEEDED)
        app.run()
        assert not app.exception, app.exception
        assert any("CSC-134-N801" in w.value for w in app.warning)

    def test_students_without_attendance_pop_out_red_after_eva_and_yellow_before(self):
        from cqc_cpcc.eva_check import flag_no_attendance

        roster = [{"id": "4340773", "text": "Marr, Tyler R. 4340773"},
                  {"id": "4396124", "text": "Pulido, Ethan 4396124"}]
        eva = DT.date(2026, 8, 26)
        job = FakeJob(PHASE_STARTING)
        job.on_eva_flags("CSC-134-N801", flag_no_attendance(
            "CSC-134-N801", {"4340773": 0}, roster, eva, DT.date(2026, 10, 6)))
        job.on_eva_flags("CSC-151-N805", flag_no_attendance(
            "CSC-151-N805", {"4396124": 0}, roster, eva, DT.date(2026, 8, 20)))
        app = _app(job)
        app.run()
        assert not app.exception, app.exception
        assert any("Past EVA: no attendance recorded" in e.value for e in app.error)
        assert any("At risk before EVA" in w.value for w in app.warning)
        assert any("CSC-134-N801" in t.value for t in app.toast)
        assert any("Marr, Tyler R." in str(table.value.to_dict()) for table in app.dataframe)
        # The toast for a course pops once, not on every refresh.
        app.run()
        assert not any("CSC-134-N801" in t.value for t in app.toast)

    def test_local_data_shows_the_ledger_path_and_reports(self, tmp_path, monkeypatch):
        from cqc_cpcc.attendance_ledger import default_db_path

        report_dir = tmp_path / "reports"
        report_dir.mkdir()
        (report_dir / "missing_CSC-134-N801_20261005_102944.csv").write_text(
            "course_section,attend_date,student_id\n")
        monkeypatch.setenv("CQC_ATTENDANCE_REPORT_DIR", str(report_dir))
        app = _app()
        app.session_state["attendance_ledger_open"] = True  # the ledger loads only when open
        app.run()
        assert not app.exception, app.exception
        assert "Local attendance data (view only)" in [h.value for h in app.subheader]
        captions = [c.value for c in app.caption]
        assert any(default_db_path() in c for c in captions)
        assert any(str(report_dir) in c for c in captions)
        assert any("missing_CSC-134-N801" in block.value for block in app.markdown)

    def test_form_builds_the_plan_from_the_selections(self):
        job = FakeJob(PHASE_AWAITING_PLAN)
        app = _app(job)
        app.run()
        assert not app.exception, app.exception

        courses = app.multiselect[0]
        assert courses.label == "Courses to process"
        assert courses.value == ["url-now"]  # active course pre-selected
        # No start-date question any more: the ledger decides per course.
        assert not app.radio
        recheck = next(c for c in app.checkbox if c.key == "attendance_full_recheck")
        recheck.check().run()
        _button(app, "Continue").click().run()
        assert not app.exception, app.exception

        plan = job.plan
        assert plan.course_urls == ["url-now"]
        assert plan.full_recheck is True
        assert plan.write_attendance is True
        assert plan.process_withdrawals is True
        assert "Attendance finished for 1 course(s)" in app.success[0].value

    def test_ledger_section_shows_counts_without_names(self):
        from cqc_cpcc.attendance_ledger import STATUS_VERIFIED, AttendanceLedger

        ledger = AttendanceLedger()  # conftest points this at a temp file
        ledger.set_status("Fall 2026", "CSC-134-N801", "1000001", DT.date(2026, 8, 19),
                          STATUS_VERIFIED)
        ledger.close()

        app = _app()
        app.session_state["attendance_ledger_open"] = True
        app.run()
        assert not app.exception, app.exception
        assert any("Attendance ledger" in e.label for e in app.expander)
        assert len(app.dataframe) >= 1

    def test_ledger_tables_explain_their_columns(self):
        from cqc_cpcc.attendance_ledger import STATUS_FAILED, AttendanceLedger

        ledger = AttendanceLedger()
        ledger.set_status("Fall 2026", "CSC-134-N801", "1000001", DT.date(2026, 8, 17),
                          STATUS_FAILED, "not Present after reload")
        ledger.close()
        app = _app()
        app.session_state["attendance_ledger_open"] = True
        app.run()
        assert not app.exception, app.exception
        assert any(block.value == "**Glossary** ⓘ" and "failed" in (block.help or "")
                   for block in app.markdown)
        # Hover help on the "failed" column header of the per-date table.
        assert any('"failed": {"help"' in table.proto.columns for table in app.dataframe)

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
        assert "Browser screenshot" in [t.label for t in app.tabs]
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
        # Inline, so no media-file URL can expire before the browser fetches it.
        assert url.startswith("data:image/png;base64,")

    def test_each_open_browser_tab_gets_its_own_view(self, monkeypatch):
        import base64

        from cqc_streamlit_app import pexels_helper

        monkeypatch.setattr(pexels_helper, "get_photo",
                            lambda query: pytest.fail("placeholder not needed"))
        png = base64.b64encode(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )).decode()
        job = FakeJob(PHASE_AWAITING_PLAN)
        job._store_screenshot(png, "faculty", "Faculty", ["faculty"])
        job._store_screenshot(png, "course", "Section Details for a long course title here",
                              ["faculty", "course"])
        app = _app(job)
        app.run()
        assert not app.exception, app.exception
        all_labels = [tab.label for tab in app.tabs]
        labels = all_labels[all_labels.index("Live"):]  # the browser views, inside "Browser screenshot"
        assert labels[0] == "Live"
        assert labels[1] == "Tab 1 · Faculty"
        assert labels[2].startswith("Tab 2 · Section Details") and labels[2].endswith("…")
        assert len(app.get("image")) == 3
        assert any("The run is using this tab." in c.value for c in app.caption)

    def test_no_pexels_key_shows_a_caption_instead(self, monkeypatch):
        from cqc_streamlit_app import pexels_helper

        monkeypatch.delenv("PEXELS_API_KEY", raising=False)
        monkeypatch.setattr(pexels_helper, "_api", None)
        app = _app()
        app.run()
        assert not app.exception, app.exception
        assert any("Screenshots of the browser appear here" in c.value for c in app.caption)



@pytest.mark.unit
class TestDesignPieceU5:
    """UX goals for Take attendance: lazy ledger, tracker URL from Settings, remembered courses."""

    def test_collapsed_ledger_does_not_open_the_database(self, monkeypatch):
        from cqc_cpcc import attendance_ledger

        opened = []
        monkeypatch.setattr(attendance_ledger.AttendanceLedger, "__init__",
                            lambda self, *a, **k: opened.append(1) or None)
        open(attendance_ledger.default_db_path(), "w").close()
        app = _app()
        app.run()
        assert not app.exception, app.exception
        assert opened == []

    def test_tracker_url_comes_from_settings_not_a_page_input(self):
        app = _app()
        app.run()
        assert not app.text_input
        assert any("https://tracker" in c.value and "Settings" in c.value for c in app.caption)

    def test_last_run_courses_are_remembered(self):
        from cqc_streamlit_app import app_settings

        job = FakeJob(PHASE_AWAITING_PLAN)
        app = _app(job)
        app.run()
        app.multiselect[0].set_value(["url-now", "url-old"])
        app.toggle[0].set_value(True).run()  # show other terms so url-old is offered
        app.multiselect[0].set_value(["url-now", "url-old"]).run()
        _button(app, "Continue").click().run()
        assert app_settings.load_settings().last_attendance_courses == ["url-now", "url-old"]
