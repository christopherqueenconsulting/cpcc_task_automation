"""Tests for handling criterion ID mismatches and None values in rubric grading.

These tests verify the fixes for issues found in production where:
1. AI returned criterion IDs that didn't match rubric criterion IDs
2. None values in points_earned caused TypeError during aggregation
"""

import pytest
from unittest.mock import AsyncMock, patch
from pydantic import BaseModel

from cqc_cpcc.rubric_models import (
    Rubric,
    Criterion,
    PerformanceLevel,
    RubricAssessmentResult,
    CriterionResult,
)
from cqc_cpcc.rubric_grading import apply_backend_scoring


@pytest.mark.unit
class TestCriterionIDMismatchHandling:
    """Test handling of criterion ID mismatches between AI response and rubric."""
    
    def test_criterion_not_in_rubric_sets_points_to_zero(self):
        """Test that criteria not found in rubric get points_earned=0."""
        # Create a rubric with specific criterion IDs
        rubric = Rubric(
            rubric_id="test_rubric",
            rubric_version="1.0",
            title="Test Rubric",
            criteria=[
                Criterion(
                    criterion_id="documentation",
                    name="Documentation",
                    max_points=25,
                    scoring_mode="level_band",
                    levels=[
                        PerformanceLevel(
                            label="Exemplary",
                            score_min=23,
                            score_max=25,
                            description="Excellent"
                        ),
                    ]
                ),
            ]
        )
        
        # Create a result with a criterion ID that doesn't match
        result = RubricAssessmentResult(
            rubric_id="test_rubric",
            rubric_version="1.0",
            total_points_possible=25,
            total_points_earned=0,
            criteria_results=[
                CriterionResult(
                    criterion_id="DOC",  # Wrong ID! Should be "documentation"
                    criterion_name="Documentation",
                    points_possible=25,
                    points_earned=None,  # AI didn't set it
                    selected_level_label="Exemplary",
                    feedback="Good documentation"
                ),
            ],
            overall_feedback="Overall good work"
        )
        
        # Apply backend scoring
        processed_result = apply_backend_scoring(rubric, result)
        
        # Verify that the mismatched criterion gets points_earned=0
        assert len(processed_result.criteria_results) == 1
        assert processed_result.criteria_results[0].criterion_id == "DOC"
        assert processed_result.criteria_results[0].points_earned == 0
        
        # Total should also be 0 since the criterion didn't match
        assert processed_result.total_points_earned == 0


@pytest.mark.unit
class TestNoneValuesHandling:
    """Test handling of None values in points_earned during aggregation."""
    
    def test_aggregate_handles_none_values_safely(self):
        """Test that sum() handles None values without TypeError."""
        from cqc_cpcc.scoring import aggregate_rubric_result
        
        # Create a rubric
        rubric = Rubric(
            rubric_id="test_rubric",
            rubric_version="1.0",
            title="Test Rubric",
            criteria=[
                Criterion(
                    criterion_id="criterion1",
                    name="Criterion 1",
                    max_points=25,
                    scoring_mode="manual"
                ),
                Criterion(
                    criterion_id="criterion2",
                    name="Criterion 2",
                    max_points=25,
                    scoring_mode="manual"
                ),
            ]
        )
        
        # Create criteria results with one None value (edge case)
        criteria_results = [
            CriterionResult(
                criterion_id="criterion1",
                criterion_name="Criterion 1",
                points_possible=25,
                points_earned=20,  # Has value
                feedback="Good"
            ),
            CriterionResult(
                criterion_id="criterion2",
                criterion_name="Criterion 2",
                points_possible=25,
                points_earned=None,  # None value!
                feedback="Needs work"
            ),
        ]
        
        # This should NOT raise TypeError
        result = aggregate_rubric_result(rubric, criteria_results)
        
        # None should be treated as 0
        assert result["total_points_earned"] == 20  # 20 + 0
        assert result["total_points_possible"] == 50


@pytest.mark.unit
class TestPromptIncludesCriterionIDs:
    """Test that prompts include explicit criterion IDs to prevent AI from making them up."""
    
    def test_prompt_includes_criterion_ids(self):
        """Test that build_rubric_grading_prompt includes criterion_id values."""
        from cqc_cpcc.rubric_grading import build_rubric_grading_prompt
        
        rubric = Rubric(
            rubric_id="test_rubric",
            rubric_version="1.0",
            title="Test Rubric",
            criteria=[
                Criterion(
                    criterion_id="documentation",
                    name="Documentation Quality",
                    max_points=25,
                    scoring_mode="level_band",
                    levels=[
                        PerformanceLevel(
                            label="Exemplary",
                            score_min=23,
                            score_max=25,
                            description="Excellent docs"
                        ),
                    ]
                ),
            ]
        )
        
        prompt = build_rubric_grading_prompt(
            rubric=rubric,
            assignment_instructions="Write good code",
            student_submission="print('hello')"
        )
        
        # Prompt should explicitly mention the criterion_id
        assert "criterion_id" in prompt.lower()
        assert "`documentation`" in prompt or "documentation" in prompt
        assert "IMPORTANT" in prompt or "CRITICAL" in prompt
        assert "exact" in prompt.lower() or "EXACT" in prompt
        
    def test_prompt_warns_about_exact_ids(self):
        """Test that prompt explicitly warns about using exact criterion IDs."""
        from cqc_cpcc.rubric_grading import build_rubric_grading_prompt
        
        rubric = Rubric(
            rubric_id="test_rubric",
            rubric_version="1.0",
            title="Test Rubric",
            criteria=[
                Criterion(
                    criterion_id="code_quality",
                    name="Code Quality",
                    max_points=25,
                    scoring_mode="level_band",
                    levels=[
                        PerformanceLevel(
                            label="Good",
                            score_min=20,
                            score_max=25,
                            description="Good quality"
                        ),
                    ]
                ),
            ]
        )
        
        prompt = build_rubric_grading_prompt(
            rubric=rubric,
            assignment_instructions="Write code",
            student_submission="code here"
        )
        
        # Should have warning about NOT making up IDs
        assert "CRITICAL" in prompt or "IMPORTANT" in prompt
        # Should explicitly show the criterion_id format
        assert "`code_quality`" in prompt or "code_quality" in prompt
