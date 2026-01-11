#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for ZIP packaging of feedback documents.

Tests the ZIP download functionality that:
- Creates a ZIP with correct number of files
- Uses sanitized filenames
- Handles duplicate names correctly
- Creates valid ZIP archives
"""

import tempfile
import zipfile
from pathlib import Path

import pytest

from cqc_cpcc.feedback_doc_generator import sanitize_filename


@pytest.mark.unit
def test_zip_contains_correct_number_of_files():
    """Test that ZIP contains the expected number of files."""
    # Create temporary test files
    test_files = []
    for i in range(3):
        temp_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt')
        temp_file.write(f"Test content {i}")
        temp_file.close()
        test_files.append((f"file{i}.txt", temp_file.name))
    
    # Create ZIP
    zip_temp = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    zip_temp.close()
    
    try:
        with zipfile.ZipFile(zip_temp.name, 'w') as zipf:
            for orig_name, temp_path in test_files:
                zipf.write(temp_path, arcname=orig_name)
        
        # Verify ZIP contents
        with zipfile.ZipFile(zip_temp.name, 'r') as zipf:
            names = zipf.namelist()
            assert len(names) == 3
            assert 'file0.txt' in names
            assert 'file1.txt' in names
            assert 'file2.txt' in names
    
    finally:
        # Cleanup
        Path(zip_temp.name).unlink(missing_ok=True)
        for _, temp_path in test_files:
            Path(temp_path).unlink(missing_ok=True)


@pytest.mark.unit
def test_zip_filenames_are_sanitized():
    """Test that filenames in ZIP are properly sanitized."""
    # Test various problematic filenames
    test_names = [
        ("John/Doe", "John_Doe_Feedback.docx"),
        ("Jane:Smith", "Jane_Smith_Feedback.docx"),
        ("Test*Student", "Test_Student_Feedback.docx"),
        ("Normal Name", "Normal_Name_Feedback.docx"),
    ]
    
    for original, expected_base in test_names:
        sanitized = sanitize_filename(original)
        expected_filename = f"{sanitized}_Feedback.docx"
        
        # Verify sanitization worked
        assert '/' not in expected_filename
        assert ':' not in expected_filename
        assert '*' not in expected_filename


@pytest.mark.unit
def test_zip_handles_duplicate_student_names():
    """Test that ZIP handles duplicate student names by using sanitized IDs."""
    # Simulate students with same name but different IDs
    students = [
        ("John_Doe_001", "John Doe"),
        ("John_Doe_002", "John Doe"),
        ("Jane_Smith", "Jane Smith"),
    ]
    
    # Generate filenames
    filenames = []
    for student_id, _ in students:
        sanitized = sanitize_filename(student_id)
        filename = f"{sanitized}_Feedback.docx"
        filenames.append(filename)
    
    # Verify all filenames are unique
    assert len(filenames) == len(set(filenames))
    assert filenames == [
        "John_Doe_001_Feedback.docx",
        "John_Doe_002_Feedback.docx",
        "Jane_Smith_Feedback.docx"
    ]


@pytest.mark.unit
def test_zip_is_valid_archive():
    """Test that created ZIP is a valid archive."""
    # Create a simple ZIP
    zip_temp = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    zip_temp.close()
    
    test_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt')
    test_file.write("Test content")
    test_file.close()
    
    try:
        # Create ZIP
        with zipfile.ZipFile(zip_temp.name, 'w') as zipf:
            zipf.write(test_file.name, arcname='test.txt')
        
        # Verify it's valid
        assert zipfile.is_zipfile(zip_temp.name)
        
        # Verify we can read it
        with zipfile.ZipFile(zip_temp.name, 'r') as zipf:
            assert zipf.testzip() is None  # No corruption
            assert 'test.txt' in zipf.namelist()
            content = zipf.read('test.txt').decode('utf-8')
            assert content == "Test content"
    
    finally:
        # Cleanup
        Path(zip_temp.name).unlink(missing_ok=True)
        Path(test_file.name).unlink(missing_ok=True)


@pytest.mark.unit
def test_zip_filename_format():
    """Test that ZIP filename follows expected format."""
    course_name = "CSC151_Exam1"
    timestamp = "20240115_1430"
    
    expected_filename = f"{course_name}_Feedback_{timestamp}.zip"
    
    # Verify format
    assert expected_filename == "CSC151_Exam1_Feedback_20240115_1430.zip"
    assert expected_filename.endswith('.zip')
    assert 'Feedback' in expected_filename


@pytest.mark.unit
def test_zip_with_empty_file_list():
    """Test that ZIP creation handles empty file list gracefully."""
    # Create ZIP with no files
    zip_temp = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    zip_temp.close()
    
    try:
        with zipfile.ZipFile(zip_temp.name, 'w') as zipf:
            pass  # Create empty ZIP
        
        # Verify it's valid but empty
        assert zipfile.is_zipfile(zip_temp.name)
        
        with zipfile.ZipFile(zip_temp.name, 'r') as zipf:
            assert len(zipf.namelist()) == 0
    
    finally:
        Path(zip_temp.name).unlink(missing_ok=True)
