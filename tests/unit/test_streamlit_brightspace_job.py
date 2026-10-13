"""Unit tests for the Grade Assignment page's separate BrightSpace fetches."""

#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

from unittest.mock import MagicMock

import pytest
import streamlit as st

import cqc_cpcc.utilities.brightspace_submissions as bs
from cqc_streamlit_app import utils


def _run(job):
    job.start()
    assert job.done.wait(5)
    return job


@pytest.mark.unit
def test_submissions_job_skips_instructions(monkeypatch) -> None:
    calls = {}

    def _build(url, accepted, **kwargs):
        calls.update(kwargs)
        return "zip-result"

    monkeypatch.setattr(bs, "build_submissions_zip_from_brightspace_url", _build)
    monkeypatch.setattr(bs, "fetch_instructions_from_brightspace_url",
                        lambda *a, **k: pytest.fail("instructions fetched"))

    job = _run(utils._BrightSpaceJob("u", ["java"], MagicMock(), mode="submissions"))
    assert job.error is None
    assert job.result == "zip-result"
    assert calls["fetch_instructions"] is False


@pytest.mark.unit
def test_instructions_job_does_not_download_submissions(monkeypatch) -> None:
    monkeypatch.setattr(bs, "fetch_instructions_from_brightspace_url", lambda *a, **k: "Do X.")
    monkeypatch.setattr(bs, "build_submissions_zip_from_brightspace_url",
                        lambda *a, **k: pytest.fail("submissions downloaded"))

    job = _run(utils._BrightSpaceJob("u", [], MagicMock(), mode="instructions"))
    assert job.error is None
    assert job.instructions == "Do X."
    assert job.result is None


@pytest.fixture
def session_state(monkeypatch):
    state = {}
    monkeypatch.setattr(st, "session_state", state)
    return state


@pytest.mark.unit
def test_sync_default_url_fills_empty_field(session_state) -> None:
    utils._sync_default_url("k", "https://a")
    assert session_state["k"] == "https://a"


@pytest.mark.unit
def test_sync_default_url_follows_default_until_user_edits(session_state) -> None:
    utils._sync_default_url("k", "https://a")
    utils._sync_default_url("k", "https://b")
    assert session_state["k"] == "https://b"

    session_state["k"] = "https://typed"
    utils._sync_default_url("k", "https://c")
    assert session_state["k"] == "https://typed"
