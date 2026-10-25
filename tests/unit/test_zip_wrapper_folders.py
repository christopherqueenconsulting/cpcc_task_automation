#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Test for wrapper folder handling in ZIP extraction."""

import os
import zipfile

import pytest

from cqc_cpcc.utilities.zip_grading_utils import extract_student_submissions_from_zip


@pytest.mark.unit
class TestWrapperFolderHandling:
    """Test handling of wrapper folders in ZIP files."""
    
    @pytest.fixture
    def zip_with_wrapper_folder(self, tmp_path):
        """Create a ZIP with a wrapper folder (common in BrightSpace downloads)."""
        zip_path = tmp_path / "exam_with_wrapper.zip"
        
        with zipfile.ZipFile(zip_path, 'w') as zf:
            # Wrapper folder: "Programming Exam 1"
            # Student folders inside wrapper
            zf.writestr(
                "Programming Exam 1/Student1/Main.java",
                "public class Main { public static void main(String[] args) {} }"
            )
            zf.writestr(
                "Programming Exam 1/Student2/script.py",
                "print('hello world')"
            )
            zf.writestr(
                "Programming Exam 1/Student3/code.cpp",
                "#include <iostream>\nint main() { return 0; }"
            )
        
        return str(zip_path)
    
    @pytest.fixture
    def zip_with_nested_wrapper(self, tmp_path):
        """Create a ZIP with nested wrapper folders."""
        zip_path = tmp_path / "exam_nested.zip"
        
        with zipfile.ZipFile(zip_path, 'w') as zf:
            # Nested wrapper: "Downloads/Programming Exam 1"
            zf.writestr(
                "Downloads/Programming Exam 1/Student1/Main.java",
                "public class Main {}"
            )
            zf.writestr(
                "Downloads/Programming Exam 1/Student2/script.py",
                "print('test')"
            )
        
        return str(zip_path)
    
    @pytest.fixture
    def zip_with_brightspace_and_wrapper(self, tmp_path):
        """Create a ZIP with wrapper folder AND BrightSpace format."""
        zip_path = tmp_path / "exam_brightspace_wrapper.zip"
        
        with zipfile.ZipFile(zip_path, 'w') as zf:
            # Wrapper + BrightSpace delimiter format
            zf.writestr(
                "Programming Exam 1/Assignment - John Doe/Main.java",
                "public class Main {}"
            )
            zf.writestr(
                "Programming Exam 1/Assignment - Jane Smith/script.py",
                "print('test')"
            )
        
        return str(zip_path)
    
    def test_extract_with_wrapper_folder(self, zip_with_wrapper_folder):
        """Test that wrapper folders are detected and handled correctly."""
        accepted_types = ['.java', '.py', '.cpp']
        
        students = extract_student_submissions_from_zip(
            zip_with_wrapper_folder,
            accepted_types
        )
        
        # Should extract 3 students, ignoring wrapper folder
        assert len(students) == 3
        assert "Student1" in students
        assert "Student2" in students
        assert "Student3" in students
        
        # Verify files extracted correctly
        assert "Main.java" in students["Student1"].files
        assert "script.py" in students["Student2"].files
        assert "code.cpp" in students["Student3"].files
    
    def test_extract_with_nested_wrapper(self, zip_with_nested_wrapper):
        """Test handling of nested wrapper folders."""
        accepted_types = ['.java', '.py']
        
        students = extract_student_submissions_from_zip(
            zip_with_nested_wrapper,
            accepted_types
        )
        
        # Should extract 2 students, handling nested wrapper
        assert len(students) == 2
        assert "Student1" in students
        assert "Student2" in students
    
    def test_extract_with_brightspace_format_and_wrapper(self, zip_with_brightspace_and_wrapper):
        """Test BrightSpace format with wrapper folder."""
        accepted_types = ['.java', '.py']
        
        students = extract_student_submissions_from_zip(
            zip_with_brightspace_and_wrapper,
            accepted_types
        )
        
        # Should extract 2 students with correct names from BrightSpace format
        assert len(students) == 2
        assert "John Doe" in students
        assert "Jane Smith" in students
        
        # Verify files
        assert "Main.java" in students["John Doe"].files
        assert "script.py" in students["Jane Smith"].files
    
    def test_no_wrapper_still_works(self, tmp_path):
        """Test that ZIPs without wrapper folders still work."""
        zip_path = tmp_path / "no_wrapper.zip"
        
        with zipfile.ZipFile(zip_path, 'w') as zf:
            # No wrapper - students at top level
            zf.writestr("Student1/Main.java", "public class Main {}")
            zf.writestr("Student2/script.py", "print('test')")
        
        accepted_types = ['.java', '.py']
        
        students = extract_student_submissions_from_zip(
            str(zip_path),
            accepted_types
        )
        
        assert len(students) == 2
        assert "Student1" in students
        assert "Student2" in students
    
    def test_error_message_includes_structure_info(self, tmp_path):
        """Test that error message includes helpful ZIP structure info."""
        zip_path = tmp_path / "malformed.zip"
        
        with zipfile.ZipFile(zip_path, 'w') as zf:
            # Files with no student folders - should fail. (A single folder of files is
            # indistinguishable from one student's folder, so it is kept as that student.)
            zf.writestr("file1.txt", "content")
            zf.writestr("file2.doc", "content")
        
        accepted_types = ['.java', '.py']
        
        with pytest.raises(ValueError) as exc_info:
            extract_student_submissions_from_zip(
                str(zip_path),
                accepted_types
            )
        
        error_msg = str(exc_info.value)
        
        # Error should include helpful information
        assert "No student submissions found" in error_msg
        assert "Expected structure:" in error_msg
        assert "Accepted file types:" in error_msg
        assert "First few files found:" in error_msg


def _write_zip(path, entries):
    with zipfile.ZipFile(path, 'w') as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return str(path)


JAVA = "public class Main { public static void main(String[] args) {} }"


@pytest.mark.unit
class TestResultsAndReviewZipAsIs:
    """The submissions ZIP from Results & review imports as is, even re-zipped on a Mac."""

    def test_mac_rezip_of_export_gives_one_submission_per_student(self, tmp_path):
        entries = {
            "__MACOSX/._brightspace_submissions": b"\x00",
            "brightspace_submissions/.DS_Store": b"\x00",
            "brightspace_submissions/Icon\r": b"",
        }
        for name in ("Student A", "Student B", "Dee Placeholder"):
            entries[f"brightspace_submissions/{name}/Exam1.java"] = JAVA
            entries[f"brightspace_submissions/{name}/Icon\r"] = b""
            entries[f"brightspace_submissions/{name}/.DS_Store"] = b"\x00"
            entries[f"__MACOSX/brightspace_submissions/{name}/._Exam1.java"] = b"\x00"
        entries["brightspace_submissions/Student A/Exam1.class"] = b"\xca\xfe\xba\xbe"
        zip_path = _write_zip(tmp_path / "mac.zip", entries)

        result = extract_student_submissions_from_zip(zip_path, ['.java'])

        assert set(result) == {"Student A", "Student B", "Dee Placeholder"}
        for sub in result.values():
            assert list(sub.files) == ["Exam1.java"]
        assert result["Student A"].rejected_files == ["Exam1.class"]
        assert result["Student B"].rejected_files == []

    def test_multi_file_student_keeps_every_file_including_subfolders(self, tmp_path):
        entries = {f"Student A/{n}.java": JAVA for n in ("Main", "Shape", "Circle", "Square")}
        entries["Student A/src/pkg/Helper.java"] = JAVA
        entries["Student B/Main.java"] = JAVA
        zip_path = _write_zip(tmp_path / "multi.zip", entries)

        result = extract_student_submissions_from_zip(zip_path, ['.java'])

        assert set(result) == {"Student A", "Student B"}
        assert set(result["Student A"].files) == {
            "Main.java", "Shape.java", "Circle.java", "Square.java", "Helper.java"}

    def test_duplicate_file_names_in_subfolders_are_both_kept(self, tmp_path):
        zip_path = _write_zip(tmp_path / "dupes.zip", {
            "Student A/part1/Main.java": "class Main { /* one */ }",
            "Student A/part2/Main.java": "class Main { /* two */ }",
            "Student B/Main.java": JAVA,
        })

        files = extract_student_submissions_from_zip(zip_path, ['.java'])["Student A"].files

        assert set(files) == {"Main.java", "part2/Main.java"}
        contents = {open(p).read() for p in files.values()}
        assert contents == {"class Main { /* one */ }", "class Main { /* two */ }"}

    def test_single_student_inside_export_wrapper_is_the_student(self, tmp_path):
        zip_path = _write_zip(tmp_path / "one.zip", {
            "brightspace_submissions/Dee Placeholder/Exam1.java": JAVA,
        })

        result = extract_student_submissions_from_zip(zip_path, ['.java'])

        assert list(result) == ["Dee Placeholder"]

    def test_full_download_all_feedback_zip_reads_the_nested_submissions(self, tmp_path):
        inner = _write_zip(tmp_path / "inner.zip", {
            "Student A/Exam1.java": JAVA,
            "Student B/Exam1.java": JAVA,
        })
        with open(inner, 'rb') as f:
            inner_bytes = f.read()
        outer = _write_zip(tmp_path / "feedback.zip", {
            "Student A_Feedback.docx": b"docx",
            "Grading_Summary.csv": "Student,Points\n",
            "Student_Submissions/brightspace_submissions.zip": inner_bytes,
        })

        result = extract_student_submissions_from_zip(outer, ['.java', '.docx'])

        assert set(result) == {"Student A", "Student B"}

    def test_brightspace_id_folders_under_unstripped_wrapper_still_parse(self, tmp_path):
        zip_path = _write_zip(tmp_path / "bs.zip", {
            "Exam 1/12345-12345 - Dee Placeholder - Jun 24, 2026 11:21 PM/src/Main.java": JAVA,
        })

        result = extract_student_submissions_from_zip(zip_path, ['.java'])

        assert list(result) == ["Dee Placeholder"]
        assert list(result["Dee Placeholder"].files) == ["Main.java"]


BS_FOLDER = "12345-12345 - Dee Placeholder - Jun 24, 2026 11:21 PM"


def _nested_temp_files():
    import glob
    import tempfile
    return glob.glob(os.path.join(tempfile.gettempdir(), "nested_submissions_*"))


@pytest.mark.unit
class TestNestedZipHardening:
    def _feedback_zip(self, tmp_path, inner_entries, name="feedback.zip"):
        inner = _write_zip(tmp_path / "inner.zip", inner_entries)
        with open(inner, 'rb') as f:
            data = f.read()
        return _write_zip(tmp_path / name, {"Student_Submissions/a.zip": data})

    def test_only_one_nested_level_is_read(self, tmp_path, monkeypatch):
        from cqc_cpcc.utilities import zip_grading_utils as z
        deepest = _write_zip(tmp_path / "b.zip", {"Student A/Main.java": JAVA})
        with open(deepest, 'rb') as f:
            b_bytes = f.read()
        outer = self._feedback_zip(tmp_path, {"Student_Submissions/b.zip": b_bytes})
        calls = []
        real = z._copy_member_capped
        monkeypatch.setattr(z, "_copy_member_capped", lambda *a, **k: calls.append(1) or real(*a, **k))
        before = set(_nested_temp_files())

        result = extract_student_submissions_from_zip(outer, ['.java'])

        assert len(calls) == 1
        assert "Student A" not in result
        assert set(_nested_temp_files()) == before

    def test_over_cap_member_raises_before_any_temp_file(self, tmp_path, monkeypatch):
        from cqc_cpcc.utilities import zip_grading_utils as z
        outer = self._feedback_zip(tmp_path, {"Student A/Main.java": JAVA})
        before = set(_nested_temp_files())
        monkeypatch.setattr(z, "MAX_NESTED_ZIP_BYTES", 10)
        with pytest.raises(ValueError, match="larger than"):
            extract_student_submissions_from_zip(outer, ['.java'])
        assert set(_nested_temp_files()) == before

    def test_chunked_copy_cap_raises_and_temp_file_removed(self, tmp_path, monkeypatch):
        from cqc_cpcc.utilities import zip_grading_utils as z
        outer = self._feedback_zip(tmp_path, {"Student A/Main.java": JAVA})
        before = set(_nested_temp_files())
        real_copy = z._copy_member_capped
        monkeypatch.setattr(z, "_copy_member_capped", lambda s, d, limit=10: real_copy(s, d, 10))
        with pytest.raises(ValueError, match="larger than"):
            extract_student_submissions_from_zip(outer, ['.java'])
        assert set(_nested_temp_files()) == before

    def test_copy_failure_leaves_no_temp_file(self, tmp_path, monkeypatch):
        from cqc_cpcc.utilities import zip_grading_utils as z
        outer = self._feedback_zip(tmp_path, {"Student A/Main.java": JAVA})
        before = set(_nested_temp_files())

        def boom(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr(z, "_copy_member_capped", boom)
        with pytest.raises(OSError):
            extract_student_submissions_from_zip(outer, ['.java'])
        assert set(_nested_temp_files()) == before

    def test_label_cleaning_drops_dotdot_and_control_chars(self):
        from cqc_cpcc.utilities.zip_grading_utils import _clean_label
        assert _clean_label("../../etc/pa\x00ss\nwd.java") == "etc/passwd.java"
        assert _clean_label("..") == "file"

    def test_absolute_path_and_backslash_entries(self, tmp_path):
        zip_path = _write_zip(tmp_path / "odd.zip", {
            "/abs/Main.java": JAVA,
            "Student A\\Main.java": JAVA,
            "Student B\\Main.java": JAVA,
        })
        result = extract_student_submissions_from_zip(zip_path, ['.java'])
        assert set(result) == {"Student A", "Student B"}

    def test_warns_on_two_nested_zips_and_student_folders_beside(self, tmp_path, caplog):
        inner = _write_zip(tmp_path / "inner.zip", {"Student A/Main.java": JAVA})
        with open(inner, 'rb') as f:
            data = f.read()
        outer = _write_zip(tmp_path / "two.zip", {
            "Student_Submissions/a.zip": data,
            "Student_Submissions/b.zip": data,
            "Student B/Main.java": JAVA,
        })
        with caplog.at_level("WARNING"):
            result = extract_student_submissions_from_zip(outer, ['.java'])
        assert set(result) == {"Student A"}
        text = caplog.text
        assert "2 ZIPs found" in text
        assert "student folders as well as a nested submissions ZIP" in text


@pytest.mark.unit
class TestSingleStudentIsNotAWrapper:
    @pytest.mark.parametrize("files, student", [
        (["Student A/src/Main.java", "Student A/lib/Util.java"], "Student A"),
        (["Student A/src/Main.java", "Student A/lib/Util.java", "Student A/Main.java"], "Student A"),
        ([f"{BS_FOLDER}/src/A.java", f"{BS_FOLDER}/lib/B.java"], "Dee Placeholder"),
        (["Student A/src/a/X.java", "Student A/src/b/Y.java"], "Student A"),
    ])
    def test_one_student_keeps_all_files(self, tmp_path, files, student):
        zip_path = _write_zip(tmp_path / "one.zip", {f: JAVA for f in files})
        result = extract_student_submissions_from_zip(zip_path, ['.java'])
        assert list(result) == [student]
        assert len(result[student].files) == len(files)

    def test_deeper_segment_with_dash_is_not_a_student(self, tmp_path):
        zip_path = _write_zip(tmp_path / "lab.zip", {
            "Student A/Lab - 1/Main.java": JAVA,
            "Student B/Main.java": JAVA,
        })
        assert set(extract_student_submissions_from_zip(zip_path, ['.java'])) == {"Student A", "Student B"}

    def test_brightspace_folder_under_unstripped_outer_still_wins(self, tmp_path):
        zip_path = _write_zip(tmp_path / "bs.zip", {f"Exam 1/{BS_FOLDER}/src/Main.java": JAVA})
        assert list(extract_student_submissions_from_zip(zip_path, ['.java'])) == ["Dee Placeholder"]


@pytest.mark.unit
class TestSkippedRootFiles:
    def test_root_level_file_explained_in_error(self, tmp_path):
        zip_path = _write_zip(tmp_path / "root.zip", {"Main.java": JAVA})
        with pytest.raises(ValueError) as exc:
            extract_student_submissions_from_zip(zip_path, ['.java'])
        assert ("Files at the ZIP root or directly in an outer folder are skipped; "
                "put each student's files in their own folder.") in str(exc.value)
