#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Tests for deterministic rubric-based scoring fixes.

Tests verify:
1. C (2 majors) band yields score 61-70, not 0
2. Minor-to-major error conversion (4 minors = 1 major)
3. OpenAI points_earned=0 doesn't force final score=0
4. Config validation - no "manual" mode on automated criteria
5. Debug logger saves complete parsed output with structural fields
"""

import pytest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from cqc_cpcc.rubric_config import get_rubric_by_id, load_rubrics_from_config
from cqc_cpcc.rubric_models import RubricAssessmentResult, CriterionResult, DetectedError
from cqc_cpcc.rubric_grading import apply_backend_scoring
from cqc_cpcc.error_scoring import normalize_errors
from cqc_cpcc.utilities.AI.openai_debug import record_response, should_debug


@pytest.mark.unit
def test_c_band_2_majors_yields_correct_score():
    """Test that 'C (2 major errors)' yields score within 61-70, not 0."""
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    # Simulate OpenAI returning C level with 2 major errors but points_earned=0
    mock_result = RubricAssessmentResult(
        rubric_id="csc151_java_exam_rubric",
        rubric_version="2.0",
        total_points_possible=100,
        total_points_earned=0,  # OpenAI returned 0 (the bug!)
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_possible=100,
                points_earned=0,  # OpenAI returned 0 (the bug!)
                selected_level_label="C (2 major errors)",
                feedback="Has 2 major errors requiring correction.",
            )
        ],
        overall_feedback="Needs improvement.",
        error_counts_by_severity={"major": 2, "minor": 0},
        detected_errors=[
            DetectedError(
                code="CSC_151_EXAM_1_LOGIC_ERROR",
                name="Logic Error",
                severity="major",
                description="Algorithm error",
                occurrences=2,
            )
        ],
    )
    
    # Apply backend scoring - should compute deterministic score
    updated_result = apply_backend_scoring(rubric, mock_result)
    
    # C (2 majors) should yield midpoint of 61-70 = 65 points
    assert 61 <= updated_result.total_points_earned <= 70, \
        f"Expected score 61-70 for C (2 majors), got {updated_result.total_points_earned}"
    
    # Verify it's exactly 65 (midpoint) based on select_program_performance_level
    assert updated_result.total_points_earned == 65, \
        f"Expected exactly 65 points (midpoint), got {updated_result.total_points_earned}"
    
    # Verify criterion was also updated
    assert updated_result.criteria_results[0].points_earned == 65
    assert updated_result.criteria_results[0].selected_level_label == "C (2 major errors)"
    
    print(f"✅ Test passed: C (2 majors) → {updated_result.total_points_earned}/100")


@pytest.mark.unit
def test_minor_to_major_conversion():
    """Test that 4 minor errors convert to 1 major error."""
    # Test the normalize_errors function directly
    effective_major, effective_minor = normalize_errors(0, 4, conversion_ratio=4)
    
    assert effective_major == 1, f"Expected 1 major after conversion, got {effective_major}"
    assert effective_minor == 0, f"Expected 0 minor after conversion, got {effective_minor}"
    
    # Test with 7 minors (should become 1 major + 3 minor)
    effective_major, effective_minor = normalize_errors(0, 7, conversion_ratio=4)
    assert effective_major == 1
    assert effective_minor == 3
    
    # Test with existing majors + minors
    effective_major, effective_minor = normalize_errors(1, 8, conversion_ratio=4)
    assert effective_major == 3  # 1 original + 2 from conversion
    assert effective_minor == 0  # 8 % 4 = 0
    
    print("✅ Test passed: Minor-to-major conversion works correctly")


@pytest.mark.unit
def test_openai_zero_points_not_forced_to_zero():
    """Test that OpenAI returning points_earned=0 doesn't force final score to 0."""
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    # OpenAI returns A (1 minor) but mistakenly assigns 0 points
    mock_result = RubricAssessmentResult(
        rubric_id="csc151_java_exam_rubric",
        rubric_version="2.0",
        total_points_possible=100,
        total_points_earned=0,  # OpenAI computed wrong
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_possible=100,
                points_earned=0,  # OpenAI computed wrong
                selected_level_label="A (1 minor error)",
                feedback="Excellent work with one small issue.",
            )
        ],
        overall_feedback="Great submission!",
        error_counts_by_severity={"major": 0, "minor": 1},
        detected_errors=[
            DetectedError(
                code="CSC_151_EXAM_1_NAMING",
                name="Naming Issue",
                severity="minor",
                description="Variable naming",
                occurrences=1,
            )
        ],
    )
    
    # Apply backend scoring
    updated_result = apply_backend_scoring(rubric, mock_result)
    
    # Should compute correct score for A (1 minor) = 93
    assert updated_result.total_points_earned == 93, \
        f"Expected 93 points for A (1 minor), got {updated_result.total_points_earned}"
    
    # Verify OpenAI's 0 was overridden
    assert updated_result.total_points_earned != 0, \
        "Backend scoring should override OpenAI's incorrect 0 points"
    
    print(f"✅ Test passed: Backend overrode OpenAI's 0 → {updated_result.total_points_earned}/100")


@pytest.mark.unit
def test_rubric_config_no_manual_mode_on_automated_criteria():
    """Test that automated criteria don't have scoring_mode='manual'."""
    rubrics = load_rubrics_from_config()
    csc151_rubric = rubrics.get("csc151_java_exam_rubric")
    
    assert csc151_rubric is not None, "CSC151 rubric not found"
    
    # Find program_performance criterion
    program_performance = next(
        (c for c in csc151_rubric.criteria if c.criterion_id == "program_performance"),
        None
    )
    
    assert program_performance is not None, "program_performance criterion not found"
    
    # Verify it's not in manual mode
    assert program_performance.scoring_mode != "manual", \
        f"program_performance should not be 'manual' mode, got '{program_performance.scoring_mode}'"
    
    # It should be error_count mode
    assert program_performance.scoring_mode == "error_count", \
        f"program_performance should be 'error_count' mode, got '{program_performance.scoring_mode}'"
    
    print(f"✅ Test passed: program_performance is in '{program_performance.scoring_mode}' mode (not manual)")


@pytest.mark.unit
def test_debug_logger_saves_complete_parsed_output():
    """Test that debug logger saves complete parsed output without truncation."""
    # Create a temp directory for debug output
    with tempfile.TemporaryDirectory() as temp_dir:
        # Mock the debug environment variables
        with patch('cqc_cpcc.utilities.AI.openai_debug.CQC_OPENAI_DEBUG', True), \
             patch('cqc_cpcc.utilities.AI.openai_debug.CQC_OPENAI_DEBUG_SAVE_DIR', temp_dir), \
             patch('cqc_cpcc.utilities.AI.openai_debug.CQC_OPENAI_DEBUG_REDACT', True):
            
            # Create a mock parsed output (simulating RubricAssessmentResult)
            mock_parsed = RubricAssessmentResult(
                rubric_id="csc151_java_exam_rubric",
                rubric_version="2.0",
                total_points_possible=100,
                total_points_earned=65,
                criteria_results=[
                    CriterionResult(
                        criterion_id="program_performance",
                        criterion_name="Program Performance",
                        points_possible=100,
                        points_earned=65,
                        selected_level_label="C (2 major errors)",
                        feedback="Has errors.",
                    )
                ],
                overall_feedback="Needs work.",
                overall_band_label="C",
                error_counts_by_severity={"major": 2, "minor": 0},
                original_major_errors=2,
                original_minor_errors=0,
                effective_major_errors=2,
                effective_minor_errors=0,
            )
            
            # Create a long output text (> 500 chars to test truncation)
            long_output_text = json.dumps(mock_parsed.model_dump(mode='json')) + "X" * 1000
            
            # Record the response
            correlation_id = "test123"
            record_response(
                correlation_id=correlation_id,
                response=None,  # Not needed for this test
                schema_name="RubricAssessmentResult",
                decision_notes="parsed successfully",
                output_text=long_output_text,
                output_parsed=mock_parsed,
            )
            
            # Check that files were created
            temp_path = Path(temp_dir)
            files = list(temp_path.glob(f"*{correlation_id}*.json"))
            
            assert len(files) > 0, "No debug files were created"
            
            # Find the response_raw file
            response_raw_files = [f for f in files if "response_raw" in f.name]
            assert len(response_raw_files) > 0, "response_raw.json not created"
            
            # Read and verify response_raw contains full text
            with open(response_raw_files[0], 'r') as f:
                response_raw_data = json.load(f)
            
            assert "output" in response_raw_data
            assert "text" in response_raw_data["output"]
            # Verify full text is saved (not truncated to 500)
            assert len(response_raw_data["output"]["text"]) > 500, \
                f"response_raw should contain full text, got {len(response_raw_data['output']['text'])} chars"
            
            # Find the response_parsed file
            response_parsed_files = [f for f in files if "response_parsed" in f.name]
            assert len(response_parsed_files) > 0, "response_parsed.json not created"
            
            # Read and verify response_parsed contains structural fields
            with open(response_parsed_files[0], 'r') as f:
                response_parsed_data = json.load(f)
            
            assert "parsed_model" in response_parsed_data
            parsed_model = response_parsed_data["parsed_model"]
            
            # Verify key structural fields are present
            assert "total_points_earned" in parsed_model, "Missing total_points_earned"
            assert parsed_model["total_points_earned"] == 65, "Incorrect total_points_earned"
            
            assert "criteria_results" in parsed_model, "Missing criteria_results"
            assert len(parsed_model["criteria_results"]) > 0, "Empty criteria_results"
            
            criterion = parsed_model["criteria_results"][0]
            assert "selected_level_label" in criterion, "Missing selected_level_label"
            assert criterion["selected_level_label"] == "C (2 major errors)"
            assert "points_earned" in criterion, "Missing criterion points_earned"
            assert criterion["points_earned"] == 65
            
            # Verify error counts are present
            assert "error_counts_by_severity" in parsed_model, "Missing error_counts_by_severity"
            assert parsed_model["error_counts_by_severity"]["major"] == 2
            
            assert "effective_major_errors" in parsed_model, "Missing effective_major_errors"
            assert parsed_model["effective_major_errors"] == 2
            
            print("✅ Test passed: Debug logger saves complete output with structural fields")


if __name__ == "__main__":
    # Run tests manually
    print("Running deterministic scoring tests...")
    print()
    
    test_c_band_2_majors_yields_correct_score()
    test_minor_to_major_conversion()
    test_openai_zero_points_not_forced_to_zero()
    test_rubric_config_no_manual_mode_on_automated_criteria()
    test_debug_logger_saves_complete_parsed_output()
    
    print()
    print("All deterministic scoring tests passed! ✅")
