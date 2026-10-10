#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""scripts/import_smoke.py: imports every module of a package and reports failures."""

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "import_smoke.py"


def _load():
    spec = importlib.util.spec_from_file_location("import_smoke", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_reports_a_module_that_fails_to_import(tmp_path, monkeypatch):
    pkg = tmp_path / "smokepkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "good.py").write_text("X = 1\n")
    (pkg / "bad.py").write_text("import a_package_that_does_not_exist_xyz\n")
    (pkg / "__main__.py").write_text("raise SystemExit('entry point must not run')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    smoke = _load()
    failures = smoke.smoke(["smokepkg"])
    assert [name for name, _ in failures] == ["smokepkg.bad"]
    assert smoke.main(["smokepkg"]) == 1
    for name in [m for m in sys.modules if m.startswith("smokepkg")]:
        del sys.modules[name]


def test_clean_package_passes(tmp_path, monkeypatch):
    pkg = tmp_path / "cleanpkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text("Y = 2\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    assert _load().main(["cleanpkg"]) == 0
