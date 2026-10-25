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
