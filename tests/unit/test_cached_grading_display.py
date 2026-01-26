#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for cached grading results display.

Tests ensure that:
1. Cached results display does NOT instantiate RubricModel
2. Display works even when rubric config is missing/changed/invalid
3. Graceful degradation when fields are missing
4. No Pydantic ValidationError when displaying cached results
"""

import importlib
from types import SimpleNamespace
import pytest
from unittest.mock import MagicMock, patch
from cqc_cpcc.rubric_models import RubricAssessmentResult, CriterionResult


def _import_grade_assignment_module():
    return importlib.import_module("src.cqc_streamlit_app.pages.4_Grade_Assignment")


class SessionState(SimpleNamespace):
    def get(self, key, default=None):
        return getattr(self, key, default)


@pytest.fixture
def mock_session_state():
    """Mock Streamlit session state."""
    return SessionState(
        grading_results_by_key={},
        expand_all_students=False,
        feedback_zip_bytes_by_key={},
    )


@pytest.fixture
def sample_cached_results():
    """Create sample cached RubricAssessmentResult objects."""
    return [
        ("Student1", RubricAssessmentResult(
            rubric_id="test_rubric",
            rubric_version="1.0",
            total_points_possible=100,
            total_points_earned=85,
            criteria_results=[
                CriterionResult(
                    criterion_id="understanding",
                    criterion_name="Understanding",
                    points_possible=50,
                    points_earned=45,
                    feedback="Good work"
                ),
                CriterionResult(
                    criterion_id="quality",
                    criterion_name="Quality",
                    points_possible=50,
                    points_earned=40,
                    feedback="Needs improvement"
                )
            ],
            overall_feedback="Overall good submission"
        )),
        ("Student2", RubricAssessmentResult(
            rubric_id="test_rubric",
            rubric_version="1.0",
            total_points_possible=100,
            total_points_earned=92,
            criteria_results=[
                CriterionResult(
                    criterion_id="understanding",
                    criterion_name="Understanding",
                    points_possible=50,
                    points_earned=48,
                    feedback="Excellent understanding"
                ),
                CriterionResult(
                    criterion_id="quality",
                    criterion_name="Quality",
                    points_possible=50,
                    points_earned=44,
                    feedback="Great quality"
                )
            ],
            overall_feedback="Excellent work"
        ))
    ]


@pytest.mark.unit
class TestCachedGradingDisplay:
    """Test cached grading results display functionality."""
    
    def test_cached_display_does_not_instantiate_rubric_model(
        self, 
        mock_session_state, 
        sample_cached_results
    ):
        """Test that displaying cached results does NOT instantiate RubricModel.
        
        This is the main regression test for the bug where placeholder_rubric
        was created with invalid parameters causing Pydantic ValidationError.
        """
        grade_assignment = _import_grade_assignment_module()
        # Mock Streamlit
        with patch.object(grade_assignment, 'st') as mock_st:
            mock_st.session_state = mock_session_state
            mock_st.session_state.grading_results_by_key['test_run_key'] = sample_cached_results
            mock_st.columns.side_effect = lambda spec: [MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))]
            
            # Mock the Rubric import to track if it's instantiated
            with patch.object(grade_assignment, 'Rubric') as mock_rubric:
                # Call the function
                grade_assignment.display_cached_grading_results('test_run_key', 'TestCourse_Exam1')
                
                # Assert Rubric was NEVER instantiated (key fix)
                mock_rubric.assert_not_called()
    
    def test_cached_display_handles_missing_fields_gracefully(
        self,
        mock_session_state
    ):
        """Test that cached display handles missing/invalid fields without crashing."""
        # Create a result with some missing optional fields
        incomplete_result = RubricAssessmentResult(
            rubric_id="test",
            rubric_version="1.0",
            total_points_possible=100,
            total_points_earned=0,
            criteria_results=[
                CriterionResult(
                    criterion_id="test",
                    criterion_name="Test",
                    points_possible=100,
                    points_earned=0,
                    feedback="Test"
                )
            ],
            overall_feedback="Test",
            overall_band_label=None,  # Missing band
            detected_errors=None  # Missing errors
        )
        
        cached_results = [("StudentIncomplete", incomplete_result)]
        
        grade_assignment = _import_grade_assignment_module()
        with patch.object(grade_assignment, 'st') as mock_st:
            mock_st.session_state = mock_session_state
            mock_st.session_state.grading_results_by_key['test_key'] = cached_results
            mock_st.columns.side_effect = lambda spec: [MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))]
            mock_st.expander.return_value.__enter__ = MagicMock()
            mock_st.expander.return_value.__exit__ = MagicMock()
            
            # Should not raise exception even with missing fields
            try:
                grade_assignment.display_cached_grading_results('test_key', 'TestCourse')
                # If we get here, test passed (no exception)
                assert True
            except Exception as e:
                pytest.fail(f"display_cached_grading_results raised unexpected exception: {e}")
    
    def test_cached_display_handles_zero_total_points(
        self,
        mock_session_state
    ):
        """Test handling of edge case where total_points_possible is 0."""
        result_with_zero_points = RubricAssessmentResult(
            rubric_id="test",
            rubric_version="1.0",
            total_points_possible=0,  # Edge case
            total_points_earned=0,
            criteria_results=[
                CriterionResult(
                    criterion_id="zero",
                    criterion_name="Zero",
                    points_possible=0,
                    points_earned=0,
                    feedback="Zero"
                )
            ],
            overall_feedback="Test"
        )
        
        cached_results = [("StudentZero", result_with_zero_points)]
        
        grade_assignment = _import_grade_assignment_module()
        with patch.object(grade_assignment, 'st') as mock_st:
            mock_st.session_state = mock_session_state
            mock_st.session_state.grading_results_by_key['test_key'] = cached_results
            mock_st.columns.side_effect = lambda spec: [MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))]
            
            # Should handle 0 points without division by zero
            try:
                grade_assignment.display_cached_grading_results('test_key', 'TestCourse')
                assert True
            except ZeroDivisionError:
                pytest.fail("display_cached_grading_results raised ZeroDivisionError")
    
    def test_no_rubric_validation_in_cached_path(self):
        """Test that cached path never requires rubric validation.
        
        This ensures that even if rubric_version is an int or criteria is empty,
        the cached display path will NOT trigger Pydantic validation.
        """
        # This test verifies the fix by ensuring we never call RubricModel()
        # with invalid parameters in the cached display path
        
        grade_assignment = _import_grade_assignment_module()
        with patch.object(grade_assignment, 'st') as mock_st:
            mock_st.session_state = SessionState(
                grading_results_by_key={
                    'key1': [("Student", MagicMock(
                        total_points_possible=100,
                        total_points_earned=85,
                        overall_band_label="Good"
                    ))]
                },
                expand_all_students=False,
                feedback_zip_bytes_by_key={},
            )
            mock_st.columns.side_effect = lambda spec: [MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))]
            
            # If this runs without ValidationError, the fix is working
            try:
                grade_assignment.display_cached_grading_results('key1', 'TestCourse')
                # Success - no validation error
                assert True
            except ValueError as e:
                if "rubric_version" in str(e) or "criteria" in str(e):
                    pytest.fail(
                        f"Cached display triggered rubric validation (should not happen): {e}"
                    )
                raise


@pytest.mark.unit
class TestGenerateFeedbackDocsAndZip:
    """Test feedback document generation without Rubric dependency."""
    
    def test_generate_docs_uses_total_points_not_rubric(self, sample_cached_results):
        """Test that _generate_feedback_docs_and_zip uses total_points_possible, not Rubric."""
        grade_assignment = _import_grade_assignment_module()
        with patch.object(grade_assignment, 'st') as mock_st:
            mock_st.session_state = SessionState(
                feedback_zip_bytes_by_key={},
            )
            mock_st.spinner.return_value.__enter__ = MagicMock()
            mock_st.spinner.return_value.__exit__ = MagicMock()
            
            with patch.object(grade_assignment, 'generate_student_feedback_doc'):
                with patch.object(grade_assignment, 'create_zip_file'):
                    # Call with total_points_possible instead of effective_rubric
                    try:
                        grade_assignment._generate_feedback_docs_and_zip(
                            all_results=sample_cached_results,
                            course_name="TestCourse",
                            total_points_possible=100,  # Direct value, no Rubric
                            model_name="test",
                            temperature=0.0,
                            run_key="test_key"
                        )
                        # If this succeeds, the function signature is correctly updated
                        assert True
                    except TypeError as e:
                        if "effective_rubric" in str(e):
                            pytest.fail(
                                "_generate_feedback_docs_and_zip still requires effective_rubric parameter"
                            )
                        raise


@pytest.mark.unit
class TestCachedErrorOnlyDisplay:
    """Test cached error-only grading results display."""
    
    def test_cached_error_only_display(self):
        """Test that error-only cached display renders without exceptions."""
        cached_results = [
            ("Student1", {
                "points_earned": 150,
                "max_points": 200,
                "major_count": 2,
                "minor_count": 1,
                "feedback_text": "Test feedback"
            }),
        ]
        
        grade_assignment = _import_grade_assignment_module()
        with patch.object(grade_assignment, 'st') as mock_st:
            mock_st.session_state = SessionState(
                error_only_results_by_key={'test_key': cached_results},
                error_only_feedback_zip_by_key={'test_key': '/tmp/test.zip'},
                expand_all_students=False,
            )
            mock_st.columns.side_effect = lambda spec: [MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))]
            mock_st.empty.return_value = MagicMock()
            
            try:
                with patch.object(grade_assignment, 'on_download_click'):
                    grade_assignment.display_cached_error_only_results('test_key', 'TestCourse_Exam1')
                assert True
            except Exception as e:
                pytest.fail(f"display_cached_error_only_results raised unexpected exception: {e}")
