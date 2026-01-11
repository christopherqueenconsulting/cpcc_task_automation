#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for grading run key generation."""

import pytest
from cqc_cpcc.grading_run_key import generate_grading_run_key, generate_file_metadata


@pytest.mark.unit
def test_generate_grading_run_key_deterministic():
    """Test that same inputs produce the same run key."""
    key1 = generate_grading_run_key(
        course_id="CSC151",
        assignment_id="Exam1",
        rubric_id="default_rubric",
        rubric_version=1,
        error_definition_ids=["MISSING_HEADER", "LOGIC_ERROR"],
        file_metadata=[("student1.java", 1234)],
        model_name="gpt-5-mini",
        temperature=0.2,
        debug_mode=False,
    )
    
    key2 = generate_grading_run_key(
        course_id="CSC151",
        assignment_id="Exam1",
        rubric_id="default_rubric",
        rubric_version=1,
        error_definition_ids=["MISSING_HEADER", "LOGIC_ERROR"],
        file_metadata=[("student1.java", 1234)],
        model_name="gpt-5-mini",
        temperature=0.2,
        debug_mode=False,
    )
    
    assert key1 == key2
    assert len(key1) == 64  # SHA256 produces 64 hex chars


@pytest.mark.unit
def test_generate_grading_run_key_different_course():
    """Test that different course IDs produce different keys."""
    key1 = generate_grading_run_key(
        course_id="CSC151",
        assignment_id="Exam1",
        rubric_id="default_rubric",
        rubric_version=1,
    )
    
    key2 = generate_grading_run_key(
        course_id="CSC251",  # Different course
        assignment_id="Exam1",
        rubric_id="default_rubric",
        rubric_version=1,
    )
    
    assert key1 != key2


@pytest.mark.unit
def test_generate_grading_run_key_different_rubric_version():
    """Test that different rubric versions produce different keys."""
    key1 = generate_grading_run_key(
        course_id="CSC151",
        assignment_id="Exam1",
        rubric_id="default_rubric",
        rubric_version=1,
    )
    
    key2 = generate_grading_run_key(
        course_id="CSC151",
        assignment_id="Exam1",
        rubric_id="default_rubric",
        rubric_version=2,  # Different version
    )
    
    assert key1 != key2


@pytest.mark.unit
def test_generate_grading_run_key_different_files():
    """Test that different file metadata produces different keys."""
    key1 = generate_grading_run_key(
        course_id="CSC151",
        assignment_id="Exam1",
        rubric_id="default_rubric",
        rubric_version=1,
        file_metadata=[("student1.java", 1234)],
    )
    
    key2 = generate_grading_run_key(
        course_id="CSC151",
        assignment_id="Exam1",
        rubric_id="default_rubric",
        rubric_version=1,
        file_metadata=[("student2.java", 5678)],  # Different file
    )
    
    assert key1 != key2


@pytest.mark.unit
def test_generate_grading_run_key_error_definitions_order_independent():
    """Test that error definition order doesn't affect the key."""
    key1 = generate_grading_run_key(
        course_id="CSC151",
        assignment_id="Exam1",
        rubric_id="default_rubric",
        rubric_version=1,
        error_definition_ids=["ERROR_A", "ERROR_B", "ERROR_C"],
    )
    
    key2 = generate_grading_run_key(
        course_id="CSC151",
        assignment_id="Exam1",
        rubric_id="default_rubric",
        rubric_version=1,
        error_definition_ids=["ERROR_C", "ERROR_A", "ERROR_B"],  # Different order
    )
    
    assert key1 == key2  # Should be same after sorting


@pytest.mark.unit
def test_generate_grading_run_key_different_model():
    """Test that different models produce different keys."""
    key1 = generate_grading_run_key(
        course_id="CSC151",
        assignment_id="Exam1",
        rubric_id="default_rubric",
        rubric_version=1,
        model_name="gpt-5-mini",
    )
    
    key2 = generate_grading_run_key(
        course_id="CSC151",
        assignment_id="Exam1",
        rubric_id="default_rubric",
        rubric_version=1,
        model_name="gpt-5",  # Different model
    )
    
    assert key1 != key2


@pytest.mark.unit
def test_generate_grading_run_key_different_temperature():
    """Test that different temperatures produce different keys."""
    key1 = generate_grading_run_key(
        course_id="CSC151",
        assignment_id="Exam1",
        rubric_id="default_rubric",
        rubric_version=1,
        temperature=0.2,
    )
    
    key2 = generate_grading_run_key(
        course_id="CSC151",
        assignment_id="Exam1",
        rubric_id="default_rubric",
        rubric_version=1,
        temperature=0.5,  # Different temperature
    )
    
    assert key1 != key2


@pytest.mark.unit
def test_generate_grading_run_key_debug_mode():
    """Test that debug mode flag affects the key."""
    key1 = generate_grading_run_key(
        course_id="CSC151",
        assignment_id="Exam1",
        rubric_id="default_rubric",
        rubric_version=1,
        debug_mode=False,
    )
    
    key2 = generate_grading_run_key(
        course_id="CSC151",
        assignment_id="Exam1",
        rubric_id="default_rubric",
        rubric_version=1,
        debug_mode=True,  # Different debug mode
    )
    
    assert key1 != key2


@pytest.mark.unit
def test_generate_file_metadata(tmp_path):
    """Test file metadata extraction from file paths."""
    # Create test files
    file1 = tmp_path / "student1.java"
    file1.write_text("public class Test {}")
    
    file2 = tmp_path / "student2.java"
    file2.write_text("public class Test2 { /* more content */ }")
    
    file_paths = [
        ("student1.java", str(file1)),
        ("student2.java", str(file2)),
    ]
    
    metadata = generate_file_metadata(file_paths)
    
    assert len(metadata) == 2
    assert metadata[0][0] == "student1.java"
    assert metadata[1][0] == "student2.java"
    assert metadata[0][1] > 0  # File size should be positive
    assert metadata[1][1] > metadata[0][1]  # Second file is larger


@pytest.mark.unit
def test_generate_file_metadata_missing_file():
    """Test that missing temp files are handled gracefully."""
    file_paths = [
        ("student1.java", "/nonexistent/path/to/file.java"),
    ]
    
    metadata = generate_file_metadata(file_paths)
    
    assert len(metadata) == 1
    assert metadata[0][0] == "student1.java"
    assert metadata[0][1] == 0  # Size should be 0 for missing file
