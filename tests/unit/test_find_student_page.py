#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""The Find Student page gathers rosters in the background and shows the MFA number."""

from pathlib import Path

import pytest

from cqc_cpcc.find_student import (
    PHASE_FAILED,
    PHASE_RUNNING,
    PHASE_SUCCEEDED,
    FindStudentJob,
    FindStudents,
)
from cqc_cpcc.utilities.selenium_util import MfaChallenge

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

PAGE = str(Path(__file__).resolve().parents[2] / "src" / "cqc_streamlit_app" / "app_pages"
           / "find_student.py")


class FakeJob(FindStudentJob):
    """A job that never starts a thread; the test sets its phase."""

    def __init__(self, phase, info=None):
        super().__init__()
        self._phase = phase
        if info is not None:
            self.finder = FindStudents(student_info=info)

    def start(self):
        pass


def _app(job=None):
    app = AppTest.from_file(PAGE, default_timeout=30)
    app.session_state["instructor_user_id"] = "instructor"
    app.session_state["instructor_password"] = "pw"
    if job is not None:
        app.session_state["find_student_job"] = job
    return app


@pytest.mark.unit
class TestFindStudentPage:
    def test_first_visit_starts_a_background_job_without_crashing(self, monkeypatch):
        import cqc_cpcc.find_student as fs_module

        started = []
        monkeypatch.setattr(fs_module.FindStudentJob, "start", lambda self: started.append(self))
        app = _app()
        app.run()
        assert not app.exception, app.exception
        assert len(started) == 1
        assert not any("Duo" in block.value for block in app.markdown)

    def test_authenticator_number_shows_while_signing_in(self):
        job = FakeJob(PHASE_RUNNING)
        job.bridge.on_challenge(MfaChallenge(context="microsoft", number="47"))
        app = _app(job)
        app.run()
        assert not app.exception, app.exception
        assert any("47" in block.value for block in app.markdown)
        assert any("Two-factor approval needed" in h.value for h in app.subheader)

    def test_results_table_after_the_job_succeeds(self):
        job = FakeJob(PHASE_SUCCEEDED, {"123": ("Adams, Ann", "a@x.edu", "CSC-134-N801")})
        app = _app(job)
        app.run()
        assert not app.exception, app.exception
        assert any(button.label == "↻ Refresh student list" for button in app.button)

    def test_failure_offers_a_retry(self):
        job = FakeJob(PHASE_FAILED)
        job.error = "RuntimeError: no grid"
        app = _app(job)
        app.run()
        assert not app.exception, app.exception
        assert any("no grid" in e.value for e in app.error)
        assert any(button.label == "Try again" for button in app.button)
