#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for ZIP-based student batch grading utilities."""

import zipfile

import pytest

from cqc_cpcc.utilities.zip_grading_utils import (
    estimate_tokens,
    should_ignore_file,
    get_file_priority,
    extract_student_submissions_from_zip,
    build_submission_text_with_token_limit,
    CHARS_PER_TOKEN,
)


@pytest.mark.unit
class TestTokenEstimation:
    """Test token estimation utilities."""
    
    def test_estimate_tokens_empty_string(self):
        """Test token estimation for empty string."""
        assert estimate_tokens("") == 0
    
    def test_estimate_tokens_short_text(self):
        """Test token estimation for short text."""
        text = "Hello world"
        tokens = estimate_tokens(text)
        expected = len(text) // CHARS_PER_TOKEN
        assert tokens == expected
    
    def test_estimate_tokens_long_text(self):
        """Test token estimation for longer text."""
        text = "This is a longer piece of text " * 100
        tokens = estimate_tokens(text)
        assert tokens > 0
        assert tokens == len(text) // CHARS_PER_TOKEN


@pytest.mark.unit
class TestFileFiltering:
    """Test file filtering logic."""
    
    def test_should_ignore_macosx_files(self):
        """Test that __MACOSX files are ignored."""
        assert should_ignore_file("__MACOSX/file.txt") is True
        assert should_ignore_file("student1/__MACOSX/file.txt") is True
    
    def test_should_ignore_git_directory(self):
        """Test that .git directories are ignored."""
        assert should_ignore_file(".git/config") is True
        assert should_ignore_file("student1/.git/file.txt") is True
    
    def test_should_ignore_node_modules(self):
        """Test that node_modules is ignored."""
        assert should_ignore_file("node_modules/package.json") is True
        assert should_ignore_file("student1/node_modules/lib.js") is True
    
    def test_should_ignore_metadata_files(self):
        """Test that metadata files are ignored."""
        assert should_ignore_file("._file.txt") is True
        assert should_ignore_file(".DS_Store") is True
        assert should_ignore_file("Thumbs.db") is True
    
    def test_should_ignore_binary_files(self):
        """Test that binary files are ignored."""
        assert should_ignore_file("program.exe") is True
        assert should_ignore_file("library.jar") is True
        assert should_ignore_file("image.jpg") is True
        assert should_ignore_file("video.mp4") is True
    
    def test_should_not_ignore_source_files(self):
        """Test that source files are not ignored."""
        assert should_ignore_file("Main.java") is False
        assert should_ignore_file("script.py") is False
        assert should_ignore_file("program.cpp") is False
        assert should_ignore_file("README.txt") is False


@pytest.mark.unit
class TestFilePriority:
    """Test file priority scoring."""
    
    def test_java_has_high_priority(self):
        """Test that Java files have high priority."""
        priority = get_file_priority("Main.java")
        assert priority == 100
    
    def test_python_has_high_priority(self):
        """Test that Python files have high priority."""
        priority = get_file_priority("script.py")
        assert priority == 100
    
    def test_txt_has_medium_priority(self):
        """Test that text files have medium priority."""
        priority = get_file_priority("README.txt")
        assert priority == 50
    
    def test_docx_has_lower_priority(self):
        """Test that documents have lower priority."""
        priority = get_file_priority("report.docx")
        assert priority == 30
    
    def test_unknown_extension_has_zero_priority(self):
        """Test that unknown extensions have zero priority."""
        priority = get_file_priority("file.xyz")
        assert priority == 0


@pytest.mark.unit
class TestZIPExtraction:
    """Test ZIP extraction with fixtures."""
    
    @pytest.fixture
    def sample_zip_simple(self, tmp_path):
        """Create a simple test ZIP with two students."""
        zip_path = tmp_path / "simple_submission.zip"
        
        with zipfile.ZipFile(zip_path, 'w') as zf:
            # Student 1: two Java files
            zf.writestr("Student1/Main.java", "public class Main { /* code */ }")
            zf.writestr("Student1/Helper.java", "public class Helper { /* code */ }")
            
            # Student 2: one Python file
            zf.writestr("Student2/script.py", "print('hello world')")
        
        return str(zip_path)
    
    @pytest.fixture
    def sample_zip_brightspace_format(self, tmp_path):
        """Create a ZIP with BrightSpace naming format."""
        zip_path = tmp_path / "brightspace_submission.zip"
        
        with zipfile.ZipFile(zip_path, 'w') as zf:
            # BrightSpace format: "Assignment - Student Name"
            zf.writestr("Assignment1 - John Doe/Main.java", "public class Main {}")
            zf.writestr("Assignment1 - Jane Smith/script.py", "print('test')")
        
        return str(zip_path)
    
    @pytest.fixture
    def sample_zip_with_noise(self, tmp_path):
        """Create a ZIP with noise files that should be filtered."""
        zip_path = tmp_path / "noisy_submission.zip"
        
        with zipfile.ZipFile(zip_path, 'w') as zf:
            # Valid files
            zf.writestr("Student1/Main.java", "public class Main {}")
            
            # Files to ignore
            zf.writestr("Student1/__MACOSX/._Main.java", "metadata")
            zf.writestr("Student1/.DS_Store", "")
            zf.writestr("Student1/node_modules/lib.js", "// library")
            zf.writestr("Student1/image.jpg", b"\xff\xd8\xff")  # Binary
        
        return str(zip_path)
    
    def test_extract_simple_zip(self, sample_zip_simple):
        """Test extraction of simple ZIP structure."""
        accepted_types = ['.java', '.py']
        
        students = extract_student_submissions_from_zip(
            sample_zip_simple,
            accepted_types
        )
        
        assert len(students) == 2
        assert "Student1" in students
        assert "Student2" in students
        
        # Check Student1
        student1 = students["Student1"]
        assert len(student1.files) == 2
        assert "Main.java" in student1.files
        assert "Helper.java" in student1.files
        assert student1.estimated_tokens > 0
        assert not student1.is_truncated
        
        # Check Student2
        student2 = students["Student2"]
        assert len(student2.files) == 1
        assert "script.py" in student2.files
    
    def test_extract_brightspace_format(self, sample_zip_brightspace_format):
        """Test extraction of BrightSpace format ZIP."""
        accepted_types = ['.java', '.py']
        
        students = extract_student_submissions_from_zip(
            sample_zip_brightspace_format,
            accepted_types
        )
        
        assert len(students) == 2
        assert "John Doe" in students
        assert "Jane Smith" in students
    
    def test_extract_filters_noise(self, sample_zip_with_noise):
        """Test that noise files are filtered out."""
        accepted_types = ['.java', '.py', '.js']
        
        students = extract_student_submissions_from_zip(
            sample_zip_with_noise,
            accepted_types
        )
        
        assert len(students) == 1
        student = students["Student1"]
        
        # Only Main.java should be extracted
        assert len(student.files) == 1
        assert "Main.java" in student.files
        
        # Noise files should not be present
        assert "._Main.java" not in student.files
        assert ".DS_Store" not in student.files
        assert "lib.js" not in student.files
        assert "image.jpg" not in student.files
    
    def test_extract_respects_accepted_types(self, sample_zip_simple):
        """Test that only accepted file types are extracted."""
        # Only accept Java files
        accepted_types = ['.java']
        
        students = extract_student_submissions_from_zip(
            sample_zip_simple,
            accepted_types
        )
        
        # Student1 has Java files
        assert "Student1" in students
        assert len(students["Student1"].files) == 2
        
        # Student2 only has Python, should not appear
        assert "Student2" not in students
    
    def test_extract_empty_zip_raises_error(self, tmp_path):
        """Test that empty ZIP raises error."""
        empty_zip = tmp_path / "empty.zip"
        with zipfile.ZipFile(empty_zip, 'w'):
            pass  # Create empty ZIP
        
        with pytest.raises(ValueError, match="No student submissions found"):
            extract_student_submissions_from_zip(
                str(empty_zip),
                ['.java', '.py']
            )
    
    def test_extract_with_token_budget(self, tmp_path):
        """Test that token budget limits files extracted."""
        zip_path = tmp_path / "large_submission.zip"
        
        # Create a ZIP with files that exceed budget
        large_content = "x" * 10000  # ~2500 tokens
        with zipfile.ZipFile(zip_path, 'w') as zf:
            zf.writestr("Student1/file1.java", large_content)
            zf.writestr("Student1/file2.java", large_content)
            zf.writestr("Student1/file3.java", large_content)
            zf.writestr("Student1/file4.java", large_content)
        
        # Set low token budget
        max_tokens = 6000  # Should fit ~2-3 files
        
        students = extract_student_submissions_from_zip(
            str(zip_path),
            ['.java'],
            max_tokens_per_student=max_tokens
        )
        
        student = students["Student1"]
        
        # No truncation - preprocessing handles large submissions
        assert len(student.files) == 4
        assert not student.is_truncated
        assert student.omitted_files == []
        assert student.estimated_tokens > max_tokens


@pytest.mark.unit
class TestSubmissionTextBuilding:
    """Test submission text building."""
    
    def test_build_submission_text_single_file(self, tmp_path):
        """Test building submission text with single file."""
        # Create temp file
        temp_file = tmp_path / "test.java"
        temp_file.write_text("public class Test {}")
        
        files = {"test.java": str(temp_file)}
        
        text = build_submission_text_with_token_limit(files)
        
        assert "FILE: test.java" in text
        assert "public class Test {}" in text
    
    def test_build_submission_text_multiple_files(self, tmp_path):
        """Test building submission text with multiple files."""
        # Create temp files
        file1 = tmp_path / "Main.java"
        file1.write_text("public class Main {}")
        
        file2 = tmp_path / "Helper.java"
        file2.write_text("public class Helper {}")
        
        files = {
            "Main.java": str(file1),
            "Helper.java": str(file2),
        }
        
        text = build_submission_text_with_token_limit(files)
        
        assert "FILE: Main.java" in text
        assert "FILE: Helper.java" in text
        assert "public class Main" in text
        assert "public class Helper" in text
    
    def test_build_submission_text_with_truncation_notice(self, tmp_path):
        """Test that truncation notice is added."""
        file1 = tmp_path / "included.java"
        file1.write_text("public class Included {}")
        
        files = {"included.java": str(file1)}
        omitted = ["omitted1.java", "omitted2.java"]
        
        text = build_submission_text_with_token_limit(
            files,
            is_truncated=True,
            omitted_files=omitted
        )
        
        assert "Some files were omitted" in text
        assert "omitted1.java" in text
        assert "omitted2.java" in text
    
    def test_build_submission_text_respects_token_limit(self, tmp_path):
        """Test that token limit warning does not truncate content."""
        # Create files with known size
        large_content = "x" * 1000  # ~250 tokens
        
        file1 = tmp_path / "file1.txt"
        file1.write_text(large_content)
        
        file2 = tmp_path / "file2.txt"
        file2.write_text(large_content)
        
        files = {
            "file1.txt": str(file1),
            "file2.txt": str(file2),
        }
        
        # Set limit that would previously exclude second file
        text = build_submission_text_with_token_limit(files, max_tokens=300)
        
        # Should include both files (no truncation)
        assert "FILE: file1.txt" in text
        assert "FILE: file2.txt" in text
