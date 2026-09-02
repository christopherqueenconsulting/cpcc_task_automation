#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for backend scoring in rubric grading.

Tests the apply_backend_scoring function to ensure scores are computed correctly
for program_performance, level_band, and error_count criteria.
"""

import pytest
from cqc_cpcc.rubric_config import get_rubric_by_id
from cqc_cpcc.rubric_models import RubricAssessmentResult, CriterionResult
from cqc_cpcc.rubric_grading import apply_backend_scoring


@pytest.mark.unit
def test_csc151_program_performance_0_errors():
    """Test CSC151 program_performance scoring with 0 errors -> A+ (195 points on 200-point scale)."""
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    result = RubricAssessmentResult(
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.rubric_version,
        total_points_earned=0,  # Placeholder
        total_points_possible=200,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_earned=0,  # Placeholder
                points_possible=200,
                feedback="Perfect submission, no errors detected.",
                selected_level_label=None
            )
        ],
        overall_feedback="Excellent work!",
        overall_band_label=None,
        detected_errors=[],
        error_counts_by_severity={"major": 0, "minor": 0}
    )
    
    updated = apply_backend_scoring(rubric, result)
    
    assert 191 <= updated.total_points_earned <= 200  # A+ range
    assert updated.criteria_results[0].selected_level_label == "A+ (0 errors)"
    assert updated.effective_major_errors == 0
    assert updated.effective_minor_errors == 0


@pytest.mark.unit
def test_csc151_program_performance_2_minor_errors():
    """Test CSC151 program_performance scoring with 2 minor errors -> A- (175 points on 200-point scale)."""
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    result = RubricAssessmentResult(
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.rubric_version,
        total_points_earned=0,
        total_points_possible=200,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_earned=0,
                points_possible=200,
                feedback="Two minor errors found.",
                selected_level_label=None
            )
        ],
        overall_feedback="Good work with minor issues.",
        overall_band_label=None,
        detected_errors=[],
        error_counts_by_severity={"major": 0, "minor": 2}
    )
    
    updated = apply_backend_scoring(rubric, result)
    
    assert 171 <= updated.total_points_earned <= 180  # A- range
    assert updated.criteria_results[0].selected_level_label == "A- (2 minor errors)"
    assert updated.original_major_errors == 0
    assert updated.original_minor_errors == 2
    assert updated.effective_major_errors == 0
    assert updated.effective_minor_errors == 2


@pytest.mark.unit
def test_csc151_program_performance_4_minor_converts_to_1_major():
    """Test CSC151 error conversion: 4 minor = 1 major -> B- (150 points on 200-point scale)."""
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    result = RubricAssessmentResult(
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.rubric_version,
        total_points_earned=0,
        total_points_possible=200,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_earned=0,
                points_possible=200,
                feedback="Four minor errors detected.",
                selected_level_label=None
            )
        ],
        overall_feedback="Work needs improvement.",
        overall_band_label=None,
        detected_errors=[],
        error_counts_by_severity={"major": 0, "minor": 4}
    )
    
    updated = apply_backend_scoring(rubric, result)
    
    # 4 minor errors should convert to 1 major error
    assert updated.original_major_errors == 0
    assert updated.original_minor_errors == 4
    assert updated.effective_major_errors == 1
    assert updated.effective_minor_errors == 0
    
    # 1 major error -> B- = 141-160 range (200-point scale)
    assert 141 <= updated.total_points_earned <= 160
    assert updated.criteria_results[0].selected_level_label == "B- (4 minor errors or 1 major error)"


@pytest.mark.unit
def test_csc151_program_performance_1_major_5_minor():
    """Test CSC151 with 1 major + 5 minor -> effective 2 major, 1 minor -> C (130 points on 200-point scale)."""
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    result = RubricAssessmentResult(
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.rubric_version,
        total_points_earned=0,
        total_points_possible=200,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_earned=0,
                points_possible=200,
                feedback="Multiple errors detected.",
                selected_level_label=None
            )
        ],
        overall_feedback="Significant issues found.",
        overall_band_label=None,
        detected_errors=[],
        error_counts_by_severity={"major": 1, "minor": 5}
    )
    
    updated = apply_backend_scoring(rubric, result)
    
    # 1 major + 5 minor = 1 major + (1 major from 4 minor) + 1 minor = 2 major, 1 minor
    assert updated.original_major_errors == 1
    assert updated.original_minor_errors == 5
    assert updated.effective_major_errors == 2
    assert updated.effective_minor_errors == 1
    
    # 2 major errors -> C = 121-140 range (200-point scale)
    assert 121 <= updated.total_points_earned <= 140
    assert updated.criteria_results[0].selected_level_label == "C (2 major errors)"


@pytest.mark.unit
def test_csc151_program_performance_no_error_counts_fallback():
    """Test CSC151 with missing error_counts_by_severity (should use 0 errors)."""
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    result = RubricAssessmentResult(
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.rubric_version,
        total_points_earned=0,
        total_points_possible=200,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_earned=0,
                points_possible=200,
                feedback="No error counts provided.",
                selected_level_label=None
            )
        ],
        overall_feedback="Unknown error status.",
        overall_band_label=None,
        detected_errors=[],
        error_counts_by_severity=None  # Missing error counts
    )
    
    updated = apply_backend_scoring(rubric, result)
    
    # Should default to 0 errors -> A+ = 191-200 range (200-point scale)
    assert 191 <= updated.total_points_earned <= 200
    assert updated.criteria_results[0].selected_level_label == "A+ (0 errors)"
    assert updated.original_major_errors == 0
    assert updated.original_minor_errors == 0


@pytest.mark.unit
def test_level_band_scoring_proficient():
    """Test level_band scoring with Proficient level selection."""
    rubric = get_rubric_by_id("ai_assignment_reflection_rubric")
    
    # This rubric has level_band criteria
    result = RubricAssessmentResult(
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.rubric_version,
        total_points_earned=0,  # Placeholder
        total_points_possible=100,
        criteria_results=[
            CriterionResult(
                criterion_id="tool_description_usage",
                criterion_name="Tool Description & Usage Context",
                points_earned=0,  # Placeholder
                points_possible=25,
                feedback="Good description of tool usage.",
                selected_level_label="Proficient"  # LLM selected this level
            ),
            CriterionResult(
                criterion_id="intelligence_analysis",
                criterion_name="Intelligence & Pattern Analysis",
                points_earned=0,
                points_possible=30,
                feedback="Excellent analysis of intelligence patterns.",
                selected_level_label="Exemplary"
            ),
            CriterionResult(
                criterion_id="personal_goals_application",
                criterion_name="Personal Goals & Application",
                points_earned=0,
                points_possible=25,
                feedback="Clear articulation of goals.",
                selected_level_label="Proficient"
            ),
            CriterionResult(
                criterion_id="presentation_requirements",
                criterion_name="Presentation & Requirements",
                points_earned=0,
                points_possible=20,
                feedback="Met all presentation requirements.",
                selected_level_label="Proficient"
            )
        ],
        overall_feedback="Strong submission overall.",
        overall_band_label=None,
        detected_errors=[]
    )
    
    updated = apply_backend_scoring(rubric, result)
    
    # Check that scores were computed from level ranges
    # Proficient for tool_description_usage (19-22) - uses "min" strategy by default
    assert updated.criteria_results[0].points_earned == 19
    
    # Exemplary for intelligence_analysis (27-30) - uses "min" strategy
    assert updated.criteria_results[1].points_earned == 27
    
    # Total should be sum of all criteria
    assert updated.total_points_earned > 0
    assert updated.total_points_earned == sum(c.points_earned for c in updated.criteria_results)


@pytest.mark.unit
def test_scoring_consistency_across_display():
    """Test that per-student scores match grading summary scores."""
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    result = RubricAssessmentResult(
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.rubric_version,
        total_points_earned=0,
        total_points_possible=200,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_earned=0,
                points_possible=200,
                feedback="Three minor errors.",
                selected_level_label=None
            )
        ],
        overall_feedback="Good work.",
        overall_band_label=None,
        detected_errors=[],
        error_counts_by_severity={"major": 0, "minor": 3}
    )
    
    updated = apply_backend_scoring(rubric, result)
    
    # Calculate what the UI would display
    total_points_card = updated.total_points_earned
    total_points_possible_card = updated.total_points_possible
    percentage_card = (total_points_card / total_points_possible_card * 100) if total_points_possible_card > 0 else 0
    band_card = updated.overall_band_label
    
    # Calculate what the summary table would show (should be the same)
    total_points_summary = updated.total_points_earned
    percentage_summary = (total_points_summary / updated.total_points_possible * 100) if updated.total_points_possible > 0 else 0
    band_summary = updated.overall_band_label
    
    # Assert consistency
    assert total_points_card == total_points_summary
    assert abs(percentage_card - percentage_summary) < 0.01
    assert band_card == band_summary
    
    # Verify actual values (B level is 161-170 on 200-point scale)
    assert 161 <= updated.total_points_earned <= 170
    assert 80 <= percentage_card <= 85


@pytest.mark.unit
def test_csc134_program_performance_0_errors_returns_outstanding():
    """Test CSC134 apply_backend_scoring: 0 errors → Outstanding (30 pts on v3 rubric)."""
    rubric = get_rubric_by_id("csc134_cpp_exam_rubric")

    result = RubricAssessmentResult(
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.rubric_version,
        total_points_earned=0,
        total_points_possible=100,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_earned=0,
                points_possible=100,
                feedback="No errors detected.",
                selected_level_label=None,
            )
        ],
        overall_feedback="Outstanding submission.",
        overall_band_label=None,
        detected_errors=[],
        error_counts_by_severity={"major": 0, "minor": 0},
    )

    updated = apply_backend_scoring(rubric, result)

    # CSC134 v3 rubric: Outstanding = score_max of 30
    assert updated.total_points_earned == 30.0
    assert updated.criteria_results[0].points_earned == 30.0
    assert updated.criteria_results[0].selected_level_label == "Outstanding"
    # With zero errors, original and effective counts are identical.
    assert updated.original_major_errors == 0
    assert updated.original_minor_errors == 0
    assert updated.effective_major_errors == 0
    assert updated.effective_minor_errors == 0


@pytest.mark.unit
def test_csc134_program_performance_3_minor_remains_above_average_after_normalization():
    """Test CSC134 apply_backend_scoring: 3 minor stays Above Average (24 pts) after normalization."""
    rubric = get_rubric_by_id("csc134_cpp_exam_rubric")

    result = RubricAssessmentResult(
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.rubric_version,
        total_points_earned=0,
        total_points_possible=100,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_earned=0,
                points_possible=100,
                feedback="Three minor errors found.",
                selected_level_label=None,
            )
        ],
        overall_feedback="Above average work.",
        overall_band_label=None,
        detected_errors=[],
        error_counts_by_severity={"major": 0, "minor": 3},
    )

    updated = apply_backend_scoring(rubric, result)

    # 3 minor errors do not cross the 4:1 conversion threshold, so the effective
    # counts remain 0 major / 3 minor and the level stays Above Average.
    assert updated.total_points_earned == 24.0
    assert updated.criteria_results[0].points_earned == 24.0
    assert updated.criteria_results[0].selected_level_label == "Above Average"
    assert updated.original_major_errors == 0
    assert updated.original_minor_errors == 3
    assert updated.effective_major_errors == 0
    assert updated.effective_minor_errors == 3


@pytest.mark.unit
def test_csc134_program_performance_3_major_returns_needs_improvement():
    """Test CSC134 apply_backend_scoring: 3 major → Needs Improvement (12 pts on v3 rubric).
    
    Note: In rubric v3, "Below Average" was removed; 3-major threshold now maps to "Needs Improvement".
    """
    rubric = get_rubric_by_id("csc134_cpp_exam_rubric")

    result = RubricAssessmentResult(
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.rubric_version,
        total_points_earned=0,
        total_points_possible=100,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_earned=0,
                points_possible=100,
                feedback="Three major errors detected.",
                selected_level_label=None,
            )
        ],
        overall_feedback="Below average submission.",
        overall_band_label=None,
        detected_errors=[],
        error_counts_by_severity={"major": 3, "minor": 0},
    )

    updated = apply_backend_scoring(rubric, result)

    # CSC134 v3: 3 major errors → Needs Improvement (score_max=12)
    assert updated.total_points_earned == 12.0
    assert updated.criteria_results[0].points_earned == 12.0
    assert updated.criteria_results[0].selected_level_label == "Needs Improvement"
    assert updated.original_major_errors == 3
    assert updated.original_minor_errors == 0
    assert updated.effective_major_errors == 3
    assert updated.effective_minor_errors == 0


@pytest.mark.unit
def test_csc134_program_performance_5_minor_uses_effective_counts_for_level_selection():
    """Test CSC134 apply_backend_scoring: 5 minor normalizes to 1 major + 1 minor for level selection."""
    rubric = get_rubric_by_id("csc134_cpp_exam_rubric")

    result = RubricAssessmentResult(
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.rubric_version,
        total_points_earned=0,
        total_points_possible=100,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_earned=0,
                points_possible=100,
                feedback="Five minor errors found.",
                selected_level_label=None,
            )
        ],
        overall_feedback="Average submission.",
        overall_band_label=None,
        detected_errors=[],
        error_counts_by_severity={"major": 0, "minor": 5},
    )

    updated = apply_backend_scoring(rubric, result)

    assert updated.original_major_errors == 0
    assert updated.original_minor_errors == 5
    assert updated.effective_major_errors == 1
    assert updated.effective_minor_errors == 1
    assert updated.total_points_earned == 24.0
    assert updated.criteria_results[0].points_earned == 24.0
    assert updated.criteria_results[0].selected_level_label == "Above Average"
    assert updated.overall_band_label == "Above Average"


@pytest.mark.unit
def test_csc134_versus_csc151_both_use_effective_counts_but_keep_rubric_specific_levels():
    """Verify CSC134 and CSC151 both normalize 5 minor errors, then apply rubric-specific level selection."""
    csc134_rubric = get_rubric_by_id("csc134_cpp_exam_rubric")
    csc151_rubric = get_rubric_by_id("csc151_java_exam_rubric")

    # 5 minor errors: under both rubrics → converts to 1 major + 1 minor (effective)
    # CSC134: effective counts still map to Above Average on the 30-point rubric.
    # CSC151: effective counts map to the 1-major bucket on the 200-point rubric.
    def make_result(rubric, minor_errors):
        points_possible = 100 if "csc134" in rubric.rubric_id else 200
        return RubricAssessmentResult(
            rubric_id=rubric.rubric_id,
            rubric_version=rubric.rubric_version,
            total_points_earned=0,
            total_points_possible=points_possible,
            criteria_results=[
                CriterionResult(
                    criterion_id="program_performance",
                    criterion_name="Program Performance",
                    points_earned=0,
                    points_possible=points_possible,
                    feedback="Test feedback.",
                    selected_level_label=None,
                )
            ],
            overall_feedback="Test.",
            overall_band_label=None,
            detected_errors=[],
            error_counts_by_severity={"major": 0, "minor": minor_errors},
        )

    csc134_result = apply_backend_scoring(csc134_rubric, make_result(csc134_rubric, 5))
    csc151_result = apply_backend_scoring(csc151_rubric, make_result(csc151_rubric, 5))

    # CSC134 v3: normalization produces 1 major + 1 minor, which still maps to
    # Above Average when effective counts are used for level selection.
    assert csc134_result.total_points_earned == 24.0
    assert csc134_result.criteria_results[0].selected_level_label == "Above Average"
    assert csc134_result.effective_major_errors == 1
    assert csc134_result.effective_minor_errors == 1
    assert csc134_result.original_major_errors == 0
    assert csc134_result.original_minor_errors == 5

    # CSC151: normalization produces 1 major + 1 minor and level selection uses
    # the effective major-count bucket.
    # → B- (141-160 pts on 200-point scale)
    assert 141 <= csc151_result.total_points_earned <= 160
    assert csc151_result.criteria_results[0].selected_level_label == "B- (4 minor errors or 1 major error)"
    assert csc151_result.effective_major_errors == 1
    assert csc151_result.effective_minor_errors == 1




@pytest.mark.unit
class TestManualOnlyRubricAggregation:
    """A manual-only rubric still needs backend aggregation.

    The grading prompt tells the model to set total_points_earned=0 and let the
    backend recalculate. apply_backend_scoring used to return early when no
    criterion needed per-criterion computation, so every all-manual rubric shipped
    a 0 score no matter what the model assigned.
    """

    @staticmethod
    def _result(rubric, points_by_criterion, levels_by_criterion=None):
        levels_by_criterion = levels_by_criterion or {}
        return RubricAssessmentResult(
            rubric_id=rubric.rubric_id,
            rubric_version=rubric.rubric_version,
            total_points_earned=0,  # What the prompt asks the model to send
            total_points_possible=rubric.total_points_possible,
            criteria_results=[
                CriterionResult(
                    criterion_id=criterion.criterion_id,
                    criterion_name=criterion.name,
                    points_earned=points_by_criterion.get(criterion.criterion_id),
                    points_possible=criterion.max_points,
                    feedback="Feedback for %s." % criterion.criterion_id,
                    selected_level_label=levels_by_criterion.get(
                        criterion.criterion_id
                    ),
                )
                for criterion in rubric.criteria
                if criterion.enabled
            ],
            overall_feedback="Overall feedback.",
        )

    def test_totals_are_recalculated_from_ai_assigned_points(self):
        rubric = get_rubric_by_id("default_100pt_rubric")
        assert all(c.scoring_mode == "manual" for c in rubric.criteria if c.enabled)

        awarded = {c.criterion_id: c.max_points for c in rubric.criteria if c.enabled}
        updated = apply_backend_scoring(rubric, self._result(rubric, awarded))

        assert updated.total_points_earned == rubric.total_points_possible
        assert all(cr.points_earned is not None for cr in updated.criteria_results)

    def test_missing_points_are_recovered_from_the_selected_level(self):
        """A model that picks a level but omits points has told us enough to score."""
        rubric = get_rubric_by_id("default_100pt_rubric")
        criterion = next(c for c in rubric.criteria if c.enabled and c.levels)
        top_level = max(criterion.levels, key=lambda level: level.score_max)

        updated = apply_backend_scoring(
            rubric,
            self._result(rubric, {}, {criterion.criterion_id: top_level.label}),
        )

        recovered = next(
            cr
            for cr in updated.criteria_results
            if cr.criterion_id == criterion.criterion_id
        )
        assert recovered.points_earned == top_level.score_min  # points_strategy="min"
        assert updated.total_points_earned == top_level.score_min

    def test_missing_points_with_no_level_fall_back_to_zero(self):
        rubric = get_rubric_by_id("default_100pt_rubric")

        updated = apply_backend_scoring(rubric, self._result(rubric, {}))

        assert updated.total_points_earned == 0
        assert all(cr.points_earned == 0 for cr in updated.criteria_results)


@pytest.mark.unit
class TestManualPromptGuidance:
    """The example JSON is the strongest signal the model gets about points_earned."""

    def test_manual_rubric_example_shows_a_number_not_null(self):
        from cqc_cpcc.rubric_grading import build_rubric_grading_prompt

        prompt = build_rubric_grading_prompt(
            rubric=get_rubric_by_id("default_100pt_rubric"),
            assignment_instructions="Write Hello World.",
            student_submission="print('Hello World')",
        )

        assert '"points_earned": null' not in prompt
        assert "scoring_mode='manual' criteria: points_earned MUST be a" in prompt

    def test_level_band_rubric_example_still_shows_null(self):
        from cqc_cpcc.rubric_grading import build_rubric_grading_prompt

        prompt = build_rubric_grading_prompt(
            rubric=get_rubric_by_id("csc113_week1_reflection_rubric"),
            assignment_instructions="Write a reflection.",
            student_submission="My reflection.",
        )

        assert '"points_earned": null' in prompt


@pytest.mark.unit
class TestPointsFromLevelLabel:
    """Resolving points from a selected level decides a student's score.

    score_level_band_criterion refuses criteria that are not level_band, so this
    helper is what recovers a manual criterion the model left unscored. Each
    points_strategy is a different number on a transcript.
    """

    @staticmethod
    def _criterion(strategy):
        from cqc_cpcc.rubric_models import Criterion, PerformanceLevel

        return Criterion(
            criterion_id="quality",
            name="Code Quality",
            description="Quality of the submission",
            max_points=25,
            points_strategy=strategy,
            levels=[
                PerformanceLevel(
                    label="Exemplary", score_min=23, score_max=25, description="Best"
                ),
                PerformanceLevel(
                    label="Proficient", score_min=18, score_max=22, description="Good"
                ),
            ],
        )

    @pytest.mark.parametrize(
        "strategy, expected",
        [("min", 18), ("max", 22), ("mid", 20)],
    )
    def test_each_strategy_picks_its_end_of_the_band(self, strategy, expected):
        from cqc_cpcc.rubric_grading import points_from_level_label

        assert points_from_level_label(
            "Proficient", self._criterion(strategy)
        ) == expected

    def test_an_unknown_label_resolves_to_nothing(self):
        """Never invent a score for a level the rubric does not define."""
        from cqc_cpcc.rubric_grading import points_from_level_label

        assert points_from_level_label("Outstanding", self._criterion("min")) is None

    @pytest.mark.parametrize("label", [None, ""])
    def test_no_label_resolves_to_nothing(self, label):
        from cqc_cpcc.rubric_grading import points_from_level_label

        assert points_from_level_label(label, self._criterion("min")) is None

    def test_a_criterion_without_levels_resolves_to_nothing(self):
        from cqc_cpcc.rubric_grading import points_from_level_label
        from cqc_cpcc.rubric_models import Criterion

        criterion = Criterion(
            criterion_id="c", name="C", description="d", max_points=10
        )
        assert points_from_level_label("Exemplary", criterion) is None


@pytest.mark.unit
class TestCompileGateAndManualScoringTogether:
    """The compile gate runs immediately before aggregation, so they interact.

    These landed on separate branches: the gate corrects ``detected_errors`` before
    scoring, and the manual-mode branch recovers points from a selected level. A
    merge that kept both hunks textually could still ship the wrong score, so this
    pins the combination rather than each half alone.
    """

    @staticmethod
    def _manual_result(rubric, levels_by_criterion, detected_errors=None):
        return RubricAssessmentResult(
            rubric_id=rubric.rubric_id,
            rubric_version=rubric.rubric_version,
            total_points_earned=0,  # what the prompt asks the model to send
            total_points_possible=rubric.total_points_possible,
            criteria_results=[
                CriterionResult(
                    criterion_id=criterion.criterion_id,
                    criterion_name=criterion.name,
                    points_earned=None,  # the model omitted them
                    points_possible=criterion.max_points,
                    feedback="Feedback for %s." % criterion.criterion_id,
                    selected_level_label=levels_by_criterion.get(
                        criterion.criterion_id),
                )
                for criterion in rubric.criteria
                if criterion.enabled
            ],
            overall_feedback="Overall feedback.",
            detected_errors=detected_errors or [],
        )

    @staticmethod
    def _top_levels(rubric):
        return {
            criterion.criterion_id: criterion.levels[0].label
            for criterion in rubric.criteria
            if criterion.enabled and criterion.levels
        }

    def test_a_gate_run_does_not_cost_an_all_manual_rubric_its_score(self):
        """The gate mutates errors; manual criteria still score from their levels."""
        from cqc_cpcc.rubric_grading import apply_compile_gate

        rubric = get_rubric_by_id("default_100pt_rubric")
        assert all(c.scoring_mode == "manual" for c in rubric.criteria if c.enabled)

        result = self._manual_result(rubric, self._top_levels(rubric))
        gated, info = apply_compile_gate(
            result, {"Main.java": "public class Main { void x() { } }"}
        )
        scored = apply_backend_scoring(rubric, gated)

        assert scored.total_points_earned > 0, (
            "an all-manual rubric must not score 0 just because the gate ran"
        )
        assert all(cr.points_earned is not None for cr in scored.criteria_results)

    def test_the_gate_is_a_no_op_when_there_are_no_source_files(self):
        """Grading a non-code submission must not disturb the result at all."""
        from cqc_cpcc.rubric_grading import apply_compile_gate

        rubric = get_rubric_by_id("default_100pt_rubric")
        result = self._manual_result(rubric, self._top_levels(rubric))

        gated, info = apply_compile_gate(result, None)

        assert info["ran"] is False
        assert gated is result

    def test_an_unsupported_language_leaves_the_llm_judgment_alone(self):
        from cqc_cpcc.rubric_grading import apply_compile_gate

        rubric = get_rubric_by_id("default_100pt_rubric")
        result = self._manual_result(rubric, self._top_levels(rubric))

        gated, info = apply_compile_gate(result, {"analysis.sas": "proc print; run;"})

        assert info["action"] in ("skipped", "none")
        assert gated.detected_errors == result.detected_errors

    def test_scoring_still_recovers_points_after_the_gate_clears_an_error(self):
        """The gate removing a false "Does Not Compile" must not zero the criteria."""
        from cqc_cpcc.rubric_grading import apply_compile_gate

        rubric = get_rubric_by_id("default_100pt_rubric")
        result = self._manual_result(rubric, self._top_levels(rubric))

        # Valid Python: the gate has a definitive verdict without a toolchain.
        gated, info = apply_compile_gate(result, {"solution.py": "x = 1\n"})
        scored = apply_backend_scoring(rubric, gated)

        assert info["compiles"] is True
        assert scored.total_points_earned > 0


@pytest.mark.unit
class TestCompileGateHelpers:
    """The two helpers that decide what the gate can act on."""

    @staticmethod
    def _error_def(error_id, name, enabled=True):
        from cqc_cpcc.error_definitions_models import ErrorDefinition

        return ErrorDefinition(
            error_id=error_id, name=name,
            description="The program does not compile" if "COMPILE" in error_id
            else "Something else",
            severity_category="major", enabled=enabled,
        )

    def test_no_definitions_means_no_compile_definition(self):
        from cqc_cpcc.rubric_grading import _find_compile_error_def

        assert _find_compile_error_def(None) is None
        assert _find_compile_error_def([]) is None

    def test_a_rubric_without_a_compile_error_yields_none(self):
        from cqc_cpcc.rubric_grading import _find_compile_error_def

        others = [self._error_def("STYLE_ERROR", "Poor Style")]

        assert _find_compile_error_def(others) is None

    def test_an_enabled_definition_is_preferred_over_a_disabled_one(self):
        from cqc_cpcc.rubric_grading import _find_compile_error_def

        definitions = [
            self._error_def("OLD_DOES_NOT_COMPILE", "Does Not Compile", enabled=False),
            self._error_def("CSC151_DOES_NOT_COMPILE", "Does Not Compile"),
        ]

        assert _find_compile_error_def(definitions).error_id == \
            "CSC151_DOES_NOT_COMPILE"

    def test_a_disabled_definition_is_still_returned_when_it_is_the_only_one(self):
        """Better to annotate a disabled definition than to invent an error id."""
        from cqc_cpcc.rubric_grading import _find_compile_error_def

        only = [self._error_def("DOES_NOT_COMPILE", "Does Not Compile", enabled=False)]

        assert _find_compile_error_def(only).error_id == "DOES_NOT_COMPILE"

    def test_raw_source_text_is_used_as_is(self):
        from cqc_cpcc.rubric_grading import _read_source_files

        assert _read_source_files({"Main.java": "class Main {}"}) == [
            ("Main.java", "class Main {}")
        ]

    def test_a_path_on_disk_is_read(self, tmp_path):
        """StudentSubmission.files maps a name to a temp path, not to source text."""
        from cqc_cpcc.rubric_grading import _read_source_files

        path = tmp_path / "Main.java"
        path.write_text("class Main { }")

        assert _read_source_files({"Main.java": str(path)}) == [
            ("Main.java", "class Main { }")
        ]

    def test_an_unreadable_path_is_skipped_not_fatal(self, tmp_path, mocker):
        """One unreadable file must not cost the gate the rest of the submission."""
        from cqc_cpcc.rubric_grading import _read_source_files

        bad = tmp_path / "Broken.java"
        bad.write_text("class Broken {}")
        mocker.patch("builtins.open", side_effect=PermissionError("denied"))

        assert _read_source_files({"Broken.java": str(bad)}) == []

    def test_empty_and_whitespace_only_entries_are_dropped(self):
        from cqc_cpcc.rubric_grading import _read_source_files

        assert _read_source_files(
            {"Empty.java": "", "Blank.java": "   \n ", "Real.java": "class R {}"}
        ) == [("Real.java", "class R {}")]

    def test_a_non_string_value_is_ignored(self):
        from cqc_cpcc.rubric_grading import _read_source_files

        assert _read_source_files({"Weird.java": None, "Real.java": "class R {}"}) == [
            ("Real.java", "class R {}")
        ]

    def test_no_source_files_at_all_reads_as_nothing(self):
        from cqc_cpcc.rubric_grading import _read_source_files

        assert _read_source_files(None) == []
        assert _read_source_files({}) == []


@pytest.mark.unit
class TestCompileGateAddsAMissingCompileError:
    """When the code really does not compile, the gate supplies the error."""

    @staticmethod
    def _result():
        rubric = get_rubric_by_id("default_100pt_rubric")
        criterion = next(c for c in rubric.criteria if c.enabled)
        return RubricAssessmentResult(
            rubric_id=rubric.rubric_id,
            rubric_version=rubric.rubric_version,
            total_points_earned=0,
            total_points_possible=rubric.total_points_possible,
            criteria_results=[
                CriterionResult(
                    criterion_id=criterion.criterion_id,
                    criterion_name=criterion.name,
                    points_earned=criterion.max_points,
                    points_possible=criterion.max_points,
                    feedback="ok",
                )
            ],
            overall_feedback="ok",
            detected_errors=[],
        )

    @staticmethod
    def _compile_definition():
        from cqc_cpcc.error_definitions_models import ErrorDefinition

        return ErrorDefinition(
            error_id="CSC151_DOES_NOT_COMPILE", name="Does Not Compile",
            description="The program does not compile",
            severity_category="major", enabled=True,
        )

    def test_broken_python_gets_the_rubrics_compile_error_added(self):
        from cqc_cpcc.rubric_grading import apply_compile_gate

        gated, info = apply_compile_gate(
            self._result(), {"solution.py": "def broken(:\n"},
            [self._compile_definition()],
        )

        assert info["compiles"] is False
        assert info["action"] == "added"
        assert [e.code for e in gated.detected_errors] == ["CSC151_DOES_NOT_COMPILE"]

    def test_the_real_compiler_diagnostics_are_attached(self):
        """Without them the instructor cannot see why the gate disagreed."""
        from cqc_cpcc.rubric_grading import apply_compile_gate

        gated, _ = apply_compile_gate(
            self._result(), {"solution.py": "def broken(:\n"},
            [self._compile_definition()],
        )

        notes = gated.detected_errors[0].notes or ""
        assert "SyntaxError" in notes
        assert "does not compile" in notes

    def test_a_very_long_diagnostic_is_truncated(self, mocker):
        """g++ can emit thousands of lines; the report has to stay readable.

        Python's compile() stops at the first SyntaxError, so a real long diagnostic
        cannot be produced here -- the compiler result is stubbed to exercise the
        truncation itself.
        """
        from cqc_cpcc.rubric_grading import apply_compile_gate
        from cqc_cpcc.utilities.compiler_gate import CompileResult

        mocker.patch(
            "cqc_cpcc.utilities.compiler_gate.check_submission",
            return_value=CompileResult(
                "cpp", supported=True, compiles=False,
                errors="error: expected ';' before '}' token\n" * 400,
                tool="g++", files_checked=["main.cpp"],
            ),
        )

        gated, info = apply_compile_gate(
            self._result(), {"main.cpp": "int main() { }"},
            [self._compile_definition()],
        )

        assert info["compiles"] is False
        notes = gated.detected_errors[0].notes or ""
        assert "truncated" in notes
        assert len(notes) < 2000

    def test_a_failure_with_no_compile_definition_leaves_errors_alone(self):
        """Inventing an error id the rubric does not define would corrupt scoring."""
        from cqc_cpcc.rubric_grading import apply_compile_gate

        gated, info = apply_compile_gate(
            self._result(), {"solution.py": "def broken(:\n"}, []
        )

        assert info["action"] == "no_compile_definition"
        assert gated.detected_errors == []
