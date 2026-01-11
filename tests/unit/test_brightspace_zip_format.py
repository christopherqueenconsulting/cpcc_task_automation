#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Test for BrightSpace ZIP format compatibility."""

import zipfile

import pytest

from cqc_cpcc.utilities.zip_grading_utils import extract_student_submissions_from_zip


@pytest.mark.unit
class TestBrightSpaceZIPFormat:
    """Test handling of actual BrightSpace ZIP download format."""
    
    @pytest.fixture
    def brightspace_zip_with_timestamps(self, tmp_path):
        """Create a ZIP matching BrightSpace's actual format with timestamps."""
        zip_path = tmp_path / "exam_brightspace_real.zip"
        
        with zipfile.ZipFile(zip_path, 'w') as zf:
            # BrightSpace format: "ID - Student Name - Timestamp"
            zf.writestr(
                "Programming Exam 1/10001-500001 - Ada Example - Oct 10, 2025 1022 AM/Main.java",
                "public class Main { public static void main(String[] args) {} }"
            )
            zf.writestr(
                "Programming Exam 1/10002-500002 - Jane Smith - Oct 11, 2025 1130 AM/script.py",
                "print('hello world')"
            )
            # Add some noise files
            zf.writestr(
                "Programming Exam 1/index.html",
                "<html>Index</html>"
            )
            zf.writestr(
                "Programming Exam 1/.DS_Store",
                "binary data"
            )
            zf.writestr(
                "__MACOSX/Programming Exam 1/._index.html",
                "mac metadata"
            )
        
        return str(zip_path)
    
    @pytest.fixture
    def brightspace_zip_no_wrapper(self, tmp_path):
        """Create a ZIP with BrightSpace format but no wrapper folder."""
        zip_path = tmp_path / "exam_brightspace_no_wrapper.zip"
        
        with zipfile.ZipFile(zip_path, 'w') as zf:
            zf.writestr(
                "12345 - John Doe - Jan 5, 2026 900 AM/Main.java",
                "public class Main {}"
            )
            zf.writestr(
                "12346 - Jane Doe - Jan 5, 2026 905 AM/script.py",
                "print('test')"
            )
        
        return str(zip_path)
    
    def test_extract_brightspace_format_with_timestamps(self, brightspace_zip_with_timestamps):
        """Test extraction from BrightSpace ZIP with ID - Name - Timestamp format."""
        # Test with extensions without dots (as Streamlit passes them)
        accepted_types = ["java", "py", "txt"]
        
        students = extract_student_submissions_from_zip(
            brightspace_zip_with_timestamps,
            accepted_types
        )
        
        # Should extract 2 students, using middle part as name
        assert len(students) == 2
        assert "Ada Example" in students
        assert "Jane Smith" in students
        
        # Verify files extracted correctly
        assert "Main.java" in students["Ada Example"].files
        assert "script.py" in students["Jane Smith"].files
    
    def test_extract_brightspace_no_wrapper(self, brightspace_zip_no_wrapper):
        """Test BrightSpace format without wrapper folder."""
        accepted_types = ["java", "py"]
        
        students = extract_student_submissions_from_zip(
            brightspace_zip_no_wrapper,
            accepted_types
        )
        
        assert len(students) == 2
        assert "John Doe" in students
        assert "Jane Doe" in students
    
    def test_accepted_types_without_dots(self, tmp_path):
        """Test that accepted types without dots work correctly."""
        zip_path = tmp_path / "test_no_dots.zip"
        
        with zipfile.ZipFile(zip_path, 'w') as zf:
            zf.writestr("Student1/Main.java", "public class Main {}")
            zf.writestr("Student1/Helper.cpp", "#include <iostream>")
            zf.writestr("Student1/README.txt", "readme")
            zf.writestr("Student1/data.csv", "data")  # Should be excluded
        
        # Pass extensions without dots (as Streamlit does)
        accepted_types = ["java", "cpp", "txt"]
        
        students = extract_student_submissions_from_zip(
            str(zip_path),
            accepted_types
        )
        
        assert len(students) == 1
        student = students["Student1"]
        
        # Should have 3 files (not csv)
        assert len(student.files) == 3
        assert "Main.java" in student.files
        assert "Helper.cpp" in student.files
        assert "README.txt" in student.files
        assert "data.csv" not in student.files
    
    def test_accepted_types_with_dots(self, tmp_path):
        """Test that accepted types with dots also work correctly."""
        zip_path = tmp_path / "test_with_dots.zip"
        
        with zipfile.ZipFile(zip_path, 'w') as zf:
            zf.writestr("Student1/Main.java", "public class Main {}")
            zf.writestr("Student1/script.py", "print('test')")
        
        # Pass extensions with dots
        accepted_types = [".java", ".py"]
        
        students = extract_student_submissions_from_zip(
            str(zip_path),
            accepted_types
        )
        
        assert len(students) == 1
        assert len(students["Student1"].files) == 2
    
    def test_mixed_accepted_types_format(self, tmp_path):
        """Test that mixed format (some with dots, some without) works."""
        zip_path = tmp_path / "test_mixed.zip"
        
        with zipfile.ZipFile(zip_path, 'w') as zf:
            zf.writestr("Student1/Main.java", "public class Main {}")
            zf.writestr("Student1/script.py", "print('test')")
            zf.writestr("Student1/code.cpp", "#include")
        
        # Mixed format
        accepted_types = ["java", ".py", "cpp"]
        
        students = extract_student_submissions_from_zip(
            str(zip_path),
            accepted_types
        )
        
        assert len(students) == 1
        assert len(students["Student1"].files) == 3
