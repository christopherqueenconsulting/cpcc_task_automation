#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for student_feedback_builder module.

Tests that the feedback builder:
- Does NOT include numeric points/percentages/scores
- Includes error definition titles when present
- Works when no errors exist
- Works when no rubric exists (future)
- Formats feedback appropriately for student consumption
"""

import re

import pytest

from cqc_cpcc.rubric_models import (
    CriterionResult,
    DetectedError,
    RubricAssessmentResult,
)
from cqc_cpcc.student_feedback_builder import (
    _extract_improvements,
    _extract_strengths,
    _filter_score_mentions,
    _format_error_for_student,
    build_student_feedback,
)


@pytest.mark.unit
def test_build_student_feedback_no_numeric_patterns():
    """Test that feedback does not include points, percentages, or scores."""
    # Create a mock result with scores
    result = RubricAssessmentResult(
        rubric_id="test_rubric",
        rubric_version="1.0",
        total_points_possible=50,
        total_points_earned=42,
        criteria_results=[
            CriterionResult(
                criterion_id="understanding",
                criterion_name="Understanding",
                points_possible=50,
                points_earned=42,
                feedback="Good understanding demonstrated. Some concepts need review."
            )
        ],
        overall_feedback="Overall performance shows strong grasp of concepts."
    )
    
    feedback = build_student_feedback(result, student_name="Test Student")
    
    # Check for numeric patterns that should NOT be present
    # Points like "42/50" or "85/100"
    assert not re.search(r'\d+\s*/\s*\d+', feedback), "Feedback contains point ratios"
    
    # Percentages like "85%" or "84.5%"
    assert not re.search(r'\d+\.?\d*\s*%', feedback), "Feedback contains percentages"
    
    # Keywords that indicate scores
    assert 'points' not in feedback.lower(), "Feedback contains 'points' keyword"
    assert 'score' not in feedback.lower(), "Feedback contains 'score' keyword"
    assert 'earned' not in feedback.lower(), "Feedback contains 'earned' keyword"
    
    # Should include student name
    assert "Test Student" in feedback


@pytest.mark.unit
def test_build_student_feedback_includes_error_titles():
    """Test that error definition titles are included when errors exist."""
    result = RubricAssessmentResult(
        rubric_id="test_rubric",
        rubric_version="1.0",
        total_points_possible=50,
        total_points_earned=35,
        criteria_results=[
            CriterionResult(
                criterion_id="code_quality",
                criterion_name="Code Quality",
                points_possible=50,
                points_earned=35,
                feedback="Code has some quality issues."
            )
        ],
        overall_feedback="Overall submission needs improvement in code quality.",
        detected_errors=[
            DetectedError(
                code="NAMING_CONVENTION",
                name="Naming Convention Violation",
                severity="minor",
                description="Variable names do not follow camelCase convention",
                notes="Found 3 instances of incorrect naming"
            ),
            DetectedError(
                code="LOGIC_ERROR",
                name="Logic Error in Calculation",
                severity="major",
                description="Incorrect calculation in the interest computation method",
                notes="The formula uses addition instead of multiplication"
            )
        ]
    )
    
    feedback = build_student_feedback(result)
    
    # Check that error titles are present
    assert "Naming Convention Violation" in feedback
    assert "Logic Error in Calculation" in feedback
    
    # Check that error descriptions are present
    assert "Variable names do not follow camelCase convention" in feedback
    assert "Incorrect calculation in the interest computation method" in feedback
    
    # Check section headers
    assert "Errors Observed:" in feedback or "errors observed" in feedback.lower()


@pytest.mark.unit
def test_build_student_feedback_works_without_errors():
    """Test that feedback works correctly when no errors are detected."""
    result = RubricAssessmentResult(
        rubric_id="test_rubric",
        rubric_version="1.0",
        total_points_possible=100,
        total_points_earned=95,
        criteria_results=[
            CriterionResult(
                criterion_id="understanding",
                criterion_name="Understanding",
                points_possible=50,
                points_earned=48,
                feedback="Excellent understanding demonstrated throughout."
            ),
            CriterionResult(
                criterion_id="implementation",
                criterion_name="Implementation",
                points_possible=50,
                points_earned=47,
                feedback="Strong implementation with good practices."
            )
        ],
        overall_feedback="Excellent work overall!",
        detected_errors=None  # No errors
    )
    
    feedback = build_student_feedback(result)
    
    # Should not crash
    assert feedback is not None
    assert len(feedback) > 0
    
    # Should have strengths section (high scores)
    assert "Strengths:" in feedback or "strengths:" in feedback.lower()
    
    # Should NOT have errors section
    assert "Errors Observed:" not in feedback


@pytest.mark.unit
def test_build_student_feedback_empty_errors_list():
    """Test that feedback works with an empty errors list."""
    result = RubricAssessmentResult(
        rubric_id="test_rubric",
        rubric_version="1.0",
        total_points_possible=100,
        total_points_earned=80,
        criteria_results=[
            CriterionResult(
                criterion_id="code_quality",
                criterion_name="Code Quality",
                points_possible=100,
                points_earned=80,
                feedback="Good quality overall."
            )
        ],
        overall_feedback="Good work.",
        detected_errors=[]  # Empty list
    )
    
    feedback = build_student_feedback(result)
    
    # Should not crash and should not show errors section
    assert feedback is not None
    assert "Errors Observed:" not in feedback


@pytest.mark.unit
def test_filter_score_mentions():
    """Test that score filtering removes numeric patterns."""
    text = """
    Great work on this assignment!
    You scored 85/100 points.
    Your percentage was 85%.
    The understanding criterion earned you 42 points.
    Keep improving your skills.
    """
    
    filtered = _filter_score_mentions(text)
    
    # Should remove lines with scores
    assert "85/100" not in filtered
    assert "85%" not in filtered
    assert "42 points" not in filtered
    assert "earned" not in filtered
    
    # Should keep lines without scores
    assert "Great work" in filtered
    assert "Keep improving" in filtered


@pytest.mark.unit
def test_extract_strengths_from_high_scores():
    """Test that strengths are extracted from high-performing criteria."""
    result = RubricAssessmentResult(
        rubric_id="test",
        rubric_version="1.0",
        total_points_possible=100,
        total_points_earned=90,
        criteria_results=[
            CriterionResult(
                criterion_id="understanding",
                criterion_name="Understanding",
                points_possible=50,
                points_earned=48,  # 96% - should be strength
                feedback="Excellent understanding of core concepts demonstrated."
            ),
            CriterionResult(
                criterion_id="style",
                criterion_name="Code Style",
                points_possible=50,
                points_earned=42,  # 84% - should be strength
                feedback="Good adherence to style guidelines with minor issues."
            )
        ],
        overall_feedback="Strong work."
    )
    
    strengths = _extract_strengths(result)
    
    # Should extract both high-performing criteria
    assert len(strengths) >= 2
    assert any("Understanding" in s for s in strengths)
    assert any("Code Style" in s for s in strengths)


@pytest.mark.unit
def test_extract_improvements_from_low_scores():
    """Test that improvements are extracted from low-performing criteria."""
    result = RubricAssessmentResult(
        rubric_id="test",
        rubric_version="1.0",
        total_points_possible=100,
        total_points_earned=60,
        criteria_results=[
            CriterionResult(
                criterion_id="testing",
                criterion_name="Testing",
                points_possible=50,
                points_earned=25,  # 50% - should be improvement
                feedback="Testing coverage is insufficient. Add more test cases."
            ),
            CriterionResult(
                criterion_id="documentation",
                criterion_name="Documentation",
                points_possible=50,
                points_earned=35,  # 70% - should be improvement
                feedback="Documentation needs more detail in method descriptions."
            )
        ],
        overall_feedback="Needs improvement."
    )
    
    improvements = _extract_improvements(result)
    
    # Should extract both low-performing criteria
    assert len(improvements) >= 2
    assert any("Testing" in i for i in improvements)
    assert any("Documentation" in i for i in improvements)


@pytest.mark.unit
def test_format_error_for_student():
    """Test that errors are formatted appropriately for students."""
    error = DetectedError(
        code="SYNTAX_ERROR",
        name="Syntax Error",
        severity="major",
        description="Missing semicolon at end of statement",
        notes="Found on line 42 in the calculate method"
    )
    
    formatted = _format_error_for_student(error)
    
    # Should include human-readable name
    assert "Syntax Error" in formatted
    
    # Should include description
    assert "Missing semicolon" in formatted
    
    # Should include notes (context)
    assert "line 42" in formatted or "calculate method" in formatted


@pytest.mark.unit
def test_build_student_feedback_no_greeting():
    """Test feedback without greeting."""
    result = RubricAssessmentResult(
        rubric_id="test",
        rubric_version="1.0",
        total_points_possible=100,
        total_points_earned=85,
        criteria_results=[
            CriterionResult(
                criterion_id="test",
                criterion_name="Test",
                points_possible=100,
                points_earned=85,
                feedback="Good work."
            )
        ],
        overall_feedback="Overall good."
    )
    
    feedback = build_student_feedback(result, include_greeting=False)
    
    # Should not include greeting
    assert not feedback.startswith("Hi ")
    assert not feedback.startswith("Hello")


@pytest.mark.unit
def test_build_student_feedback_groups_errors_by_severity():
    """Test that errors are grouped by major and minor severity."""
    result = RubricAssessmentResult(
        rubric_id="test",
        rubric_version="1.0",
        total_points_possible=100,
        total_points_earned=70,
        criteria_results=[
            CriterionResult(
                criterion_id="test",
                criterion_name="Test",
                points_possible=100,
                points_earned=70,
                feedback="Needs improvement."
            )
        ],
        overall_feedback="Needs work.",
        detected_errors=[
            DetectedError(
                code="ERR1", name="Major Error 1",
                severity="major", description="Desc 1"
            ),
            DetectedError(
                code="ERR2", name="Minor Error 1",
                severity="minor", description="Desc 2"
            ),
            DetectedError(
                code="ERR3", name="Major Error 2",
                severity="major", description="Desc 3"
            ),
        ]
    )
    
    feedback = build_student_feedback(result)
    
    # Should have separate sections
    assert "Major Issues:" in feedback or "major issues" in feedback.lower()
    assert "Minor Issues:" in feedback or "minor issues" in feedback.lower()
    
    # Check order (major before minor)
    major_pos = feedback.lower().find("major")
    minor_pos = feedback.lower().find("minor")
    
    # Both should be found and major should come first
    assert major_pos != -1, "Major errors section not found in feedback"
    assert minor_pos != -1, "Minor errors section not found in feedback"
    assert major_pos < minor_pos, (
        "Major errors should appear before minor errors"
    )


@pytest.mark.unit
def test_build_student_feedback_limits_strengths_and_improvements():
    """Test that strengths and improvements are limited to reasonable counts."""
    # Create result with many criteria
    criteria = []
    for i in range(10):
        criteria.append(
            CriterionResult(
                criterion_id=f"crit_{i}",
                criterion_name=f"Criterion {i}",
                points_possible=10,
                points_earned=9 if i < 5 else 3,  # First 5 high, last 5 low
                feedback=(
                    f"Excellent work on criterion {i}" if i < 5
                    else f"Needs improvement on criterion {i}"
                )
            )
        )
    
    result = RubricAssessmentResult(
        rubric_id="test",
        rubric_version="1.0",
        total_points_possible=100,
        total_points_earned=60,
        criteria_results=criteria,
        overall_feedback="Mixed performance."
    )
    
    strengths = _extract_strengths(result)
    improvements = _extract_improvements(result)
    
    # Should limit counts
    assert len(strengths) <= 4, "Strengths should be limited to 4"
    assert len(improvements) <= 6, "Improvements should be limited to 6"
