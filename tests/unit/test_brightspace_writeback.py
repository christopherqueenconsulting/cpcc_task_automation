#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for BrightSpace draft grade write-back.

Covers the pure core (score buffer, feedback HTML, result->item mapping, name
matching) plus the dry-run orchestration (mocked Selenium). The dry-run path must
never attempt a save.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import cqc_cpcc.utilities.brightspace_writeback as wb


# ---------------------------------------------------------------------------
# Score buffer (configurable, add-pct-of-max, capped)
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("score,maxp,pct,expected", [
    (80, 100, 10, 90.0),      # +10% of 100
    (95, 100, 10, 100.0),     # capped at max
    (100, 100, 10, 100.0),    # already max
    (80, 100, 0, 80.0),       # buffer disabled
    (40, 50, 20, 50.0),       # +20% of 50 = +10 -> 50 (capped)
    (40, 50, 10, 45.0),       # +5
    (80, 100, -5, 80.0),      # negative pct clamped to 0
])
def test_apply_score_buffer(score, maxp, pct, expected):
    assert wb.apply_score_buffer(score, maxp, pct) == expected


@pytest.mark.unit
def test_apply_score_buffer_zero_max_is_safe():
    assert wb.apply_score_buffer(0, 0, 10) == 0.0
    assert wb.apply_score_buffer(5, 0, 10) == 5.0


# ---------------------------------------------------------------------------
# Feedback HTML composition
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_build_feedback_html_overall_only():
    html = wb.build_feedback_html("Nice work.", criteria=None)
    assert html == "<p>Nice work.</p>"


@pytest.mark.unit
def test_build_feedback_html_escapes_and_includes_criteria_and_band():
    crit = SimpleNamespace(
        criterion_name="Logic & Flow", criterion_id="logic",
        points_earned=23.0, points_possible=25, selected_level_label="Proficient",
        feedback="Good <handling>",
    )
    html = wb.build_feedback_html("Overall good.", [crit], include_criteria=True,
                                  band_label="Proficient")
    assert "<p>Overall good.</p>" in html
    assert "<strong>Overall:</strong> Proficient" in html
    assert "Logic &amp; Flow (23/25) — Proficient" in html  # escaped + formatted
    assert "Good &lt;handling&gt;" in html                   # escaped feedback
    assert "<ul>" in html and "<li>" in html


@pytest.mark.unit
def test_build_feedback_html_excludes_criteria_when_flag_false():
    crit = SimpleNamespace(criterion_name="X", points_earned=1, points_possible=2,
                           selected_level_label=None, feedback="hi")
    html = wb.build_feedback_html("Summary.", [crit], include_criteria=False)
    assert "<ul>" not in html
    assert html == "<p>Summary.</p>"


# ---------------------------------------------------------------------------
# Result -> write-item mapping (applies buffer + parses name)
# ---------------------------------------------------------------------------

def _result(earned, possible, feedback="ok", band="Proficient", criteria=None):
    return SimpleNamespace(
        total_points_earned=earned, total_points_possible=possible,
        overall_feedback=feedback, overall_band_label=band,
        criteria_results=criteria or [],
    )


@pytest.mark.unit
def test_build_write_items_applies_buffer_and_parses_name():
    results = [
        ("39786-640693 - Aiden Rodriguez - Oct 10, 2025 234 PM", _result(80, 100)),
    ]
    items = wb.build_write_items_from_results(
        results, buffer_pct=10, include_criteria_feedback=False,
        name_parser=lambda s: "Aiden Rodriguez",
    )
    assert len(items) == 1
    it = items[0]
    assert it.display_name == "Aiden Rodriguez"
    assert it.raw_score == 80.0
    assert it.score == 90.0           # buffered
    assert it.max_points == 100.0
    # Overall feedback + band, but no per-criterion list (include_criteria_feedback=False).
    assert it.feedback_html == "<p>ok</p>\n<p><strong>Overall:</strong> Proficient</p>"


@pytest.mark.unit
def test_build_write_items_uses_default_name_parser():
    # Real parser turns "Id - Name - Date" into the clean name.
    results = [("39786-640693 - Jane Doe - Oct 1, 2025 100 PM", _result(50, 100))]
    items = wb.build_write_items_from_results(results, buffer_pct=0)
    assert items[0].display_name == "Jane Doe"
    assert items[0].score == 50.0


# ---------------------------------------------------------------------------
# Name matching
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_normalize_name():
    assert wb._normalize_name("  Anne-Marie  O'Neil ") == "anne marie o neil"


@pytest.mark.unit
def test_match_items_to_learners_exact_and_unmatched():
    items = [
        wb.GradeWriteItem("k1", "Jane Doe", 80, 90, 100, "<p>x</p>"),
        wb.GradeWriteItem("k2", "John Q. Public", 70, 80, 100, "<p>y</p>"),
    ]
    learners = [
        {"name": "jane doe", "userId": "1"},
        {"name": "Someone Else", "userId": "2"},
    ]
    matches, unmatched_items, unmatched_learners = wb.match_items_to_learners(items, learners)
    assert [m.item.student_key for m in matches] == ["k1"]
    assert matches[0].learner["userId"] == "1"
    assert [i.student_key for i in unmatched_items] == ["k2"]
    assert [l["userId"] for l in unmatched_learners] == ["2"]


# ---------------------------------------------------------------------------
# Dry-run orchestration (quiz route) — must locate but never save
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_push_quiz_grades_dry_run_reports_without_saving(mocker):
    import cqc_cpcc.utilities.brightspace_fetch as bf

    driver = MagicMock()
    wait = MagicMock()
    # _locate_write_targets reads via execute_script — say the score field exists.
    driver.execute_script.return_value = {"score": True, "feedback": True}

    mocker.patch.object(wb, "detect_route", create=True)  # not used (imported in func)
    mocker.patch("cqc_cpcc.utilities.brightspace_submissions.detect_route",
                 return_value="quiz")
    mocker.patch.object(bf, "derive_quiz_grading_url", return_value="https://grid")
    mocker.patch.object(bf, "_open_and_login")
    mocker.patch.object(bf, "_set_max_results_per_page")
    mocker.patch.object(bf, "_gather_quiz_attempts", return_value=[
        {"name": "Jane Doe", "userId": "1", "attemptId": "10", "label": "attempt 1"},
    ])
    mocker.patch.object(bf, "_keep_last_attempt_per_user", side_effect=lambda x: x)
    open_attempt = mocker.patch.object(bf, "_open_quiz_attempt", return_value=True)
    save = mocker.patch.object(wb, "_save_draft")

    items = [wb.GradeWriteItem("k1", "Jane Doe", 80, 90, 100, "<p>fb</p>")]
    report = wb.push_grades_to_brightspace(
        "https://brightspace.cpcc.edu/d2l/lms/quizzing/x?qi=1&ou=2",
        items, driver=driver, wait=wait, dry_run=True,
    )

    assert report.route == "quiz" and report.dry_run is True
    assert report.matched_count == 1
    assert report.saved_count == 0
    o = report.outcomes[0]
    assert o.matched and o.fields_found and not o.saved
    assert o.score_written == 90.0
    open_attempt.assert_called_once()
    save.assert_not_called()           # dry run must never save


@pytest.mark.unit
def test_push_quiz_grades_reports_unmatched(mocker):
    import cqc_cpcc.utilities.brightspace_fetch as bf
    driver = MagicMock(); wait = MagicMock()
    driver.execute_script.return_value = {"score": True, "feedback": True}
    mocker.patch("cqc_cpcc.utilities.brightspace_submissions.detect_route", return_value="quiz")
    mocker.patch.object(bf, "derive_quiz_grading_url", return_value="https://grid")
    mocker.patch.object(bf, "_open_and_login")
    mocker.patch.object(bf, "_set_max_results_per_page")
    mocker.patch.object(bf, "_gather_quiz_attempts", return_value=[
        {"name": "Nobody Here", "userId": "9", "attemptId": "1", "label": "attempt 1"},
    ])
    mocker.patch.object(bf, "_keep_last_attempt_per_user", side_effect=lambda x: x)
    mocker.patch.object(bf, "_open_quiz_attempt", return_value=True)

    items = [wb.GradeWriteItem("k1", "Jane Doe", 80, 90, 100, "<p>fb</p>")]
    report = wb.push_grades_to_brightspace(
        "https://brightspace.cpcc.edu/d2l/lms/quizzing/x?qi=1&ou=2",
        items, driver=driver, wait=wait, dry_run=True,
    )
    assert report.matched_count == 0
    assert report.unmatched_students == ["Jane Doe"]
    assert report.unmatched_learners == ["Nobody Here"]


@pytest.mark.unit
def test_save_draft_constants_never_target_publish():
    # The publish exclusion list is what keeps drafts from being published.
    assert "publish" in wb.PUBLISH_BUTTON_TEXTS
    assert all("publish" != s for s in wb.SAVE_DRAFT_BUTTON_TEXTS)
