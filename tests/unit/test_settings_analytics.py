#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""The Settings page can supply PostHog credentials when .env does not."""

from pathlib import Path
import os
from unittest.mock import MagicMock, patch

import pytest

from cqc_cpcc.utilities.AI import posthog_telemetry as telemetry

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

SETTINGS_PAGE = str(
    Path(__file__).resolve().parents[2] / "src" / "cqc_streamlit_app" / "app_pages" / "settings.py"
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("POSTHOG_API_KEY", "POSTHOG_HOST", "POSTHOG_LLM_ANALYTICS"):
        monkeypatch.delenv(name, raising=False)
    telemetry._reset_for_tests()
    yield
    # configure() writes os.environ directly, outside monkeypatch's bookkeeping.
    for name in ("POSTHOG_API_KEY", "POSTHOG_HOST"):
        os.environ.pop(name, None)
    telemetry._reset_for_tests()


def _run_page():
    app = AppTest.from_file(SETTINGS_PAGE, default_timeout=30)
    app.run()
    assert not app.exception, app.exception
    return app


def _field(app, label):
    return next(widget for widget in app.text_input if widget.label == label)


@pytest.mark.unit
class TestAnalyticsSettings:
    def test_page_shows_analytics_off_without_a_key(self):
        app = _run_page()
        messages = " ".join(block.value for block in app.info)
        assert "POSTHOG_API_KEY is not set" in messages

    def test_saving_a_key_turns_analytics_on(self, monkeypatch):
        posthog_module = MagicMock()
        with patch.dict("sys.modules", {"posthog": posthog_module}):
            app = _run_page()
            _field(app, "PostHog project API key").input("phc_from_settings")
            next(s for s in app.selectbox if s.label == "PostHog region").select("EU Cloud")
            app.run()
            next(button for button in app.button
                 if button.label == "Save analytics settings").click()
            app.run()

        assert not app.exception
        kwargs = posthog_module.Posthog.call_args.kwargs
        assert kwargs["project_api_key"] == "phc_from_settings"
        assert kwargs["host"] == "https://eu.i.posthog.com"
        assert app.session_state.posthog_api_key == "phc_from_settings"
        assert any("enabled" in block.value for block in app.success)

    def test_custom_host_must_be_https(self):
        app = _run_page()
        next(s for s in app.selectbox if s.label == "PostHog region").select("Custom (self-hosted)")
        app.run()
        _field(app, "PostHog host URL").input("http://insecure.example.edu")
        next(button for button in app.button if button.label == "Save analytics settings").click()
        app.run()
        assert any("https://" in block.value for block in app.error)


@pytest.mark.unit
def test_settings_page_has_no_password_fields_for_safari_to_hijack():
    """Password-type fields make Safari offer to save/fill a password on every Tab."""
    app = _run_page()
    for widget in app.text_input:
        assert widget.proto.type == widget.proto.DEFAULT, widget.label
        assert widget.proto.autocomplete == "off", widget.label
    secret_keys = {w.key for w in app.text_input if w.key and w.key.startswith("cqc_secret_")}
    assert secret_keys == {"cqc_secret_openrouter_api_key", "cqc_secret_openai_api_key",
                           "cqc_secret_instructor_password", "cqc_secret_posthog_api_key"}
