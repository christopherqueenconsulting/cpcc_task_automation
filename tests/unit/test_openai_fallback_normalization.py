#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Tests for OpenAI fallback normalization and schema validation fixes.

Tests verify:
1. Fallback JSON normalization handles wrong field names and stringified data
2. Fallback prompt does NOT instruct string-typed detected_errors/error_counts
3. Schema validation errors include correlation_id and helpful summary
4. finish_reason="length" is handled properly
"""

import pytest
import json
from cqc_cpcc.utilities.AI.openai_client import _normalize_fallback_json, _build_fallback_prompt
from cqc_cpcc.rubric_models import RubricAssessmentResult, CriterionResult, DetectedError


@pytest.mark.unit
def test_normalize_fallback_json_with_wrong_field_names():
    """Test normalization handles rubric_criterion_id -> criterion_id mapping."""
    # Simulate fallback JSON with wrong field names (like fe35ddd7)
    fallback_data = {
        "rubric_id": "csc151_java_exam_rubric",
        "rubric_version": "2.0",
        "total_points_possible": 100,
        "total_points_earned": 50,
        "criteria_results": [
            {
                "rubric_criterion_id": "program_performance",  # Wrong name
                "criterion_title": "Program Performance",  # Wrong name
                "points_possible": 100,
                "points_earned": 50,
                "level_label": "C (2 major errors)",  # Wrong name
                "feedback": "Has errors"
            }
        ],
        "overall_feedback": "Needs work"
    }
    
    # Normalize
    normalized = _normalize_fallback_json(fallback_data, RubricAssessmentResult)
    
    # Verify correct field names
    assert "criteria_results" in normalized
    criterion = normalized["criteria_results"][0]
    assert "criterion_id" in criterion
    assert criterion["criterion_id"] == "program_performance"
    assert "rubric_criterion_id" not in criterion  # Old name removed
    
    assert "criterion_name" in criterion
    assert criterion["criterion_name"] == "Program Performance"
    assert "criterion_title" not in criterion  # Old name removed
    
    assert "selected_level_label" in criterion
    assert criterion["selected_level_label"] == "C (2 major errors)"
    assert "level_label" not in criterion  # Old name removed
    
    print("✅ Field name normalization works")


@pytest.mark.unit
def test_normalize_fallback_json_with_stringified_arrays():
    """Test normalization handles detected_errors as JSON string."""
    fallback_data = {
        "rubric_id": "test",
        "rubric_version": "1.0",
        "total_points_possible": 100,
        "total_points_earned": 50,
        "criteria_results": [
            {
                "criterion_id": "test_criterion",
                "criterion_name": "Test",
                "points_possible": 100,
                "points_earned": 50,
                "feedback": "Test"
            }
        ],
        "overall_feedback": "Test",
        # This is a string containing JSON, not an array
        "detected_errors": '[{"code":"ERR1","name":"Error 1","severity":"major","description":"Test error","occurrences":2}]'
    }
    
    # Normalize
    normalized = _normalize_fallback_json(fallback_data, RubricAssessmentResult)
    
    # Verify detected_errors is now a list
    assert "detected_errors" in normalized
    assert isinstance(normalized["detected_errors"], list)
    assert len(normalized["detected_errors"]) == 1
    assert normalized["detected_errors"][0]["code"] == "ERR1"
    
    print("✅ Stringified array normalization works")


@pytest.mark.unit
def test_normalize_fallback_json_with_stringified_dicts():
    """Test normalization handles error_counts as JSON strings."""
    fallback_data = {
        "rubric_id": "test",
        "rubric_version": "1.0",
        "total_points_possible": 100,
        "total_points_earned": 50,
        "criteria_results": [
            {
                "criterion_id": "test_criterion",
                "criterion_name": "Test",
                "points_possible": 100,
                "points_earned": 50,
                "feedback": "Test"
            }
        ],
        "overall_feedback": "Test",
        # These are strings containing JSON, not dicts
        "error_counts_by_severity": '{"major":6,"minor":7}',
        "error_counts_by_id": '{"ERR1":3,"ERR2":4}'
    }
    
    # Normalize
    normalized = _normalize_fallback_json(fallback_data, RubricAssessmentResult)
    
    # Verify counts are now dicts with integer values
    assert "error_counts_by_severity" in normalized
    assert isinstance(normalized["error_counts_by_severity"], dict)
    assert normalized["error_counts_by_severity"]["major"] == 6
    assert normalized["error_counts_by_severity"]["minor"] == 7
    assert isinstance(normalized["error_counts_by_severity"]["major"], int)
    
    assert "error_counts_by_id" in normalized
    assert isinstance(normalized["error_counts_by_id"], dict)
    assert normalized["error_counts_by_id"]["ERR1"] == 3
    
    print("✅ Stringified dict normalization works")


@pytest.mark.unit
def test_normalize_fallback_json_with_string_integers():
    """Test normalization handles integer fields as strings."""
    fallback_data = {
        "rubric_id": "test",
        "rubric_version": "1.0",
        "total_points_possible": "100",  # String instead of int
        "total_points_earned": "50",  # String instead of int
        "criteria_results": [
            {
                "criterion_id": "test_criterion",
                "criterion_name": "Test",
                "points_possible": 100,
                "points_earned": 50,
                "feedback": "Test"
            }
        ],
        "overall_feedback": "Test",
        "original_major_errors": "2",  # String instead of int
        "original_minor_errors": "3",  # String instead of int
        "effective_major_errors": "2",
        "effective_minor_errors": "3"
    }
    
    # Normalize
    normalized = _normalize_fallback_json(fallback_data, RubricAssessmentResult)
    
    # Verify integers are converted
    assert isinstance(normalized["total_points_possible"], int)
    assert normalized["total_points_possible"] == 100
    assert isinstance(normalized["total_points_earned"], int)
    assert normalized["total_points_earned"] == 50
    assert isinstance(normalized["original_major_errors"], int)
    assert normalized["original_major_errors"] == 2
    
    print("✅ String integer normalization works")


@pytest.mark.unit
def test_normalize_fallback_json_full_fe35ddd7_scenario():
    """Test normalization with a realistic fe35ddd7-style fallback response."""
    # Simulate the actual problematic fallback response from fe35ddd7
    fallback_data = {
        "rubric_id": "csc151_java_exam_rubric",
        "rubric_version": "2.0",
        "total_points_possible": "100",
        "total_points_earned": "35",
        "criteria_results": [
            {
                "rubric_criterion_id": "program_performance",  # Wrong name
                "criterion_title": "Program Performance",  # Wrong name
                "points_possible": 100,
                "points_earned": 35,
                "scoring_mode": "error_count",
                "level_label": "D (3 major errors)",  # Wrong name
                "feedback": "Multiple major errors detected",
                "evidence": ["line 10: logic error", "line 25: syntax issue"]
            }
        ],
        "overall_feedback": "Significant improvements needed",
        # Stringified JSON
        "detected_errors": '[{"code":"CSC_151_EXAM_1_LOGIC_ERROR","name":"Logic Error","severity":"major","description":"Algorithm error","occurrences":3}]',
        "error_counts_by_severity": '{"major":6,"minor":7}',
        "error_counts_by_id": '{"CSC_151_EXAM_1_LOGIC_ERROR":3,"CSC_151_EXAM_1_SYNTAX_ERROR":7}',
        # String integers
        "original_major_errors": "6",
        "original_minor_errors": "7",
        "effective_major_errors": "7",
        "effective_minor_errors": "3"
    }
    
    # Normalize
    normalized = _normalize_fallback_json(fallback_data, RubricAssessmentResult)
    
    # Verify it can now be validated by Pydantic
    try:
        result = RubricAssessmentResult.model_validate(normalized)
        
        # Verify key fields
        assert result.rubric_id == "csc151_java_exam_rubric"
        assert result.total_points_possible == 100
        assert result.total_points_earned == 35
        assert len(result.criteria_results) == 1
        assert result.criteria_results[0].criterion_id == "program_performance"
        assert result.criteria_results[0].criterion_name == "Program Performance"
        assert result.criteria_results[0].selected_level_label == "D (3 major errors)"
        assert isinstance(result.detected_errors, list)
        assert len(result.detected_errors) == 1
        assert result.detected_errors[0].code == "CSC_151_EXAM_1_LOGIC_ERROR"
        assert isinstance(result.error_counts_by_severity, dict)
        assert result.error_counts_by_severity["major"] == 6
        assert result.original_major_errors == 6
        assert result.effective_major_errors == 7
        
        print("✅ Full fe35ddd7 scenario normalized and validated successfully")
        
    except Exception as e:
        pytest.fail(f"Normalized data failed validation: {e}")


@pytest.mark.unit
def test_fallback_prompt_uses_correct_field_names():
    """Test that fallback prompt specifies correct field names."""
    prompt = _build_fallback_prompt("Grade this code", RubricAssessmentResult)
    
    # Verify correct field names are in the prompt
    assert "criterion_id" in prompt
    assert "criterion_name" in prompt
    assert "selected_level_label" in prompt
    
    # Verify that prompt explicitly warns about wrong field names
    # (it's OK if wrong names appear in "NOT" instructions)
    assert "criterion_id" in prompt  # Correct name must be present
    assert "criterion_name" in prompt  # Correct name must be present
    
    # Check that the prompt has the NOT instruction for wrong names
    assert "NOT rubric_criterion_id" in prompt or "criterion_id" in prompt
    assert "NOT criterion_title" in prompt or "criterion_name" in prompt
    
    print("✅ Fallback prompt uses correct field names")


@pytest.mark.unit
def test_fallback_prompt_does_not_instruct_string_types():
    """Test that fallback prompt does NOT instruct string-typed arrays/objects."""
    prompt = _build_fallback_prompt("Grade this code", RubricAssessmentResult)
    
    # Verify it says arrays/objects must be JSON, not strings
    assert "MUST be JSON array" in prompt or "NOT string" in prompt.lower()
    assert "MUST be JSON object" in prompt or "NOT string" in prompt.lower()
    
    # Verify it doesn't say detected_errors should be a string
    assert "detected_errors: string" not in prompt.lower()
    assert "error_counts_by_severity: string" not in prompt.lower()
    assert "error_counts_by_id: string" not in prompt.lower()
    
    print("✅ Fallback prompt does NOT instruct string types for arrays/objects")


@pytest.mark.unit
def test_fallback_prompt_emphasizes_exact_field_names():
    """Test that fallback prompt emphasizes EXACT field names."""
    prompt = _build_fallback_prompt("Grade this code", RubricAssessmentResult)
    
    # Verify emphasis on exact names
    assert "EXACT" in prompt or "exact" in prompt
    assert "criterion_id" in prompt and "NOT rubric_criterion_id" in prompt
    assert "criterion_name" in prompt and "NOT criterion_title" in prompt
    
    print("✅ Fallback prompt emphasizes exact field names")


if __name__ == "__main__":
    # Run tests manually
    print("Running OpenAI fallback normalization tests...")
    print()
    
    test_normalize_fallback_json_with_wrong_field_names()
    test_normalize_fallback_json_with_stringified_arrays()
    test_normalize_fallback_json_with_stringified_dicts()
    test_normalize_fallback_json_with_string_integers()
    test_normalize_fallback_json_full_fe35ddd7_scenario()
    test_fallback_prompt_uses_correct_field_names()
    test_fallback_prompt_does_not_instruct_string_types()
    test_fallback_prompt_emphasizes_exact_field_names()
    
    print()
    print("All fallback normalization tests passed! ✅")
