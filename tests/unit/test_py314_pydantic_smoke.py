"""Import smoke: first-party modules must not pull in Pydantic v1.

Imports every module under ``src/cqc_cpcc`` and ``src/cqc_streamlit_app`` in a fresh
interpreter (so this test cannot leak imports into the rest of the suite) under
``warnings.catch_warnings(record=True)``. A module that fails to import (optional
extras, Streamlit-only side effects) is recorded and ignored: only Pydantic v1
signals are asserted.

* Python >= 3.14: pydantic emits "Core Pydantic V1 functionality isn't compatible with
  Python 3.14 or greater" whenever ``pydantic.v1`` is imported.
* Python 3.12 / 3.13: the same import is silent, so the check is that ``pydantic.v1``
  is not in ``sys.modules`` after the sweep.

Both are xfail(strict=False) today: langchain-core (``output_parsers.json``,
``utils.pydantic``) still imports ``pydantic.v1`` and first-party code imports
langchain-core. Fixed in PR-2; remove the xfail there.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]

_SWEEP = textwrap.dedent(
    """
    import importlib, json, pkgutil, sys, warnings

    failures = {}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        for root in ("cqc_cpcc", "cqc_streamlit_app"):
            pkg = importlib.import_module(root)
            names = [root] + [m.name for m in pkgutil.walk_packages(pkg.__path__, root + ".")]
            for name in names:
                try:
                    importlib.import_module(name)
                except BaseException as exc:  # optional extras, Streamlit side effects, SystemExit
                    failures[name] = type(exc).__name__
    print(json.dumps({
        "warnings": [str(w.message) for w in caught if "Pydantic V1" in str(w.message)],
        "v1_loaded": sorted(m for m in sys.modules if m == "pydantic.v1" or m.startswith("pydantic.v1.")),
        "failures": failures,
    }))
    """
)


def _run_sweep() -> dict:
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT / "src"), CQC_EXECUTION="off")
    proc = subprocess.run(
        [sys.executable, "-c", _SWEEP], capture_output=True, text=True, env=env, cwd=REPO_ROOT, timeout=600
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_imports_without_pydantic_v1_warning(request: pytest.FixtureRequest) -> None:
    request.applymarker(
        pytest.mark.xfail(strict=False, reason="langchain-core imports pydantic.v1; fixed in PR-2")
    )
    result = _run_sweep()
    if sys.version_info >= (3, 14):
        assert not [w for w in result["warnings"] if "Core Pydantic V1 functionality" in w], result["warnings"]
    assert not result["v1_loaded"], f"pydantic.v1 loaded by first-party imports: {result['v1_loaded'][:5]}"


def test_import_sweep_runs() -> None:
    """The sweep itself works and covers the packages (not xfail: guards the harness)."""
    result = _run_sweep()
    assert isinstance(result["failures"], dict)
    assert len(result["failures"]) < 20, result["failures"]
