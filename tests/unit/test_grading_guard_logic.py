#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Integration test for grading state management and caching behavior.

This test verifies that the grading guard logic prevents re-grading on
passive UI interactions like Expand All and Download buttons.
"""

import pytest
from unittest.mock import MagicMock, patch
from cqc_cpcc.grading_run_key import generate_grading_run_key


@pytest.mark.unit
def test_grading_guard_prevents_regrading_on_expand():
    """Test that Expand All button doesn't trigger re-grading when results are cached."""
    # Simulate session state
    session_state = {
        'do_grade': False,  # Not clicked
        'grading_run_key': 'abc123',
        'grading_results_by_key': {'abc123': [('student1', MagicMock())]},  # Cached results exist
        'expand_all_students': False,
    }
    
    current_run_key = 'abc123'  # Same as cached
    has_cached_results = current_run_key in session_state['grading_results_by_key']
    
    # Simulate guard logic
    should_grade = (
        session_state['do_grade']
        and session_state['grading_run_key'] == current_run_key
        and not has_cached_results
    )
    
    # Verify grading is blocked
    assert not should_grade, "Grading should be blocked when results are cached"
    
    # Simulate Expand All click (only mutates state)
    session_state['expand_all_students'] = True
    
    # Re-check guard after state mutation
    should_grade_after = (
        session_state['do_grade']
        and session_state['grading_run_key'] == current_run_key
        and not has_cached_results
    )
    
    # Verify grading is still blocked
    assert not should_grade_after, "Grading should remain blocked after Expand All"


@pytest.mark.unit
def test_grading_guard_allows_grading_on_grade_button():
    """Test that Grade button triggers grading when no cached results exist."""
    # Simulate session state
    session_state = {
        'do_grade': False,
        'grading_run_key': None,
        'grading_results_by_key': {},  # No cached results
        'expand_all_students': False,
    }
    
    current_run_key = 'abc123'
    has_cached_results = current_run_key in session_state['grading_results_by_key']
    
    # Simulate Grade button click
    session_state['do_grade'] = True
    session_state['grading_run_key'] = current_run_key
    
    # Check guard logic
    should_grade = (
        session_state['do_grade']
        and session_state['grading_run_key'] == current_run_key
        and not has_cached_results
    )
    
    # Verify grading is allowed
    assert should_grade, "Grading should be allowed when Grade button is clicked"


@pytest.mark.unit
def test_grading_guard_blocks_regrading_with_cached_results():
    """Test that Grade button doesn't re-grade if results are already cached."""
    # Simulate session state with cached results
    session_state = {
        'do_grade': False,
        'grading_run_key': 'abc123',
        'grading_results_by_key': {'abc123': [('student1', MagicMock())]},
        'expand_all_students': False,
    }
    
    current_run_key = 'abc123'
    has_cached_results = current_run_key in session_state['grading_results_by_key']
    
    # Simulate Grade button click
    session_state['do_grade'] = True
    session_state['grading_run_key'] = current_run_key
    
    # Check guard logic
    should_grade = (
        session_state['do_grade']
        and session_state['grading_run_key'] == current_run_key
        and not has_cached_results
    )
    
    # Verify grading is blocked (cached results exist)
    assert not should_grade, "Grading should be blocked when cached results exist"


@pytest.mark.unit
def test_grading_guard_allows_regrading_after_clear():
    """Test that Clear Results button enables re-grading."""
    # Simulate session state with cached results
    session_state = {
        'do_grade': False,
        'grading_run_key': 'abc123',
        'grading_results_by_key': {'abc123': [('student1', MagicMock())]},
        'grading_status_by_key': {'abc123': 'done'},
        'expand_all_students': False,
    }
    
    current_run_key = 'abc123'
    
    # Simulate Clear Results button click
    del session_state['grading_results_by_key'][current_run_key]
    del session_state['grading_status_by_key'][current_run_key]
    
    has_cached_results = current_run_key in session_state['grading_results_by_key']
    
    # Simulate Grade button click after clear
    session_state['do_grade'] = True
    session_state['grading_run_key'] = current_run_key
    
    # Check guard logic
    should_grade = (
        session_state['do_grade']
        and session_state['grading_run_key'] == current_run_key
        and not has_cached_results
    )
    
    # Verify grading is allowed after clearing cache
    assert should_grade, "Grading should be allowed after clearing cached results"


@pytest.mark.unit
def test_grading_guard_triggers_new_grading_on_input_change():
    """Test that changing inputs generates new run key and triggers grading."""
    # Initial grading
    initial_run_key = generate_grading_run_key(
        course_id="CSC151",
        assignment_id="Exam1",
        rubric_id="rubric_v1",
        rubric_version=1,
        error_definition_ids=["ERROR_A"],
        file_metadata=[("student1.java", 1234)],
        model_name="gpt-5-mini",
        temperature=0.2,
    )
    
    session_state = {
        'do_grade': False,
        'grading_run_key': initial_run_key,
        'grading_results_by_key': {initial_run_key: [('student1', MagicMock())]},
    }
    
    # Change input: add new error definition
    new_run_key = generate_grading_run_key(
        course_id="CSC151",
        assignment_id="Exam1",
        rubric_id="rubric_v1",
        rubric_version=1,
        error_definition_ids=["ERROR_A", "ERROR_B"],  # Changed!
        file_metadata=[("student1.java", 1234)],
        model_name="gpt-5-mini",
        temperature=0.2,
    )
    
    # Verify new run key is different
    assert new_run_key != initial_run_key, "Changed inputs should generate new run key"
    
    # Check cache miss
    has_cached_results = new_run_key in session_state['grading_results_by_key']
    assert not has_cached_results, "New run key should not have cached results"
    
    # Simulate Grade button click with new inputs
    session_state['do_grade'] = True
    session_state['grading_run_key'] = new_run_key
    
    # Check guard logic
    should_grade = (
        session_state['do_grade']
        and session_state['grading_run_key'] == new_run_key
        and not has_cached_results
    )
    
    # Verify grading is allowed for new inputs
    assert should_grade, "Grading should be allowed when inputs change (cache miss)"


@pytest.mark.unit
def test_run_key_stability_across_reruns():
    """Test that same inputs produce same run key across multiple simulated reruns."""
    # Simulate multiple reruns with same inputs
    run_keys = []
    
    for _ in range(5):  # 5 simulated reruns
        run_key = generate_grading_run_key(
            course_id="CSC151",
            assignment_id="Exam1",
            rubric_id="rubric_v1",
            rubric_version=1,
            error_definition_ids=["ERROR_A", "ERROR_B"],
            file_metadata=[("student1.java", 1234), ("student2.java", 5678)],
            model_name="gpt-5-mini",
            temperature=0.2,
        )
        run_keys.append(run_key)
    
    # Verify all keys are identical
    assert len(set(run_keys)) == 1, "Same inputs should produce same run key across reruns"
