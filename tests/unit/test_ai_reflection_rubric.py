#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Integration tests for AI Assignment Reflection Rubric.

Tests cover:
1. Rubric loads correctly from config
2. All criteria have correct scoring modes
3. Level-band scoring works with the rubric
4. Mock grading produces correct results
"""

import pytest
from unittest.mock import AsyncMock, patch

from cqc_cpcc.rubric_config import get_rubric_by_id
from cqc_cpcc.rubric_models import RubricAssessmentResult, CriterionResult
from cqc_cpcc.rubric_grading import grade_with_rubric, apply_backend_scoring


@pytest.mark.unit
def test_ai_reflection_rubric_loads():
    """Test that AI reflection rubric loads correctly from config."""
    rubric = get_rubric_by_id("ai_assignment_reflection_rubric")
    
    assert rubric.rubric_id == "ai_assignment_reflection_rubric"
    assert rubric.title == "AI Assignment Reflection Rubric"
    assert len(rubric.criteria) == 4
    assert rubric.total_points_possible == 100
    
    # Check course IDs
    assert "CSC151" in rubric.course_ids
    assert "CSC251" in rubric.course_ids


@pytest.mark.unit
def test_ai_reflection_rubric_criteria():
    """Test that all criteria in AI reflection rubric have correct setup."""
    rubric = get_rubric_by_id("ai_assignment_reflection_rubric")
    
    expected_criteria = {
        "tool_description_usage": 25,
        "intelligence_analysis": 30,
        "personal_goals_application": 25,
        "presentation_requirements": 20,
    }
    
    for criterion in rubric.criteria:
        assert criterion.criterion_id in expected_criteria
        assert criterion.max_points == expected_criteria[criterion.criterion_id]
        assert criterion.scoring_mode == "level_band"
        assert criterion.points_strategy == "min"
        assert len(criterion.levels) == 4  # Exemplary, Proficient, Developing, Beginning


@pytest.mark.unit
def test_ai_reflection_rubric_levels():
    """Test that performance levels are correctly defined."""
    rubric = get_rubric_by_id("ai_assignment_reflection_rubric")
    
    # Check first criterion as representative
    tool_usage = next(c for c in rubric.criteria if c.criterion_id == "tool_description_usage")
    
    assert len(tool_usage.levels) == 4
    
    # Check that levels have expected labels
    labels = [level.label for level in tool_usage.levels]
    assert "Exemplary" in labels
    assert "Proficient" in labels
    assert "Developing" in labels
    assert "Beginning" in labels
    
    # Check that levels cover the full range
    exemplary = next(l for l in tool_usage.levels if l.label == "Exemplary")
    assert exemplary.score_max == 25  # Max points
    
    beginning = next(l for l in tool_usage.levels if l.label == "Beginning")
    assert beginning.score_min == 0  # Min points


@pytest.mark.unit
def test_ai_reflection_rubric_overall_bands():
    """Test that overall bands are correctly defined."""
    rubric = get_rubric_by_id("ai_assignment_reflection_rubric")
    
    assert len(rubric.overall_bands) == 4
    
    # Check band ranges
    bands_dict = {band.label: (band.score_min, band.score_max) for band in rubric.overall_bands}
    
    assert bands_dict["Exemplary"] == (90, 100)
    assert bands_dict["Proficient"] == (75, 89)
    assert bands_dict["Developing"] == (60, 74)
    assert bands_dict["Beginning"] == (0, 59)


@pytest.mark.unit
def test_ai_reflection_backend_scoring():
    """Test backend scoring with AI reflection rubric."""
    rubric = get_rubric_by_id("ai_assignment_reflection_rubric")
    
    # Create mock result from OpenAI (with placeholder points)
    mock_result = RubricAssessmentResult(
        rubric_id="ai_assignment_reflection_rubric",
        rubric_version="1.0",
        total_points_possible=100,
        total_points_earned=0,  # Placeholder - will be computed
        criteria_results=[
            CriterionResult(
                criterion_id="tool_description_usage",
                criterion_name="Tool Description & Usage",
                points_possible=25,
                points_earned=0,  # Placeholder
                selected_level_label="Proficient",
                feedback="Good description of the AI tool and how it was used.",
            ),
            CriterionResult(
                criterion_id="intelligence_analysis",
                criterion_name="Intelligence Analysis",
                points_possible=30,
                points_earned=0,  # Placeholder
                selected_level_label="Exemplary",
                feedback="Excellent analysis of AI capabilities and limitations.",
            ),
            CriterionResult(
                criterion_id="personal_goals_application",
                criterion_name="Personal Goals & Application",
                points_possible=25,
                points_earned=0,  # Placeholder
                selected_level_label="Proficient",
                feedback="Clear connection to learning goals.",
            ),
            CriterionResult(
                criterion_id="presentation_requirements",
                criterion_name="Presentation & Requirements",
                points_possible=20,
                points_earned=0,  # Placeholder
                selected_level_label="Developing",
                feedback="Meets most requirements but has some organization issues.",
            ),
        ],
        overall_feedback="Overall strong reflection with good analysis.",
    )
    
    # Apply backend scoring
    updated_result = apply_backend_scoring(rubric, mock_result)
    
    # Check that points were computed (using "min" strategy)
    # tool_description_usage: Proficient = 19 (min of 19-22)
    # intelligence_analysis: Exemplary = 27 (min of 27-30)
    # personal_goals_application: Proficient = 19 (min of 19-22)
    # presentation_requirements: Developing = 12 (min of 12-14)
    # Total: 19 + 27 + 19 + 12 = 77
    
    assert updated_result.criteria_results[0].points_earned == 19
    assert updated_result.criteria_results[1].points_earned == 27
    assert updated_result.criteria_results[2].points_earned == 19
    assert updated_result.criteria_results[3].points_earned == 12
    assert updated_result.total_points_earned == 77
    
    # Check overall band (77 is in Proficient range: 75-89)
    assert updated_result.overall_band_label == "Proficient"


@pytest.mark.unit
@patch('cqc_cpcc.rubric_grading.get_structured_completion')
async def test_ai_reflection_full_grading(mock_openai):
    """Test full grading flow with AI reflection rubric."""
    rubric = get_rubric_by_id("ai_assignment_reflection_rubric")
    
    # Mock OpenAI response
    mock_result = RubricAssessmentResult(
        rubric_id="ai_assignment_reflection_rubric",
        rubric_version="1.0",
        total_points_possible=100,
        total_points_earned=0,  # Placeholder
        criteria_results=[
            CriterionResult(
                criterion_id="tool_description_usage",
                criterion_name="Tool Description & Usage",
                points_possible=25,
                points_earned=0,
                selected_level_label="Exemplary",
                feedback="Comprehensive tool description.",
                evidence=["Detailed feature list", "Usage examples"]
            ),
            CriterionResult(
                criterion_id="intelligence_analysis",
                criterion_name="Intelligence Analysis",
                points_possible=30,
                points_earned=0,
                selected_level_label="Exemplary",
                feedback="Insightful analysis.",
            ),
            CriterionResult(
                criterion_id="personal_goals_application",
                criterion_name="Personal Goals & Application",
                points_possible=25,
                points_earned=0,
                selected_level_label="Exemplary",
                feedback="Thoughtful connection to goals.",
            ),
            CriterionResult(
                criterion_id="presentation_requirements",
                criterion_name="Presentation & Requirements",
                points_possible=20,
                points_earned=0,
                selected_level_label="Exemplary",
                feedback="Exceeds all requirements.",
            ),
        ],
        overall_feedback="Excellent reflection paper demonstrating deep understanding.",
    )
    
    mock_openai.return_value = mock_result
    
    # Grade with rubric
    result = await grade_with_rubric(
        rubric=rubric,
        assignment_instructions="Write a reflection on using an AI tool...",
        student_submission="I used GitHub Copilot for this assignment...",
    )
    
    # Backend should have computed all Exemplary scores (min strategy)
    # tool: 23, intelligence: 27, goals: 23, presentation: 18
    # Total: 23 + 27 + 23 + 18 = 91
    
    assert result.total_points_earned == 91
    assert result.overall_band_label == "Exemplary"  # 91 >= 90
    
    # Verify individual scores
    assert result.criteria_results[0].points_earned == 23
    assert result.criteria_results[1].points_earned == 27
    assert result.criteria_results[2].points_earned == 23
    assert result.criteria_results[3].points_earned == 18


@pytest.mark.unit
def test_ai_reflection_with_mid_strategy():
    """Test AI reflection rubric with mid strategy."""
    # Create a variant rubric with mid strategy
    rubric = get_rubric_by_id("ai_assignment_reflection_rubric")
    
    # Override strategy for testing
    for criterion in rubric.criteria:
        criterion.points_strategy = "mid"
    
    # Create mock result
    mock_result = RubricAssessmentResult(
        rubric_id="ai_assignment_reflection_rubric",
        rubric_version="1.0",
        total_points_possible=100,
        total_points_earned=0,
        criteria_results=[
            CriterionResult(
                criterion_id="tool_description_usage",
                criterion_name="Tool Description & Usage",
                points_possible=25,
                points_earned=0,
                selected_level_label="Proficient",  # 19-22, mid=20
                feedback="Good.",
            ),
            CriterionResult(
                criterion_id="intelligence_analysis",
                criterion_name="Intelligence Analysis",
                points_possible=30,
                points_earned=0,
                selected_level_label="Proficient",  # 23-26, mid=24
                feedback="Good.",
            ),
            CriterionResult(
                criterion_id="personal_goals_application",
                criterion_name="Personal Goals & Application",
                points_possible=25,
                points_earned=0,
                selected_level_label="Proficient",  # 19-22, mid=20
                feedback="Good.",
            ),
            CriterionResult(
                criterion_id="presentation_requirements",
                criterion_name="Presentation & Requirements",
                points_possible=20,
                points_earned=0,
                selected_level_label="Proficient",  # 15-17, mid=16
                feedback="Good.",
            ),
        ],
        overall_feedback="Solid work.",
    )
    
    # Apply backend scoring
    updated_result = apply_backend_scoring(rubric, mock_result)
    
    # Check midpoint scoring: 20 + 24 + 20 + 16 = 80
    assert updated_result.total_points_earned == 80
    assert updated_result.overall_band_label == "Proficient"
