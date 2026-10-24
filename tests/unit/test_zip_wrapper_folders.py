#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Test for wrapper folder handling in ZIP extraction."""

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
