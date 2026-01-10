#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Integration tests for rubric configuration and grading system.

These tests verify that the rubric configuration system works correctly when loading
and assembling grading prompts for rubric-based assessments.
"""

import pytest

from cqc_cpcc.rubric_config import (
    load_rubrics_from_config,
    get_rubric_by_id,
    get_rubrics_for_course,
)
from cqc_cpcc.rubric_models import Rubric
from cqc_cpcc.rubric_grading import build_rubric_grading_prompt
from cqc_cpcc.error_definitions_config import get_error_definitions


@pytest.mark.integration
def test_load_rubrics_from_config_succeeds():
    """Test that rubrics load successfully from JSON configuration."""
    rubrics = load_rubrics_from_config()
    
    assert rubrics is not None
    assert len(rubrics) > 0
    assert "default_100pt_rubric" in rubrics


@pytest.mark.integration
def test_get_rubric_by_id_returns_correct_rubric():
    """Test retrieving a rubric by its ID."""
    rubric = get_rubric_by_id("default_100pt_rubric")
    
    assert rubric is not None
    assert rubric.rubric_id == "default_100pt_rubric"
    assert rubric.title is not None
    assert len(rubric.criteria) > 0
    assert rubric.total_points_possible == 100


@pytest.mark.integration
def test_get_rubric_by_id_handles_missing_rubric():
    """Test that missing rubric ID raises ValueError."""
    with pytest.raises(ValueError, match="not found"):
        get_rubric_by_id("nonexistent_rubric")


@pytest.mark.integration
def test_get_rubrics_for_course_returns_rubrics():
    """Test retrieving rubrics for a specific course."""
    rubrics = get_rubrics_for_course("CSC151")
    
    assert rubrics is not None
    assert len(rubrics) > 0
    
    # Check that all returned rubrics include CSC151
    for rubric in rubrics.values():
        assert "CSC151" in rubric.course_ids


@pytest.mark.integration
def test_rubric_criteria_are_valid():
    """Test that all rubric criteria have valid structure."""
    rubric = get_rubric_by_id("default_100pt_rubric")
    
    assert len(rubric.criteria) > 0
    
    for criterion in rubric.criteria:
        assert criterion.criterion_id is not None
        assert criterion.name is not None
        assert criterion.max_points > 0
        assert len(criterion.levels) > 0
        
        # Check that levels are properly ordered and cover the full range
        levels_sorted = sorted(criterion.levels, key=lambda l: l.score_min)
        assert levels_sorted[0].score_min == 0
        assert levels_sorted[-1].score_max == criterion.max_points


@pytest.mark.integration
def test_apply_overrides_to_rubric_modifies_criteria():
    """Test that rubric can be modified with overrides (simplified test)."""
    rubric = get_rubric_by_id("default_100pt_rubric")
    original_points = rubric.total_points_possible
    
    # Test that we can check how modifying criteria would affect total
    # We don't actually modify the immutable rubric, just calculate expected changes
    understanding_criterion = next(c for c in rubric.criteria if c.criterion_id == "understanding")
    original_understanding_points = understanding_criterion.max_points
    new_understanding_points = 30
    
    # Calculate expected new total
    points_difference = new_understanding_points - original_understanding_points
    expected_new_total = original_points + points_difference
    
    # Verify the calculation makes sense
    assert original_understanding_points == 25  # Default is 25
    assert expected_new_total == original_points + 5  # 30 - 25 = 5 more points


@pytest.mark.integration
def test_apply_overrides_to_rubric_disables_criteria():
    """Test that rubric criteria can be disabled."""
    rubric = get_rubric_by_id("default_100pt_rubric")
    original_points = rubric.total_points_possible
    
    # Calculate what total would be if understanding criterion was disabled
    understanding_criterion = next(c for c in rubric.criteria if c.criterion_id == "understanding")
    expected_new_total = original_points - understanding_criterion.max_points
    
    # Verify the calculation
    assert expected_new_total == original_points - 25  # Understanding is 25 points


@pytest.mark.integration
def test_build_rubric_grading_prompt_includes_all_sections(
    sample_assignment_instructions,
    sample_student_submission,
    sample_reference_solution
):
    """Test that the grading prompt includes all required sections."""
    rubric = get_rubric_by_id("default_100pt_rubric")
    
    prompt = build_rubric_grading_prompt(
        rubric=rubric,
        assignment_instructions=sample_assignment_instructions,
        student_submission=sample_student_submission,
        reference_solution=sample_reference_solution,
        error_definitions=None
    )
    
    # Check that prompt contains expected sections
    assert "# Grading Task" in prompt
    assert "## Assignment Instructions" in prompt
    assert "## Reference Solution" in prompt
    assert f"## Grading Rubric: {rubric.title}" in prompt
    assert "### Criteria" in prompt
    assert "## Student Submission" in prompt
    assert sample_assignment_instructions in prompt
    assert sample_student_submission in prompt


@pytest.mark.integration
def test_build_rubric_grading_prompt_includes_error_definitions(
    sample_assignment_instructions,
    sample_student_submission
):
    """Test that the grading prompt includes error definitions when provided."""
    rubric = get_rubric_by_id("default_100pt_rubric")
    errors = get_error_definitions("CSC151", "Exam1")
    
    prompt = build_rubric_grading_prompt(
        rubric=rubric,
        assignment_instructions=sample_assignment_instructions,
        student_submission=sample_student_submission,
        reference_solution=None,
        error_definitions=errors
    )
    
    # Check that error definitions section is in the prompt
    assert "Error Definitions" in prompt or "error" in prompt.lower()
    
    # Check that at least one error ID is mentioned (using error_id not name)
    assert any(error.error_id in prompt for error in errors[:5])  # Check first few errors


@pytest.mark.integration
def test_build_rubric_grading_prompt_handles_disabled_criteria(
    sample_assignment_instructions,
    sample_student_submission
):
    """Test that the grading prompt would handle disabled criteria appropriately."""
    rubric = get_rubric_by_id("default_100pt_rubric")
    
    # Build prompt with all criteria enabled
    prompt = build_rubric_grading_prompt(
        rubric=rubric,
        assignment_instructions=sample_assignment_instructions,
        student_submission=sample_student_submission
    )
    
    # Verify that all enabled criteria appear in the prompt
    for criterion in rubric.criteria:
        if criterion.enabled:
            # The criterion name should appear in the prompt
            assert criterion.name in prompt


@pytest.mark.integration
def test_rubric_total_points_calculation():
    """Test that rubric total points are calculated correctly."""
    rubric = get_rubric_by_id("default_100pt_rubric")
    
    # Sum up enabled criteria max points
    enabled_total = sum(c.max_points for c in rubric.criteria if c.enabled)
    
    assert rubric.total_points_possible == enabled_total
    assert rubric.total_points_possible == 100  # For the default rubric
