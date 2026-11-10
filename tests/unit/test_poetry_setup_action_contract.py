"""Contract checks for the poetry_setup composite action's venv cache."""

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github/actions/poetry_setup/action.yml"
PATCH_REF = "steps.setup-python.outputs.python-version"


def _steps() -> list[dict]:
    return yaml.safe_load(ACTION.read_text())["runs"]["steps"]


def _step(name: str) -> dict:
    matches = [s for s in _steps() if s.get("name") == name]
    assert len(matches) == 1, f"expected exactly one step named {name!r}"
    return matches[0]


def test_venv_cache_key_includes_exact_python_patch():
    restore = _step("Restore Poetry virtualenv")
    save = _step("Save Poetry virtualenv")
    for key in (
        restore["with"]["key"],
        restore["with"]["restore-keys"],
        save["with"]["key"],
    ):
        assert PATCH_REF in key
    assert "inputs.python-version" not in restore["with"]["key"]


def test_venv_save_is_master_push_only():
    cond = _step("Save Poetry virtualenv")["if"]
    assert "cache-hit != 'true'" in cond
    assert "github.event_name == 'push'" in cond
    assert "github.ref == 'refs/heads/master'" in cond


def test_verify_step_guards_the_install_skip():
    names = [s.get("name") for s in _steps()]
    assert names.index("Restore Poetry virtualenv") < names.index(
        "Verify restored virtualenv"
    ) < names.index("Install dependencies")
    verify = _step("Verify restored virtualenv")
    cond = verify["if"]
    assert "skip-install-on-exact-hit == 'true'" in cond
    assert "cache-hit == 'true'" in cond
    assert verify["env"]["EXPECTED_PYTHON"] == "${{ " + PATCH_REF + " }}"
    script = verify["run"]
    assert ".venv/bin/python" in script
    assert "poetry install" in script
    # The skip condition on the real install is unchanged.
    assert "cache-hit == 'true'" in _step("Install dependencies")["if"]
