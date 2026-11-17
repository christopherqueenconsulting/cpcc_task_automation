#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Privacy behaviour of scripts/login_probe.py: no capture of authenticated BrightSpace pages."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "login_probe.py"


@pytest.fixture
def probe_module(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("login_probe_under_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "OUT_DIR", str(tmp_path / "login_probe"))
    return module


def _driver(url: str, title: str = "Some title"):
    driver = Mock()
    driver.current_url = url
    driver.title = title
    driver.find_elements.return_value = []
    driver.execute_script.return_value = {"inputs": [], "buttons": [], "errors": [], "headings": []}
    return driver


@pytest.mark.unit
def test_snapshot_skips_capture_on_authenticated_brightspace_page(probe_module):
    driver = _driver("https://brightspace.cpcc.edu/d2l/lms/dropbox/admin/mark/folder_submissions_users.d2l")
    step = probe_module.Probe(driver, ["example"]).snapshot("loop")

    driver.save_screenshot.assert_not_called()
    driver.execute_script.assert_not_called()
    assert "title" not in step
    assert "inventory" not in step
    assert step["host"] == "brightspace.cpcc.edu"
    assert probe_module._classify(step) == "brightspace"


@pytest.mark.unit
def test_snapshot_still_captures_the_microsoft_sign_in_page(probe_module):
    driver = _driver("https://login.microsoftonline.com/tenant/saml2", "Sign in to your account")
    step = probe_module.Probe(driver, ["example"]).snapshot("loop")

    driver.save_screenshot.assert_called_once()
    assert step["title"] == "Sign in to your account"
    assert "inventory" in step
