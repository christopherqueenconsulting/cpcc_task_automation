#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Navigation shell, local preferences and the legacy-pages toggle (plan N-1 to N-3)."""

import json
from pathlib import Path

import pytest

from cqc_streamlit_app import app_settings, navigation

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

APP_DIR = Path(__file__).resolve().parents[2] / "src" / "cqc_streamlit_app"
HOME = str(APP_DIR / "Home.py")



@pytest.mark.unit
def test_legacy_pages_listed_only_when_enabled():
    home = navigation
    off = {f for _, f, _, _ in home.page_specs(False)}
    on = {f for _, f, _, _ in home.page_specs(True)}
    assert "legacy_exam_grading.py" not in off
    assert on - off == {"legacy_exam_grading.py"}


@pytest.mark.unit
def test_every_listed_page_file_exists():
    home = navigation
    for _, file, _, _ in home.page_specs(True):
        assert (APP_DIR / "app_pages" / file).is_file(), file


@pytest.mark.unit
def test_settings_round_trip_and_defaults(tmp_path):
    assert app_settings.load_settings().show_legacy_pages is False
    app_settings.update_settings(show_legacy_pages=True)
    path = app_settings.settings_path()
    assert json.loads(path.read_text()) == {"show_legacy_pages": True}
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    # A fresh load (as after an app restart) sees the saved value.
    assert app_settings.load_settings().show_legacy_pages is True


@pytest.mark.unit
def test_corrupt_settings_file_falls_back_to_defaults():
    path = app_settings.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    assert app_settings.load_settings() == app_settings.AppSettings()


@pytest.mark.unit
def test_router_runs_default_page():
    app = AppTest.from_file(HOME, default_timeout=60).run()
    assert not app.exception, [e.value for e in app.exception]
    assert any("Welcome to CPCC Task Automation" in h.value for h in app.header)


@pytest.mark.unit
@pytest.mark.parametrize("page", [
    "home.py", "grade_assignment.py", "flowgorithm.py", "give_feedback.py",
    "take_attendance.py", "find_student.py", "settings.py", "legacy_exam_grading.py",
])
def test_every_page_loads_without_exception(page):
    app = AppTest.from_file(str(APP_DIR / "app_pages" / page), default_timeout=60).run()
    assert not app.exception, [e.value for e in app.exception]


@pytest.mark.unit
def test_settings_toggle_saves_preference():
    app = AppTest.from_file(str(APP_DIR / "app_pages" / "settings.py"), default_timeout=60).run()
    toggle = next(t for t in app.toggle if t.label == "Show legacy pages")
    assert toggle.value is False
    toggle.set_value(True).run()
    assert app_settings.load_settings().show_legacy_pages is True


@pytest.mark.unit
def test_legacy_page_shows_deprecation_notice(monkeypatch):
    events = []
    from cqc_cpcc.utilities.AI import posthog_telemetry
    monkeypatch.setattr(posthog_telemetry, "capture_event", lambda e, p=None: events.append((e, p)))
    app = AppTest.from_file(str(APP_DIR / "app_pages" / "legacy_exam_grading.py"), default_timeout=60).run()
    assert any("Deprecated" in w.value for w in app.warning)
    app.run()
    assert events == [("cqc_legacy_page_view", {"cqc_page": "exams_legacy"})]


@pytest.mark.unit
def test_pages_render_when_run_through_the_router():
    """Pages guard their body with `if __name__ == '__main__'`; it must still run under
    st.navigation."""
    app = AppTest.from_file(HOME, default_timeout=60).run()
    app.switch_page("app_pages/settings.py").run()
    assert not app.exception, [e.value for e in app.exception]
    assert any(t.label == "Show legacy pages" for t in app.toggle)


@pytest.mark.unit
def test_legacy_page_not_reachable_when_disabled():
    app = AppTest.from_file(HOME, default_timeout=60).run()
    with pytest.raises(ValueError, match="Could not find a navigation page"):
        app.switch_page("app_pages/legacy_exam_grading.py")


@pytest.mark.unit
def test_legacy_page_reachable_when_enabled():
    app_settings.update_settings(show_legacy_pages=True)
    app = AppTest.from_file(HOME, default_timeout=60).run()
    app.switch_page("app_pages/legacy_exam_grading.py").run()
    assert any("Deprecated" in w.value for w in app.warning)
