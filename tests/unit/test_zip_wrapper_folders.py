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
            # Files with no student folders - should fail
            zf.writestr("Wrapper/file1.txt", "content")
            zf.writestr("Wrapper/file2.doc", "content")
        
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
