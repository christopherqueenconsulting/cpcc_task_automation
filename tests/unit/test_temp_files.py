#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Unit tests for the private, self-purging app temp directory."""

import os
import tempfile
import time

import pytest

from cqc_cpcc.utilities import temp_files


@pytest.fixture(autouse=True)
def restore_tempdir():
    original = tempfile.tempdir
    temp_files._reset_for_tests()
    yield
    tempfile.tempdir = original
    temp_files._reset_for_tests()


@pytest.mark.unit
class TestConfigureAppTempdir:
    def test_routes_tempfile_into_a_private_dir(self, tmp_path):
        root = temp_files.configure_app_tempdir(str(tmp_path))
        assert root == os.path.join(str(tmp_path), temp_files.APP_TEMP_DIRNAME)
        assert (os.stat(root).st_mode & 0o777) == 0o700
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            assert os.path.dirname(handle.name) == root
        assert os.path.dirname(tempfile.mkdtemp()) == root

    def test_purges_stale_items_on_first_call(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CQC_TEMP_RETENTION_HOURS", "24")
        root = tmp_path / temp_files.APP_TEMP_DIRNAME
        (root / "bs_extract_old").mkdir(parents=True)
        (root / "bs_extract_old" / "Main.java").write_text("x")
        stale_file = root / "browser_mfa_old.png"
        stale_file.write_text("x")
        fresh = root / "bs_extract_new"
        fresh.mkdir()
        old = time.time() - 48 * 3600
        for path in (root / "bs_extract_old", stale_file):
            os.utime(path, (old, old))

        temp_files.configure_app_tempdir(str(tmp_path))
        assert fresh.exists()
        assert not (root / "bs_extract_old").exists()
        assert not stale_file.exists()

    def test_idempotent(self, tmp_path):
        first = temp_files.configure_app_tempdir(str(tmp_path))
        assert temp_files.configure_app_tempdir(str(tmp_path / "other")) == first


@pytest.mark.unit
class TestPurgeStale:
    def test_zero_retention_keeps_everything(self, tmp_path):
        (tmp_path / "a").write_text("x")
        assert temp_files.purge_stale(str(tmp_path), 0) == 0

    def test_missing_root(self, tmp_path):
        assert temp_files.purge_stale(str(tmp_path / "missing"), 24) == 0
