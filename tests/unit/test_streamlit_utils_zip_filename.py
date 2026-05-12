"""Unit tests for Streamlit ZIP filename sanitization."""

#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import pytest

from cqc_streamlit_app.utils import sanitize_zip_filename


@pytest.mark.unit
def test_sanitize_zip_filename_removes_redundant_course_prefix_in_assignment() -> None:
    """Assignment segment should not repeat the course and section prefix."""
    result = sanitize_zip_filename("CSC_251_N804_CSC_251_Exam_2", "20260511_1713")
    assert result == "CSC_251_N804_Exam_2_Feedback_20260511_1713.zip"


@pytest.mark.unit
def test_sanitize_zip_filename_handles_course_without_section() -> None:
    """Course-only names should still produce stable filename structure."""
    result = sanitize_zip_filename("CSC_151_CSC_151_Exam_1", "20260511_1713")
    assert result == "CSC_151_Exam_1_Feedback_20260511_1713.zip"


@pytest.mark.unit
def test_sanitize_zip_filename_handles_spaces_and_hyphens() -> None:
    """Mixed separators in assignment name should normalize to underscores."""
    result = sanitize_zip_filename("CSC_251_N804_CSC-251: Exam 2", "20260511_1713")
    assert result == "CSC_251_N804_Exam_2_Feedback_20260511_1713.zip"

