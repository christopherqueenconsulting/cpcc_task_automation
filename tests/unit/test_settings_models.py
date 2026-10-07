#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Settings page model section, Home update banner, and registry-driven pickers."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from cqc_cpcc.utilities.AI import model_registry

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

APP_DIR = Path(__file__).resolve().parents[2] / "src" / "cqc_streamlit_app"
SETTINGS_PAGE = str(APP_DIR / "app_pages" / "settings.py")


@pytest.fixture(autouse=True)
def clean_pins(monkeypatch):
    for role in model_registry.ROLES:
        monkeypatch.delenv(f"CQC_MODEL_{role.upper()}", raising=False)
    monkeypatch.delenv("CQC_MODEL_REGISTRY_PATH", raising=False)
    model_registry._cache.clear()
    yield
    # The page writes os.environ directly, outside monkeypatch's bookkeeping.
    for role in model_registry.ROLES:
        os.environ.pop(f"CQC_MODEL_{role.upper()}", None)


@pytest.mark.unit
class TestModelSettings:
    def test_table_shows_registry_model_for_each_role(self):
        app = AppTest.from_file(SETTINGS_PAGE, default_timeout=30)
        app.run()
        assert not app.exception, app.exception
        table = app.dataframe[0].value
        assert list(table["Feature"]) == list(model_registry.ROLES)
        for role, model in zip(table["Feature"], table["Model in use"]):
            assert model == model_registry.resolve(role).model

    def test_pin_sets_env_override_and_unpin_clears_it(self):
        app = AppTest.from_file(SETTINGS_PAGE, default_timeout=30)
        app.run()
        pin = next(s for s in app.selectbox if s.label == "Pin model for grading")
        pin.select("openai/gpt-5-mini")
        next(b for b in app.button if b.label == "Save model pins").click()
        app.run()
        assert not app.exception, app.exception
        assert os.environ["CQC_MODEL_GRADING"] == "openai/gpt-5-mini"
        assert model_registry.resolve("grading").source == "env"

        pin = next(s for s in app.selectbox if s.label == "Pin model for grading")
        pin.select("Use registry default")
        next(b for b in app.button if b.label == "Save model pins").click()
        app.run()
        assert "CQC_MODEL_GRADING" not in os.environ


@pytest.mark.unit
class TestRegistryHelpers:
    def test_recommended_model_follows_registry_and_pins(self, monkeypatch):
        from cqc_streamlit_app.utils import recommended_model

        assert recommended_model("grading") == model_registry.load_registry().roles["grading"].model
        monkeypatch.setenv("CQC_MODEL_GRADING", "openai/gpt-5")
        assert recommended_model("grading") == "openai/gpt-5"

    def test_choices_include_roles_fallbacks_and_previous(self):
        from cqc_streamlit_app.utils import registry_model_choices

        registry = model_registry.load_registry()
        choices = registry_model_choices()
        for role in registry.roles.values():
            assert role.model in choices
            if role.fallback:
                assert role.fallback in choices
        assert set(registry.previous.values()) <= set(choices)


HOME_SCRIPT = """
from unittest.mock import patch
import cqc_streamlit_app.utils as utils
with patch.object(utils, "remote_registry_revision", return_value=REMOTE):
    utils.show_model_update_banner()
"""


@pytest.mark.unit
class TestUpdateBanner:
    def _run(self, remote):
        app = AppTest.from_string(HOME_SCRIPT.replace("REMOTE", repr(remote)), default_timeout=30)
        app.run()
        assert not app.exception, app.exception
        return " ".join(block.value for block in app.info)

    def test_newer_remote_revision_shows_banner(self):
        assert "git pull" in self._run("9999-12-31.1")

    def test_same_or_unknown_remote_revision_shows_nothing(self):
        local = model_registry.load_registry().revision
        assert self._run(local) == ""
        assert self._run(None) == ""
