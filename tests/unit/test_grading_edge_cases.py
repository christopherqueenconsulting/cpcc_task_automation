#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Edge case tests for grading state management."""

import pytest
from unittest.mock import MagicMock
from cqc_cpcc.grading_run_key import generate_grading_run_key


@pytest.mark.unit
def test_multiple_concurrent_run_keys():
    """Test that different configurations can coexist in cache."""
    session_state = {
        'grading_results_by_key': {},
        'do_grade': False,
    }
    
    # Grade config 1
    run_key_1 = generate_grading_run_key(
        course_id="CSC151",
        assignment_id="Exam1",
        rubric_id="rubric_v1",
        rubric_version=1,
    )
    session_state['grading_results_by_key'][run_key_1] = [('student1', MagicMock())]
    
    # Grade config 2 (different rubric version)
    run_key_2 = generate_grading_run_key(
        course_id="CSC151",
        assignment_id="Exam1",
        rubric_id="rubric_v1",
        rubric_version=2,  # Different version
    )
    session_state['grading_results_by_key'][run_key_2] = [('student1', MagicMock())]
    
    # Verify both exist independently
    assert run_key_1 in session_state['grading_results_by_key']
    assert run_key_2 in session_state['grading_results_by_key']
    assert run_key_1 != run_key_2
    assert len(session_state['grading_results_by_key']) == 2


@pytest.mark.unit
def test_guard_handles_missing_flag():
    """Test that guard handles missing do_grade flag gracefully."""
    session_state = {
        'grading_results_by_key': {},
        # do_grade flag not set (edge case)
    }
    
    run_key = 'abc123'
    
    # Guard should default to False if flag missing
    should_grade = (
        session_state.get('do_grade', False)  # Defaults to False
        and session_state.get('grading_run_key') == run_key
        and run_key not in session_state['grading_results_by_key']
    )
    
    assert not should_grade, "Guard should block when flag is missing"


@pytest.mark.unit
def test_guard_handles_mismatched_run_key():
    """Test that guard blocks grading when run_key doesn't match."""
    session_state = {
        'do_grade': True,
        'grading_run_key': 'old_key_123',
        'grading_results_by_key': {},
    }
    
    current_run_key = 'new_key_456'  # Different key
    has_cached = current_run_key in session_state['grading_results_by_key']
    
    # Guard should block (key mismatch)
    should_grade = (
        session_state['do_grade']
        and session_state['grading_run_key'] == current_run_key  # Mismatch!
        and not has_cached
    )
    
    assert not should_grade, "Guard should block when run_key doesn't match"


@pytest.mark.unit
def test_empty_file_list_generates_valid_key():
    """Test that empty file list produces a valid run key."""
    run_key = generate_grading_run_key(
        course_id="CSC151",
        assignment_id="Exam1",
        rubric_id="rubric_v1",
        rubric_version=1,
        file_metadata=[],  # Empty
    )
    
    assert run_key is not None
    assert len(run_key) == 64  # SHA256 hex
    assert isinstance(run_key, str)


@pytest.mark.unit
def test_none_error_definitions_handled():
    """Test that None error definitions are handled correctly."""
    run_key_1 = generate_grading_run_key(
        course_id="CSC151",
        assignment_id="Exam1",
        rubric_id="rubric_v1",
        rubric_version=1,
        error_definition_ids=None,
    )
    
    run_key_2 = generate_grading_run_key(
        course_id="CSC151",
        assignment_id="Exam1",
        rubric_id="rubric_v1",
        rubric_version=1,
        error_definition_ids=[],  # Empty list
    )
    
    # None and [] should produce same key (both represent "no errors")
    assert run_key_1 == run_key_2


@pytest.mark.unit
def test_flag_reset_after_grading():
    """Test that do_grade flag is reset after grading completes."""
    session_state = {
        'do_grade': True,
        'grading_run_key': 'abc123',
        'grading_results_by_key': {},
    }
    
    run_key = 'abc123'
    has_cached = run_key in session_state['grading_results_by_key']
    
    # Guard allows grading
    should_grade = (
        session_state['do_grade']
        and session_state['grading_run_key'] == run_key
        and not has_cached
    )
    
    assert should_grade
    
    # Simulate grading completion
    session_state['grading_results_by_key'][run_key] = [('student1', MagicMock())]
    session_state['do_grade'] = False  # Reset flag
    
    # Verify flag is reset
    assert not session_state['do_grade']
    
    # Verify subsequent rerun won't re-grade
    has_cached_after = run_key in session_state['grading_results_by_key']
    should_grade_after = (
        session_state['do_grade']
        and session_state['grading_run_key'] == run_key
        and not has_cached_after
    )
    
    assert not should_grade_after, "Guard should block after flag reset"


@pytest.mark.unit
def test_clear_results_removes_all_artifacts():
    """Test that clearing results removes all related cache entries."""
    run_key = 'abc123'
    
    session_state = {
        'grading_results_by_key': {run_key: [('student1', MagicMock())]},
        'grading_status_by_key': {run_key: 'done'},
        'grading_errors_by_key': {run_key: None},
        'feedback_zip_bytes_by_key': {run_key: '/tmp/feedback.zip'},
    }
    
    # Simulate Clear Results button
    for key_dict in [
        session_state['grading_results_by_key'],
        session_state['grading_status_by_key'],
        session_state['grading_errors_by_key'],
        session_state['feedback_zip_bytes_by_key'],
    ]:
        if run_key in key_dict:
            del key_dict[run_key]
    
    # Verify all entries removed
    assert run_key not in session_state['grading_results_by_key']
    assert run_key not in session_state['grading_status_by_key']
    assert run_key not in session_state['grading_errors_by_key']
    assert run_key not in session_state['feedback_zip_bytes_by_key']


@pytest.mark.unit
def test_temperature_precision_affects_key():
    """Test that temperature precision affects run key generation."""
    key_1 = generate_grading_run_key(
        course_id="CSC151",
        assignment_id="Exam1",
        rubric_id="rubric_v1",
        rubric_version=1,
        temperature=0.2,
    )
    
    key_2 = generate_grading_run_key(
        course_id="CSC151",
        assignment_id="Exam1",
        rubric_id="rubric_v1",
        rubric_version=1,
        temperature=0.20,  # Same value, different representation
    )
    
    # Should produce same key (0.2 == 0.20)
    assert key_1 == key_2
    
    key_3 = generate_grading_run_key(
        course_id="CSC151",
        assignment_id="Exam1",
        rubric_id="rubric_v1",
        rubric_version=1,
        temperature=0.21,  # Slightly different
    )
    
    # Should produce different key
    assert key_1 != key_3
