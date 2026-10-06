#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import os
import tempfile

import pytest

# Fixed alias key so tests never create ~/.cqc_cpcc/pii_alias.key and aliases are
# deterministic (see cqc_cpcc.utilities.pii_redaction).
os.environ.setdefault("CQC_PII_ALIAS_KEY", "test-alias-key")
_SYSTEM_TEMPDIR = tempfile.tempdir

@pytest.fixture
def sample_fixture():
    return "sample data"

@pytest.fixture(autouse=True)
def _isolate_app_tempdir():
    """Page modules call init_session_state() on import, which routes tempfile into
    the app's private temp dir (cqc_cpcc.utilities.temp_files). Keep that from
    leaking into unrelated tests that rely on the system temp dir."""
    tempfile.tempdir = _SYSTEM_TEMPDIR
    yield
    tempfile.tempdir = _SYSTEM_TEMPDIR


@pytest.fixture(autouse=True)
def _isolate_posthog_telemetry(monkeypatch):
    """Never send test events to the real PostHog project from a developer's .env.
    Tests that exercise telemetry set POSTHOG_API_KEY themselves."""
    from cqc_cpcc.utilities.AI import posthog_telemetry

    monkeypatch.delenv("POSTHOG_API_KEY", raising=False)
    posthog_telemetry.reload()
    yield
    posthog_telemetry.reload()


@pytest.fixture(autouse=True)
def _isolate_attendance_ledger(tmp_path, monkeypatch):
    """Never let a test write to the real ~/.cqc_cpcc/attendance.sqlite3."""
    monkeypatch.setenv("CQC_ATTENDANCE_DB", str(tmp_path / "attendance.sqlite3"))
    monkeypatch.setenv("CQC_ATTENDANCE_REPORT_DIR", str(tmp_path / "attendance_reports"))
