#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""scripts/import_smoke.py: imports every module of a package and reports failures."""

import importlib.util
import os
import subprocess
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


def _package(tmp_path, name, files):
    pkg = tmp_path / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for fname, body in files.items():
        (pkg / fname).write_text(body)
    return pkg


def _forget(prefix):
    for name in [m for m in sys.modules if m.startswith(prefix)]:
        del sys.modules[name]


def test_function_level_import_of_a_missing_package_fails(tmp_path, monkeypatch):
    _package(tmp_path, "lazypkg", {"lazy.py": "def build():\n    import a_package_that_does_not_exist_xyz\n"})
    monkeypatch.syspath_prepend(str(tmp_path))
    smoke = _load()
    assert smoke.smoke(["lazypkg"]) == []  # importing the module alone does not see it
    failures, _ = smoke.resolve(["lazypkg"])
    assert failures == [("lazypkg.lazy", "line 2: import a_package_that_does_not_exist_xyz does not resolve\n")]
    assert smoke.main(["lazypkg"]) == 1
    _forget("lazypkg")


def test_guarded_relative_stdlib_and_first_party_imports_are_skipped(tmp_path, monkeypatch):
    _package(tmp_path, "skippkg", {"mod.py": (
        "import json\nfrom . import other\nfrom cqc_cpcc import utilities\n"
        "try:\n    import optional_package_xyz\nexcept ImportError:\n    optional_package_xyz = None\n"
    ), "other.py": ""})
    monkeypatch.syspath_prepend(str(tmp_path))
    failures, skipped = _load().resolve(["skippkg"])
    assert failures == []
    assert "json" in skipped["stdlib"] and skipped["first_party"] == {"cqc_cpcc"}


def test_a_sys_exit_at_import_does_not_end_the_run(tmp_path, monkeypatch):
    _package(tmp_path, "exitpkg", {"a_exits.py": "import sys\nsys.exit(3)\n", "b_bad.py": "import missing_xyz\n"})
    monkeypatch.syspath_prepend(str(tmp_path))
    names = [name for name, _ in _load().smoke(["exitpkg"])]
    assert names == ["exitpkg.a_exits", "exitpkg.b_bad"]
    _forget("exitpkg")


def test_resolve_only_package_is_not_executed(tmp_path, monkeypatch):
    _package(tmp_path, "pagepkg", {"page.py": "raise RuntimeError('page code must not run')\n"})
    monkeypatch.syspath_prepend(str(tmp_path))
    assert _load().main(["--resolve", "pagepkg"]) == 0


def test_a_package_removed_from_the_lock_but_still_installed_is_reported():
    smoke = _load()
    base = '[[package]]\nname = "PyTest"\nversion = "1"\n\n[[package]]\nname = "not_installed_xyz"\nversion = "1"\n'
    removed, installed = smoke.removed_but_installed(base, "")
    assert removed == {"pytest", "not-installed-xyz"} and installed == ["pytest"]


def test_first_party_packages_pass_the_smoke():
    """The same command the dependency-smoke job runs, minus --removed-since. No network."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--import", "cqc_cpcc", "--resolve", "cqc_streamlit_app"],
        capture_output=True, text=True, timeout=300, cwd=SCRIPT.parents[1],
        env={**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(SCRIPT.parents[1] / "src"),
                                                                       os.environ.get("PYTHONPATH")]))},
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert "0 failure(s)" in result.stdout
