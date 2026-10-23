#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""The neutral two-effort comparison and the mixed-effort check (plan PR 18)."""

import pytest

from cqc_cpcc import effort_compare as ec
from cqc_cpcc.rubric_models import CriterionResult, DetectedError, RequirementResult, RubricAssessmentResult

pytestmark = pytest.mark.unit


def _result(earned, label, level="Good", errors=(), requirements=()):
    return RubricAssessmentResult(
        rubric_id="r", rubric_version="1", total_points_possible=200, total_points_earned=earned,
        criteria_results=[CriterionResult(criterion_id="c", criterion_name="C", points_possible=200,
                                          points_earned=earned, selected_level_label=level, feedback="ok")],
        overall_feedback="note", graded_with_model=label,
        detected_errors=[DetectedError(code=c, name=c.title(), severity="minor", description="d")
                         for c in errors],
        requirement_results=[RequirementResult(requirement_id=i, status=s) for i, s in requirements] or None)


def test_the_diff_reports_score_level_errors_and_requirements_without_a_verdict():
    old = _result(170, "openai/x@high", "Good", ["A", "B"], [("R1", "met"), ("R2", "partial")])
    new = _result(150, "openai/x@low", "Fair", ["B", "C"], [("R1", "met"), ("R2", "missing")])
    diff = ec.compare_results(old, new)
    assert (diff.old_score, diff.new_score, diff.possible) == (170, 150, 200)
    assert (diff.old_level, diff.new_level) == ("Good", "Fair")
    assert diff.errors_added == ("C",)
    assert diff.errors_removed == ("A",)
    assert diff.requirement_changes == (("R2", "partial", "missing"),)
    assert (diff.old_label, diff.new_label) == ("openai/x@high", "openai/x@low")
    # Neutral: the comparison carries facts only, never a "better" or "worse".
    assert not any(hasattr(diff, name) for name in ("better", "winner", "higher", "delta"))


def test_a_requirement_only_one_side_assessed_is_reported():
    diff = ec.compare_results(_result(10, "m@low"), _result(10, "m@high", requirements=[("R1", "met")]))
    assert diff.requirement_changes == (("R1", "not assessed", "met"),)


def test_identical_results_differ_in_nothing():
    diff = ec.compare_results(_result(10, "m@low", errors=["A"]), _result(10, "m@high", errors=["A"]))
    assert diff.errors_added == diff.errors_removed == diff.requirement_changes == ()


def test_the_keep_note_names_both_efforts_and_both_scores():
    note = ec.keep_note(ec.compare_results(_result(170, "openai/x@high"), _result(150, "openai/x@low")))
    assert (note.stage, note.action) == ("effort", "note")
    assert "effort low (150/200)" in note.reason and "effort high (170/200)" in note.reason


def test_split_label():
    assert ec.split_label("openai/gpt-6-luna@high") == ("openai/gpt-6-luna", "high")
    assert ec.split_label("openai/gpt-6-luna") == ("openai/gpt-6-luna", None)
    assert ec.split_label(None) == (None, None)


def test_the_mix_finds_the_majority_and_the_others():
    results = [("S1", _result(1, "m@high")), ("S2", _result(1, "m@high")), ("S3", _result(1, "m@low")),
               ("S4", _result(0, None))]  # S4 was held by the validity gate: no model ran
    mix = ec.effort_mix(results)
    assert mix.mixed
    assert (mix.majority_label, mix.majority_effort) == ("m@high", "high")
    assert mix.others == ("S3",)


def test_one_label_is_not_mixed_and_a_tie_goes_to_the_first_seen():
    assert not ec.effort_mix([("S1", _result(1, "m@high")), ("S2", _result(1, "m@high"))]).mixed
    assert not ec.effort_mix([]).mixed
    tie = ec.effort_mix([("S1", _result(1, "m@low")), ("S2", _result(1, "m@high"))])
    assert tie.majority_label == "m@low" and tie.others == ("S2",)


def test_a_different_model_at_the_same_effort_counts_as_mixed():
    mix = ec.effort_mix([("S1", _result(1, "a@high")), ("S2", _result(1, "a@high")), ("S3", _result(1, "b@high"))])
    assert mix.mixed and mix.others == ("S3",) and mix.majority_effort == "high"
