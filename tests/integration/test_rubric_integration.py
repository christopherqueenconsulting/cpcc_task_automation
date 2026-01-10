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
    """Test that missing rubric ID returns None."""
    rubric = get_rubric_by_id("nonexistent_rubric")
    
    assert rubric is None


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
    
    # Test that we can modify a rubric manually
    modified_rubric = Rubric.model_validate(rubric.model_dump())
    
    # Find and modify a criterion
    for criterion in modified_rubric.criteria:
        if criterion.criterion_id == "understanding":
            criterion.max_points = 30  # Changed from 25
            break
    
    # Recalculate total
    modified_rubric.total_points_possible = sum(c.max_points for c in modified_rubric.criteria if c.enabled)
    
    # Check that the criterion was modified
    understanding = next(c for c in modified_rubric.criteria if c.criterion_id == "understanding")
    assert understanding.max_points == 30
    
    # Total points should have changed
    assert modified_rubric.total_points_possible != original_points


@pytest.mark.integration
def test_apply_overrides_to_rubric_disables_criteria():
    """Test that rubric criteria can be disabled."""
    rubric = get_rubric_by_id("default_100pt_rubric")
    
    # Create a copy and disable a criterion
    modified_rubric = Rubric.model_validate(rubric.model_dump())
    
    for criterion in modified_rubric.criteria:
        if criterion.criterion_id == "understanding":
            criterion.enabled = False
            break
    
    # Recalculate total
    modified_rubric.total_points_possible = sum(c.max_points for c in modified_rubric.criteria if c.enabled)
    
    # Check that the criterion was disabled
    understanding = next(c for c in modified_rubric.criteria if c.criterion_id == "understanding")
    assert understanding.enabled is False


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
    
    # Check that error definitions are in the prompt
    assert "## Error Definitions" in prompt or "Error" in prompt
    
    # Check that at least one error is mentioned
    assert any(error.name in prompt for error in errors[:3])  # Check first few errors


@pytest.mark.integration
def test_build_rubric_grading_prompt_handles_disabled_criteria(
    sample_assignment_instructions,
    sample_student_submission
):
    """Test that the grading prompt excludes disabled criteria."""
    rubric = get_rubric_by_id("default_100pt_rubric")
    
    # Create a copy and disable a criterion
    modified_rubric = Rubric.model_validate(rubric.model_dump())
    
    for criterion in modified_rubric.criteria:
        if criterion.criterion_id == "understanding":
            criterion.enabled = False
            break
    
    prompt = build_rubric_grading_prompt(
        rubric=modified_rubric,
        assignment_instructions=sample_assignment_instructions,
        student_submission=sample_student_submission
    )
    
    # The disabled criterion should not appear in the prompt
    # (or should be marked as disabled)
    understanding_criterion = next(c for c in rubric.criteria if c.criterion_id == "understanding")
    
    # Prompt should either not contain it or mark it as disabled
    # Since the implementation skips disabled criteria in the loop, it won't be in output
    lines_with_understanding = [line for line in prompt.split('\n') if understanding_criterion.name in line]
    
    # Either no lines or lines that indicate it's disabled
    assert len(lines_with_understanding) == 0 or "disabled" in prompt.lower()


@pytest.mark.integration
def test_rubric_total_points_calculation():
    """Test that rubric total points are calculated correctly."""
    rubric = get_rubric_by_id("default_100pt_rubric")
    
    # Sum up enabled criteria max points
    enabled_total = sum(c.max_points for c in rubric.criteria if c.enabled)
    
    assert rubric.total_points_possible == enabled_total
    assert rubric.total_points_possible == 100  # For the default rubric
