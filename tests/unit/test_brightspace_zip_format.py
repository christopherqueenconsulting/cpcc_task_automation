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
    
    def test_index_html_filtered_even_when_html_accepted(self, tmp_path):
        """Test that index.html is filtered even when html files are accepted."""
        zip_path = tmp_path / "test_index_html.zip"
        
        with zipfile.ZipFile(zip_path, 'w') as zf:
            # BrightSpace format with wrapper and index.html
            zf.writestr(
                "Programming Exam/index.html",
                "<html>BrightSpace Index</html>"
            )
            zf.writestr(
                "Programming Exam/12345 - Student One/assignment.html",
                "<html>Student work</html>"
            )
            zf.writestr(
                "Programming Exam/12346 - Student Two/Main.java",
                "public class Main {}"
            )
        
        # Accept html files
        accepted_types = ["html", "java"]
        
        students = extract_student_submissions_from_zip(
            str(zip_path),
            accepted_types
        )
        
        # Should have 2 students
        assert len(students) == 2
        assert "Student One" in students
        assert "Student Two" in students
        
        # Student One should have assignment.html (not index.html)
        assert "assignment.html" in students["Student One"].files
        assert "index.html" not in students["Student One"].files
        
        # Verify index.html wasn't treated as a separate student
        assert "index" not in students
        assert "html" not in students
    
    def test_index_htm_also_filtered(self, tmp_path):
        """Test that index.htm (alternative format) is also filtered."""
        zip_path = tmp_path / "test_index_htm.zip"
        
        with zipfile.ZipFile(zip_path, 'w') as zf:
            zf.writestr("Exam/index.htm", "<html>Index</html>")
            zf.writestr("Exam/12345 - Student/page.htm", "<html>Page</html>")
        
        accepted_types = ["htm", "html"]
        
        students = extract_student_submissions_from_zip(
            str(zip_path),
            accepted_types
        )
        
        # Should have 1 student with page.htm only
        assert len(students) == 1
        assert "Student" in students
        assert "page.htm" in students["Student"].files
        assert "index.htm" not in students["Student"].files
    
    def test_index_html_case_insensitive(self, tmp_path):
        """Test that index.html filtering is case-insensitive."""
        zip_path = tmp_path / "test_case_insensitive.zip"
        
        with zipfile.ZipFile(zip_path, 'w') as zf:
            zf.writestr("Exam/INDEX.HTML", "<html>Index</html>")
            zf.writestr("Exam/Index.Html", "<html>Index</html>")
            zf.writestr("Exam/12345 - Student/Page.HTML", "<html>Page</html>")
        
        accepted_types = ["html"]
        
        students = extract_student_submissions_from_zip(
            str(zip_path),
            accepted_types
        )
        
        # Should have 1 student
        assert len(students) == 1
        assert "Student" in students
        # Should only have the actual student file
        assert "Page.HTML" in students["Student"].files
