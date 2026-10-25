"""Zip-bomb limits for ordinary member extraction (synthetic in-memory ZIPs only)."""

import glob
import io
import os
import tempfile
import zipfile

import pytest

from cqc_cpcc.utilities import zip_grading_utils as z
from cqc_cpcc.utilities.zip_grading_utils import extract_student_submissions_from_zip

JAVA = "public class Main { public static void main(String[] args) {} }"


def _write_zip(path, entries):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return str(path)


def _temp_files():
    pattern = os.path.join(tempfile.gettempdir(), "from_zip_*")
    return set(glob.glob(pattern)) | set(
        glob.glob(os.path.join(tempfile.gettempdir(), "nested_submissions_*")))


@pytest.mark.unit
class TestMemberLimits:
    def test_normal_zip_is_unchanged(self, tmp_path):
        zip_path = _write_zip(tmp_path / "ok.zip", {
            "Student A/Main.java": JAVA, "Student B/Main.java": JAVA})
        result = extract_student_submissions_from_zip(zip_path, ['.java'])
        assert set(result) == {"Student A", "Student B"}

    def test_compressible_member_over_cap_is_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(z, "MAX_MEMBER_BYTES", 1000)
        zip_path = _write_zip(tmp_path / "big.zip", {
            "Student A/Main.java": JAVA, "Student B/Big.java": "a" * 5000})
        before = _temp_files()
        with pytest.raises(ValueError, match="zip bomb"):
            extract_student_submissions_from_zip(zip_path, ['.java'])
        assert _temp_files() == before

    def test_huge_ratio_member_is_rejected_before_any_read(self, tmp_path):
        zip_path = _write_zip(tmp_path / "ratio.zip", {
            "Student A/Main.java": JAVA, "Student B/Zeros.txt": "0" * (3 * 1024 * 1024)})
        before = _temp_files()
        with pytest.raises(ValueError, match="compressed more than 100:1"):
            extract_student_submissions_from_zip(zip_path, ['.java', '.txt'])
        assert _temp_files() == before

    def test_lying_header_is_caught_by_the_streamed_count(self, tmp_path, monkeypatch):
        # The header check is bypassed, as if the header understated the size: only the
        # bytes actually read count.
        monkeypatch.setattr(z, "_check_member_before_read", lambda info, used: None)
        monkeypatch.setattr(z, "MAX_MEMBER_BYTES", 1000)
        zip_path = _write_zip(tmp_path / "lie.zip", {"Student A/Big.java": "a" * 5000})
        before = _temp_files()
        with pytest.raises(ValueError, match="expands to more than"):
            extract_student_submissions_from_zip(zip_path, ['.java'])
        assert _temp_files() == before

    def test_copy_counts_real_bytes(self):
        dst = io.BytesIO()
        with pytest.raises(ValueError, match="custom message"):
            z._copy_member_capped(io.BytesIO(b"x" * 3_000_000), dst, 2_000_000, too_big="custom message")

    def test_too_many_entries(self, tmp_path, monkeypatch):
        monkeypatch.setattr(z, "MAX_ENTRY_COUNT", 5)
        zip_path = _write_zip(tmp_path / "many.zip", {f"S{i}/Main.java": JAVA for i in range(6)})
        before = _temp_files()
        with pytest.raises(ValueError, match="more than 5 entries"):
            extract_student_submissions_from_zip(zip_path, ['.java'])
        assert _temp_files() == before

    def test_total_budget_across_students_and_temp_files_removed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(z, "MAX_MEMBER_BYTES", 2000)
        monkeypatch.setattr(z, "MAX_TOTAL_UNCOMPRESSED_BYTES", 3000)
        body = "int x = 1; // " + "abcdefghij" * 115  # ~1160 bytes
        zip_path = _write_zip(tmp_path / "total.zip", {f"Student {c}/Main.java": body for c in "ABC"})
        before = _temp_files()
        with pytest.raises(ValueError, match="in total"):
            extract_student_submissions_from_zip(zip_path, ['.java'])
        assert _temp_files() == before

    def test_total_budget_includes_the_nested_zip(self, tmp_path, monkeypatch):
        body = "int x = 1; // " + "abcdefghij" * 115
        inner = _write_zip(tmp_path / "inner.zip", {f"Student {c}/Main.java": body for c in "AB"})
        with open(inner, 'rb') as f:
            data = f.read()
        outer = _write_zip(tmp_path / "outer.zip", {"Student_Submissions/a.zip": data})
        # Room for the inner ZIP copy plus ONE member, not two.
        monkeypatch.setattr(z, "MAX_TOTAL_UNCOMPRESSED_BYTES", len(data) + len(body) + 100)
        before = _temp_files()
        with pytest.raises(ValueError, match="in total"):
            extract_student_submissions_from_zip(outer, ['.java'])
        assert _temp_files() == before

    def test_temp_files_removed_when_no_students_are_found(self, tmp_path):
        zip_path = _write_zip(tmp_path / "root.zip", {"Main.java": JAVA})
        before = _temp_files()
        with pytest.raises(ValueError):
            extract_student_submissions_from_zip(zip_path, ['.java'])
        assert _temp_files() == before

    def test_temp_files_removed_when_a_later_copy_fails(self, tmp_path, monkeypatch):
        zip_path = _write_zip(tmp_path / "two.zip", {
            "Student A/Main.java": JAVA, "Student B/Main.java": JAVA})
        real = z._copy_member_capped
        calls = []

        def flaky(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise OSError("disk full")
            return real(*args, **kwargs)
        monkeypatch.setattr(z, "_copy_member_capped", flaky)
        before = _temp_files()
        with pytest.raises(OSError):
            extract_student_submissions_from_zip(zip_path, ['.java'])
        assert _temp_files() == before

    def test_no_submissions_error_escapes_control_characters(self, tmp_path):
        zip_path = _write_zip(tmp_path / "evil.zip", {"evil\nname\x85.java": JAVA})
        with pytest.raises(ValueError) as exc:
            extract_student_submissions_from_zip(zip_path, ['.java'])
        text = str(exc.value)
        assert "evil\nname" not in text and "\x85" not in text
        assert "evil\\nname\\x85.java" in text


def _eocd(entries, comment=b""):
    import struct
    return b"PK\x05\x06" + struct.pack("<HHHHLLH", 0, 0, entries, entries, 0, 0, len(comment)) + comment


@pytest.mark.unit
class TestPreOpenChecks:
    def _no_zipfile(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("ZipFile must not be built")
        monkeypatch.setattr(z.zipfile, "ZipFile", boom)

    def test_eocd_claiming_too_many_entries_is_rejected_without_zipfile(self, tmp_path, monkeypatch):
        path = tmp_path / "claims.zip"
        path.write_bytes(b"junk" * 10 + _eocd(11000))
        self._no_zipfile(monkeypatch)
        with pytest.raises(ValueError, match="more than 10,000 entries"):
            extract_student_submissions_from_zip(str(path), ['.java'])

    def test_zip64_record_claiming_too_many_entries(self, tmp_path, monkeypatch):
        import struct
        record = struct.pack("<4sQHHIIQQQQ", b"PK\x06\x06", 44, 45, 45, 0, 0, 11000, 11000, 0, 0)
        locator = struct.pack("<4sIQI", b"PK\x06\x07", 0, 0, 1)
        path = tmp_path / "z64.zip"
        path.write_bytes(record + locator + _eocd(0xFFFF))
        self._no_zipfile(monkeypatch)
        with pytest.raises(ValueError, match="more than 10,000 entries"):
            extract_student_submissions_from_zip(str(path), ['.java'])

    def test_oversized_file_is_rejected_without_zipfile(self, tmp_path, monkeypatch):
        path = _write_zip(tmp_path / "ok.zip", {"Student A/Main.java": JAVA})
        monkeypatch.setattr(z, "MAX_ZIP_BYTES", 10)
        self._no_zipfile(monkeypatch)
        with pytest.raises(ValueError, match="larger than"):
            extract_student_submissions_from_zip(path, ['.java'])

    def test_not_a_zip_still_reports_bad_zip(self, tmp_path):
        path = tmp_path / "bad.zip"
        path.write_bytes(b"not a zip at all")
        with pytest.raises(zipfile.BadZipFile):
            extract_student_submissions_from_zip(str(path), ['.java'])


@pytest.mark.unit
class TestLimitConfig:
    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("CQC_ZIP_MAX_MEMBER_MB", "7")
        assert z._limit_from_env("CQC_ZIP_MAX_MEMBER_MB", 100) == 7 * 1024 * 1024

    @pytest.mark.parametrize("bad", ["abc", "0", "-5", "99999999", "1.5"])
    def test_bad_env_falls_back_to_default(self, monkeypatch, bad):
        monkeypatch.setenv("CQC_ZIP_MAX_TOTAL_MB", bad)
        assert z._limit_from_env("CQC_ZIP_MAX_TOTAL_MB", 256) == 256 * 1024 * 1024

    def test_unset_env_uses_default(self, monkeypatch):
        monkeypatch.delenv("CQC_ZIP_MAX_TOTAL_MB", raising=False)
        assert z._limit_from_env("CQC_ZIP_MAX_TOTAL_MB", 1024) == 1024 * 1024 * 1024

    def test_cloud_detection_uses_mount_src(self, monkeypatch):
        from pathlib import Path
        monkeypatch.setattr(Path, "exists", lambda self: str(self) == "/mount/src")
        assert z._on_community_cloud() is True

    def test_copy_default_limit_is_read_at_call_time(self, monkeypatch):
        monkeypatch.setattr(z, "MAX_NESTED_ZIP_BYTES", 10)
        with pytest.raises(ValueError, match="inside the upload"):
            z._copy_member_capped(io.BytesIO(b"x" * 100), io.BytesIO())

    def test_member_over_1_mib_with_zero_compress_size_is_rejected(self):
        info = zipfile.ZipInfo("Student A/Big.java")
        info.file_size = 2 * 1024 * 1024
        info.compress_size = 0
        with pytest.raises(ValueError, match="compressed more than"):
            z._check_member_before_read(info, 0)


@pytest.mark.unit
class TestRebuildPathIsBounded:
    def test_rebuild_normal(self, tmp_path):
        from cqc_streamlit_app.utils import _rebuild_zip_from_kept
        src = _write_zip(tmp_path / "src.zip", {"Student A/Main.java": JAVA, "Student B/Main.java": JAVA})
        out = _rebuild_zip_from_kept(src, {"Student A/Main.java"})
        with zipfile.ZipFile(out) as zf:
            assert zf.namelist() == ["Student A/Main.java"]
            assert zf.read("Student A/Main.java").decode() == JAVA
        os.unlink(out)

    def test_rebuild_over_cap_raises_and_removes_output(self, tmp_path, monkeypatch):
        from cqc_streamlit_app import utils
        src = _write_zip(tmp_path / "src.zip", {"Student A/Big.java": "a" * 5000})
        monkeypatch.setattr(z, "MAX_MEMBER_BYTES", 1000)
        created = []
        real = tempfile.NamedTemporaryFile

        def tracking(*a, **k):
            f = real(*a, **k)
            created.append(f.name)
            return f
        monkeypatch.setattr(utils.tempfile, "NamedTemporaryFile", tracking)
        with pytest.raises(ValueError, match="zip bomb"):
            utils._rebuild_zip_from_kept(src, {"Student A/Big.java"})
        assert created and not any(os.path.exists(p) for p in created)

    def test_rebuild_rejects_too_many_entries_before_opening(self, tmp_path):
        from cqc_streamlit_app import utils
        path = tmp_path / "claims.zip"
        path.write_bytes(_eocd(11000))
        with pytest.raises(ValueError, match="more than 10,000 entries"):
            utils._rebuild_zip_from_kept(str(path), {"x"})


@pytest.mark.unit
class TestZipPathConfinement:
    def test_zip_outside_allowed_roots_is_refused(self):
        with pytest.raises(ValueError, match="Refusing to read"):
            extract_student_submissions_from_zip("/etc/not-a-real-upload.zip", ['.java'])
