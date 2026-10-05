#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""The Pexels placeholder client is created lazily and needs PEXELS_API_KEY."""

from unittest.mock import MagicMock

import pytest

from cqc_streamlit_app import pexels_helper


@pytest.fixture(autouse=True)
def fresh_client(monkeypatch):
    monkeypatch.setattr(pexels_helper, "_api", None)


@pytest.mark.unit
def test_missing_key_raises_only_when_used(monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="PEXELS_API_KEY"):
        pexels_helper.get_photo("landscape")


@pytest.mark.unit
def test_get_photo_picks_one_search_result(monkeypatch):
    client = MagicMock()
    client.get_entries.return_value = ["photo-a", "photo-b"]
    api_class = MagicMock(return_value=client)
    monkeypatch.setattr(pexels_helper, "API", api_class)
    monkeypatch.setenv("PEXELS_API_KEY", "key")

    assert pexels_helper.get_photo("landscape") in ("photo-a", "photo-b")
    client.search.assert_called_once_with("landscape", page=1, results_per_page=25)

    pexels_helper.get_photo("landscape")
    api_class.assert_called_once_with("key")  # the client is reused
