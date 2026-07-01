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
def test_normalize_name_flips_last_comma_first():
    # BrightSpace "Last, First" must key the same as the grader's "First Last".
    assert wb._normalize_name("Patel, Dharma") == "dharma patel"
    assert wb._normalize_name("Patel, Dharma") == wb._normalize_name("Dharma Patel")
    # A comma that isn't a name separator (missing a side) is left as punctuation.
    assert wb._normalize_name("Doe,") == "doe"


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
    assert "update" in wb.PUBLISH_BUTTON_TEXTS and "retract" in wb.PUBLISH_BUTTON_TEXTS
    assert all("publish" != s for s in wb.SAVE_DRAFT_BUTTON_TEXTS)


# ---------------------------------------------------------------------------
# Pure helpers: _get / _fmt_num / build_feedback_html + name-parser fallback
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_get_reads_dict_and_object_and_missing():
    assert wb._get({"a": 1}, "a") == 1
    assert wb._get(SimpleNamespace(a=2), "a") == 2
    assert wb._get(None, "a") is None
    assert wb._get({"a": 1}, "b") is None


@pytest.mark.unit
def test_fmt_num_handles_ints_floats_and_garbage():
    assert wb._fmt_num(23.0) == "23"
    assert wb._fmt_num(23.5) == "23.5"
    assert wb._fmt_num("n/a") == "n/a"


@pytest.mark.unit
def test_build_feedback_html_empty_returns_empty():
    assert wb.build_feedback_html("", criteria=None) == ""


@pytest.mark.unit
def test_build_write_items_name_parser_exception_falls_back_to_key():
    def boom(_s):
        raise ValueError("bad")
    items = wb.build_write_items_from_results(
        [("weird_key", _result(10, 20))], buffer_pct=0, name_parser=boom)
    assert items[0].display_name == "weird_key"


# ---------------------------------------------------------------------------
# Selenium helpers (mocked driver): locate / save-draft / write-one / assignment
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_locate_write_targets_parses_result_and_handles_error():
    driver = MagicMock()
    driver.execute_script.return_value = {"score": True, "feedback": False}
    assert wb._locate_write_targets(driver) == {"score": True, "feedback": False}
    driver.execute_script.side_effect = RuntimeError("boom")
    assert wb._locate_write_targets(driver) == {"score": False, "feedback": False}


@pytest.mark.unit
def test_save_draft_returns_bool_and_swallows_errors():
    driver = MagicMock()
    driver.execute_script.return_value = True
    assert wb._save_draft(driver) is True
    driver.execute_script.return_value = False
    assert wb._save_draft(driver) is False
    driver.execute_script.side_effect = RuntimeError("x")
    assert wb._save_draft(driver) is False


@pytest.mark.unit
def test_write_one_student_dry_run_does_not_fill_or_save(mocker):
    driver = MagicMock()
    mocker.patch.object(wb, "_locate_write_targets", return_value={"score": True, "feedback": True})
    save = mocker.patch.object(wb, "_save_draft")
    o = wb.StudentWriteOutcome(student_key="k", display_name="Jane", matched=True)
    item = wb.GradeWriteItem("k", "Jane", 80, 90, 100, "<p>fb</p>")
    wb._write_one_student(driver, MagicMock(), item, o, lambda *_: None, dry_run=True)
    assert o.fields_found and o.score_written == 90.0 and not o.saved
    save.assert_not_called()
    driver.execute_script.assert_not_called()   # dry run fills nothing


@pytest.mark.unit
def test_write_one_student_real_fills_and_saves_draft(mocker):
    driver = MagicMock()
    driver.execute_script.return_value = {"score": True, "feedback": True}
    mocker.patch.object(wb, "_locate_write_targets", return_value={"score": True, "feedback": True})
    save = mocker.patch.object(wb, "_save_draft", return_value=True)
    o = wb.StudentWriteOutcome(student_key="k", display_name="Jane", matched=True)
    item = wb.GradeWriteItem("k", "Jane", 80, 90, 100, "<p>fb</p>")
    wb._write_one_student(driver, MagicMock(), item, o, lambda *_: None, dry_run=False)
    assert o.score_written == 90.0 and o.saved and o.note == "saved as draft"
    save.assert_called_once()


@pytest.mark.unit
def test_write_one_student_no_score_field_reports_not_found(mocker):
    driver = MagicMock()
    mocker.patch.object(wb, "_locate_write_targets", return_value={"score": False, "feedback": False})
    save = mocker.patch.object(wb, "_save_draft")
    o = wb.StudentWriteOutcome(student_key="k", display_name="Jane", matched=True)
    item = wb.GradeWriteItem("k", "Jane", 80, 90, 100, "<p>fb</p>")
    wb._write_one_student(driver, MagicMock(), item, o, lambda *_: None, dry_run=False)
    assert not o.fields_found and not o.saved
    assert "not found" in o.note
    save.assert_not_called()


@pytest.mark.unit
def test_write_one_student_filled_but_no_save_button(mocker):
    driver = MagicMock()
    driver.execute_script.return_value = {"score": True, "feedback": True}
    mocker.patch.object(wb, "_locate_write_targets", return_value={"score": True, "feedback": True})
    mocker.patch.object(wb, "_save_draft", return_value=False)
    o = wb.StudentWriteOutcome(student_key="k", display_name="Jane", matched=True)
    item = wb.GradeWriteItem("k", "Jane", 80, 90, 100, "<p>fb</p>")
    wb._write_one_student(driver, MagicMock(), item, o, lambda *_: None, dry_run=False)
    assert not o.saved and "NOT saved" in o.note


@pytest.mark.unit
def test_gather_assignment_learners_filters_and_handles_error():
    driver = MagicMock()
    driver.execute_script.return_value = [
        {"name": "Jane Doe", "userId": "1"},
        {"name": "", "userId": "2"},       # no name -> dropped
        {"name": "No Id"},                 # no userId -> dropped
    ]
    rows = wb._gather_assignment_learners(driver)
    assert [r["name"] for r in rows] == ["Jane Doe"]
    driver.execute_script.side_effect = RuntimeError("boom")
    assert wb._gather_assignment_learners(driver) == []


@pytest.mark.unit
def test_open_assignment_evaluation_clicks_name_link_or_skips(mocker):
    mocker.patch("cqc_cpcc.utilities.selenium_util.wait_for_ajax", create=True)
    driver = MagicMock()
    wait = MagicMock()
    url = "https://bs/d2l/lms/dropbox/admin/mark/folder_submissions_users.d2l?db=1&ou=2"

    ok = wb._open_assignment_evaluation(driver, wait, url, {"name": "Jane Doe", "userId": "117059"})
    assert ok is True
    driver.get.assert_called_once_with(url)
    # located the name link by its feedback,<userId> onclick, then clicked it
    xpath = driver.find_element.call_args[0][1]
    assert "feedback,117059" in xpath and "EvaluateDropboxSubmission" in xpath
    driver.find_element.return_value.click.assert_called_once()

    # no userId -> skip without navigating
    driver.reset_mock()
    assert wb._open_assignment_evaluation(driver, wait, url, {"name": "x"}) is False
    driver.get.assert_not_called()


@pytest.mark.unit
def test_push_assignment_grades_dry_run_matches_and_reports(mocker):
    import cqc_cpcc.utilities.brightspace_fetch as bf
    driver = MagicMock(); wait = MagicMock()
    driver.execute_script.return_value = {"score": True, "feedback": True}
    mocker.patch("cqc_cpcc.utilities.brightspace_submissions.detect_route",
                 return_value="assignment")
    mocker.patch.object(bf, "_open_and_login")
    mocker.patch.object(bf, "_set_max_results_per_page")
    mocker.patch.object(wb, "_gather_assignment_learners", return_value=[
        {"name": "Jane Doe", "userId": "1"},
    ])
    mocker.patch.object(wb, "_open_assignment_evaluation", return_value=True)
    save = mocker.patch.object(wb, "_save_draft")

    items = [wb.GradeWriteItem("k1", "Jane Doe", 80, 90, 100, "<p>fb</p>")]
    report = wb.push_grades_to_brightspace(
        "https://brightspace.cpcc.edu/d2l/lms/dropbox/admin/mark/x?ou=1",
        items, driver=driver, wait=wait, dry_run=True,
    )
    assert report.route == "assignment" and report.matched_count == 1
    assert report.saved_count == 0
    save.assert_not_called()
