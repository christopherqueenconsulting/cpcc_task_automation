#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Each distinct error type counts once, minors and majors (Christopher, 2026-10-22)."""

import pytest

from cqc_cpcc.rubric_config import get_rubric_by_id
from cqc_cpcc.rubric_grading import apply_backend_scoring
from cqc_cpcc.rubric_models import (
    Criterion, CriterionResult, DetectedError, ErrorConversionRules, ErrorCountScoringRules, Rubric,
    RubricAssessmentResult,
)

RATIO_4_RUBRICS = ("csc151_java_exam_rubric", "csc134_cpp_exam_rubric", "csc134_cpp_final_exam_rubric")


def _error(code, severity, occurrences=None):
    return DetectedError(code=code, name=code.title(), severity=severity, description="d", occurrences=occurrences)


def _score(errors, rubric_key="csc151_java_exam_rubric", **extra):
    rubric = get_rubric_by_id(rubric_key)
    criterion = rubric.criteria[0]
    result = RubricAssessmentResult(
        rubric_id=rubric.rubric_id, rubric_version=rubric.rubric_version,
        total_points_possible=criterion.max_points, total_points_earned=0, overall_feedback="x",
        criteria_results=[CriterionResult(criterion_id=criterion.criterion_id, criterion_name=criterion.name,
                                          points_possible=criterion.max_points, points_earned=0, feedback="x")],
        detected_errors=errors, **extra)
    return apply_backend_scoring(rubric, result)


@pytest.mark.unit
def test_one_minor_seen_four_times_is_one_minor():
    scored = _score([_error("CSC_151_EXAM_1_OUTPUT_FORMATTING", "minor", occurrences=4)])
    assert (scored.effective_major_errors, scored.effective_minor_errors) == (0, 1)
    assert scored.total_points_earned == 190                      # A (1 minor), not B- (1 major)


@pytest.mark.unit
def test_three_occurrences_of_one_major_type_are_one_major():
    scored = _score([_error("CSC_151_EXAM_1_OUTPUT_IMPACT_ERROR", "major", occurrences=3)])
    assert scored.effective_major_errors == 1
    assert scored.total_points_earned == 160                      # B-, not C or below


@pytest.mark.unit
def test_the_same_type_listed_twice_counts_once():
    scored = _score([_error("CSC_151_EXAM_1_OUTPUT_IMPACT_ERROR", "major"),
                     _error("CSC_151_EXAM_1_OUTPUT_IMPACT_ERROR", "major")])
    assert scored.effective_major_errors == 1


@pytest.mark.unit
def test_four_different_minor_types_still_convert_to_one_major():
    scored = _score([_error(f"CSC_151_EXAM_1_MINOR_{i}", "minor") for i in range(4)])
    assert (scored.effective_major_errors, scored.effective_minor_errors) == (1, 0)
    assert scored.total_points_earned == 160


@pytest.mark.unit
def test_counts_the_model_reports_without_listing_errors_cost_nothing():
    scored = _score([], error_counts_by_severity={"major": 3, "minor": 2})
    assert (scored.original_major_errors, scored.original_minor_errors) == (0, 0)
    assert scored.total_points_earned == 200


@pytest.mark.unit
def test_model_counts_never_override_the_listed_errors():
    scored = _score([_error("CSC_151_EXAM_1_OUTPUT_FORMATTING", "minor", occurrences=4)],
                    error_counts_by_severity={"major": 0, "minor": 4})
    assert scored.effective_minor_errors == 1 and scored.total_points_earned == 190


@pytest.mark.unit
@pytest.mark.parametrize("rubric_key", RATIO_4_RUBRICS)
def test_every_ratio_4_rubric_counts_types_once_and_says_so(rubric_key):
    scored = _score([_error("SOME_MINOR", "minor", occurrences=5)], rubric_key=rubric_key)
    assert (scored.effective_major_errors, scored.effective_minor_errors) == (0, 1)
    rubric = get_rubric_by_id(rubric_key)
    for text in (rubric.description, rubric.criteria[0].description):
        assert "once, however often it occurs" in text and "4 different minor error types" in text
        assert "every 4 minor errors" not in text.lower()


@pytest.mark.unit
def test_codes_are_compared_case_insensitively():
    scored = _score([_error("CSC_151_EXAM_1_X", "minor"), _error("csc_151_exam_1_x ", "minor"),
                     _error(" Csc_151_Exam_1_X", "minor")])
    assert (scored.effective_major_errors, scored.effective_minor_errors) == (0, 1)


@pytest.mark.unit
def test_one_code_at_two_severities_counts_once_as_major():
    scored = _score([_error("CSC_151_EXAM_1_OUTPUT_IMPACT_ERROR", "minor"),
                     _error("CSC_151_EXAM_1_OUTPUT_IMPACT_ERROR", "major")])
    assert (scored.effective_major_errors, scored.effective_minor_errors) == (1, 0)


@pytest.mark.unit
@pytest.mark.parametrize("rubric_key", ["csc134_cpp_exam_rubric", "csc134_cpp_final_exam_rubric"])
def test_csc134_text_names_no_level_conversion_rules_out(rubric_key):
    text = get_rubric_by_id(rubric_key).criteria[0].description
    assert "level table below" in text
    assert not any(phrase in text for phrase in ("3-4 minor", "5 minor", ">5 minor", "Below Average"))


@pytest.mark.unit
def test_plain_error_count_rubric_with_no_errors_and_no_counts_scores_zero_errors():
    rubric = Rubric(rubric_id="plain", rubric_version="1.0", title="Plain", criteria=[Criterion(
        criterion_id="quality", name="Quality", max_points=100, scoring_mode="error_count",
        error_rules=ErrorCountScoringRules(major_weight=40, minor_weight=10, floor_score=0,
                                           error_conversion=ErrorConversionRules(minor_to_major_ratio=4)))])
    result = RubricAssessmentResult(
        rubric_id="plain", rubric_version="1.0", total_points_possible=100, total_points_earned=0,
        overall_feedback="x", detected_errors=[], error_counts_by_severity=None,
        criteria_results=[CriterionResult(criterion_id="quality", criterion_name="Quality",
                                          points_possible=100, points_earned=0, feedback="x")])
    scored = apply_backend_scoring(rubric, result)
    assert (scored.original_major_errors, scored.original_minor_errors) == (0, 0)
    assert scored.total_points_earned == 100
