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


@pytest.mark.unit
def test_build_feedback_html_includes_errors_observed_grouped_by_severity():
    errors = [
        SimpleNamespace(name="Null <deref>", description="Missing check",
                        severity="Major", notes="Line 12"),
        SimpleNamespace(name="Style", description="Naming", severity="minor", notes=""),
    ]
    html = wb.build_feedback_html("Summary.", criteria=None, errors=errors)
    assert "<strong>Errors Observed:</strong>" in html
    assert "<em>Major Issues:</em>" in html
    assert "<em>Minor Issues:</em>" in html
    assert "Null &lt;deref&gt;" in html          # escaped error name
    assert "Missing check" in html
    assert "Line 12" in html                       # notes rendered


@pytest.mark.unit
def test_build_feedback_html_no_errors_section_when_absent():
    html = wb.build_feedback_html("Summary.", criteria=None, errors=[])
    assert "Errors Observed" not in html
    assert html == "<p>Summary.</p>"


@pytest.mark.unit
def test_build_write_items_carries_detected_errors_into_feedback():
    err = SimpleNamespace(name="Bug", description="Broke", severity="major", notes="")
    result = SimpleNamespace(
        total_points_earned=8, total_points_possible=10, overall_feedback="Good.",
        criteria_results=[], overall_band_label=None, detected_errors=[err],
    )
    items = wb.build_write_items_from_results(
        [("123 - Jane Doe - 2026", result)], buffer_pct=0,
    )
    assert "Errors Observed" in items[0].feedback_html
    assert "Bug" in items[0].feedback_html


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
        ("10001-500001 - Ada Example - Oct 10, 2025 234 PM", _result(80, 100)),
    ]
    items = wb.build_write_items_from_results(
        results, buffer_pct=10, include_criteria_feedback=False,
        name_parser=lambda s: "Ada Example",
    )
    assert len(items) == 1
    it = items[0]
    assert it.display_name == "Ada Example"
    assert it.raw_score == 80.0
    assert it.score == 90.0           # buffered
    assert it.max_points == 100.0
    # Overall feedback + band, but no per-criterion list (include_criteria_feedback=False).
    assert it.feedback_html == "<p>ok</p>\n<p><strong>Overall:</strong> Proficient</p>"


@pytest.mark.unit
def test_build_write_items_uses_default_name_parser():
    # Real parser turns "Id - Name - Date" into the clean name.
    results = [("10001-500001 - Jane Doe - Oct 1, 2025 100 PM", _result(50, 100))]
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
    mocker.patch.object(wb, "_write_feedback_via_editor", return_value=True)
    save = mocker.patch.object(wb, "_save_draft", return_value=True)
    o = wb.StudentWriteOutcome(student_key="k", display_name="Jane", matched=True)
    item = wb.GradeWriteItem("k", "Jane", 80, 90, 100, "<p>fb</p>")
    wb._write_one_student(driver, MagicMock(), item, o, lambda *_: None, dry_run=False)
    assert o.score_written == 90.0 and o.saved and o.note == "saved as draft"
    assert o.feedback_written is True
    save.assert_called_once()


@pytest.mark.unit
def test_write_feedback_via_editor_empty_returns_false():
    driver = MagicMock()
    assert wb._write_feedback_via_editor(driver, MagicMock(), "") is False
    driver.execute_script.assert_not_called()


@pytest.mark.unit
def test_write_feedback_via_editor_types_into_iframe():
    driver = MagicMock()
    # schedule, poll, setContent, scroll-into-view, find-iframe, focus
    driver.execute_script.side_effect = [True, True, True, True, "IFRAME_EL", None]
    ok = wb._write_feedback_via_editor(driver, MagicMock(), "<p>fb</p>")
    assert ok is True
    driver.switch_to.frame.assert_called_once_with("IFRAME_EL")
    assert driver.find_element.return_value.send_keys.called   # real keystrokes typed
    driver.switch_to.default_content.assert_called()           # frame restored


@pytest.mark.unit
def test_write_feedback_via_editor_no_iframe_returns_false():
    driver = MagicMock()
    # schedule, poll, setContent, scroll, find-iframe -> None
    driver.execute_script.side_effect = [True, True, True, True, None]
    assert wb._write_feedback_via_editor(driver, MagicMock(), "<p>fb</p>") is False
    driver.switch_to.frame.assert_not_called()


@pytest.mark.unit
def test_write_one_student_no_score_field_reports_not_found(mocker):
    driver = MagicMock()
    # Patch the hydration wait directly so the test doesn't sit through the poll timeout.
    mocker.patch.object(wb, "_wait_for_write_targets", return_value={"score": False, "feedback": False})
    save = mocker.patch.object(wb, "_save_draft")
    o = wb.StudentWriteOutcome(student_key="k", display_name="Jane", matched=True)
    item = wb.GradeWriteItem("k", "Jane", 80, 90, 100, "<p>fb</p>")
    wb._write_one_student(driver, MagicMock(), item, o, lambda *_: None, dry_run=False)
    assert not o.fields_found and not o.saved
    assert "not found" in o.note
    save.assert_not_called()


@pytest.mark.unit
def test_build_write_items_populates_rubric_selections():
    crit1 = SimpleNamespace(criterion_name="Program Performance", points_earned=24,
                            points_possible=30, selected_level_label="Above Average", feedback="")
    crit2 = SimpleNamespace(criterion_name="Style", points_earned=5, points_possible=5,
                            selected_level_label=None, feedback="")  # no level -> skipped
    results = [("id - Jane Doe - Oct 1, 2025 100 PM", _result(29, 35, criteria=[crit1, crit2]))]
    items = wb.build_write_items_from_results(results, buffer_pct=0)
    sels = items[0].rubric_selections
    assert len(sels) == 1
    assert sels[0].criterion_name == "Program Performance"
    assert sels[0].level_label == "Above Average"


@pytest.mark.unit
def test_select_rubric_levels_empty_skips_script():
    driver = MagicMock()
    assert wb._select_rubric_levels(driver, [], dry_run=True) == {"selected": [], "missing": []}
    driver.execute_script.assert_not_called()


@pytest.mark.unit
def test_select_rubric_levels_passes_payload_and_dryrun_flag():
    driver = MagicMock()
    driver.execute_script.return_value = {"selected": [{"criterion": "C", "level": "L"}], "missing": []}
    out = wb._select_rubric_levels(driver, [wb.RubricLevelSelection("C", "L")], dry_run=True)
    args = driver.execute_script.call_args[0]
    assert args[1] == [{"criterion": "C", "level": "L"}]   # payload
    assert args[2] is True                                  # dry_run flag
    assert out["selected"][0]["criterion"] == "C"


@pytest.mark.unit
def test_write_one_student_selects_rubric_before_fill_and_saves(mocker):
    driver = MagicMock()
    driver.execute_script.return_value = {"score": True, "feedback": True}
    mocker.patch.object(wb, "_locate_write_targets", return_value={"score": True, "feedback": True})
    rub = mocker.patch.object(wb, "_select_rubric_levels", return_value={
        "selected": [{"criterion": "Program Performance", "level": "Above Average"}], "missing": []})
    mocker.patch.object(wb, "_save_draft", return_value=True)
    o = wb.StudentWriteOutcome(student_key="k", display_name="Jane", matched=True)
    item = wb.GradeWriteItem("k", "Jane", 24, 27, 30, "<p>fb</p>",
                             rubric_selections=[wb.RubricLevelSelection("Program Performance", "Above Average")])
    wb._write_one_student(driver, MagicMock(), item, o, lambda *_: None, dry_run=False)
    rub.assert_called_once()
    assert rub.call_args.kwargs.get("dry_run") is False    # real selection, not dry-run
    assert o.rubric_selected == 1 and o.saved and o.score_written == 27.0


@pytest.mark.unit
def test_write_one_student_dry_run_reports_rubric_matches_without_saving(mocker):
    driver = MagicMock()
    mocker.patch.object(wb, "_locate_write_targets", return_value={"score": True, "feedback": True})
    rub = mocker.patch.object(wb, "_select_rubric_levels", return_value={
        "selected": [{"criterion": "Program Performance", "level": "Above Average"}],
        "missing": [{"criterion": "Style", "level": "Full", "reason": "level not found"}]})
    save = mocker.patch.object(wb, "_save_draft")
    o = wb.StudentWriteOutcome(student_key="k", display_name="Jane", matched=True)
    item = wb.GradeWriteItem("k", "Jane", 24, 27, 30, "<p>fb</p>", rubric_selections=[
        wb.RubricLevelSelection("Program Performance", "Above Average"),
        wb.RubricLevelSelection("Style", "Full")])
    wb._write_one_student(driver, MagicMock(), item, o, lambda *_: None, dry_run=True)
    rub.assert_called_once()
    assert rub.call_args.kwargs.get("dry_run") is True     # matched only, not clicked
    assert o.rubric_selected == 1 and len(o.rubric_missing) == 1 and not o.saved
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
def test_write_one_quiz_student_dry_run_reports_would_post(mocker):
    """Quiz dry-run: fields hydrate, Completion Summary feedback editor reachable."""
    driver = MagicMock()
    mocker.patch.object(wb, "_wait_for_write_targets", return_value={"score": True, "feedback": True})
    switch = mocker.patch.object(wb, "_switch_quiz_view", return_value=True)
    mocker.patch.object(wb, "_locate_feedback_editor", return_value=True)
    save = mocker.patch.object(wb, "_click_commit")
    o = wb.StudentWriteOutcome(student_key="k", display_name="Jane", matched=True)
    item = wb.GradeWriteItem("k", "Jane", 80, 90, 100, "<p>fb</p>")

    wb._write_one_quiz_student(driver, MagicMock(), item, o, lambda *_: None, dry_run=True)

    assert o.fields_found and o.score_written == 90 and not o.saved
    assert "would POST" in o.note and "overall feedback" in o.note
    save.assert_not_called()                 # dry run must never post
    # switched to completion summary to check feedback, then back to attempt
    assert switch.call_count == 2


@pytest.mark.unit
def test_write_one_quiz_student_dry_run_flags_missing_feedback_editor(mocker):
    driver = MagicMock()
    mocker.patch.object(wb, "_wait_for_write_targets", return_value={"score": True, "feedback": False})
    mocker.patch.object(wb, "_switch_quiz_view", return_value=True)
    mocker.patch.object(wb, "_locate_feedback_editor", return_value=False)
    o = wb.StudentWriteOutcome(student_key="k", display_name="Jane", matched=True)
    item = wb.GradeWriteItem("k", "Jane", 80, 90, 100, "<p>fb</p>")
    # No doc path + default attach mode -> inline branch (checks the feedback editor).
    wb._write_one_quiz_student(driver, MagicMock(), item, o, lambda *_: None,
                               dry_run=True, feedback_mode="inline")
    assert o.fields_found and "overall feedback target NOT found" in o.note


@pytest.mark.unit
def test_write_one_quiz_student_no_score_field(mocker):
    driver = MagicMock()
    mocker.patch.object(wb, "_wait_for_write_targets", return_value={"score": False, "feedback": False})
    o = wb.StudentWriteOutcome(student_key="k", display_name="Jane", matched=True)
    item = wb.GradeWriteItem("k", "Jane", 80, 90, 100, "<p>fb</p>")
    wb._write_one_quiz_student(driver, MagicMock(), item, o, lambda *_: None, dry_run=True)
    assert not o.fields_found and "score field not found" in o.note


@pytest.mark.unit
def test_write_one_quiz_student_real_posts_score_and_feedback(mocker):
    """Real quiz write: fill score, post, switch to Completion Summary, feedback, save."""
    driver = MagicMock()
    mocker.patch.object(wb, "_wait_for_write_targets", return_value={"score": True, "feedback": True})
    fill = mocker.patch.object(wb, "_fill_score", return_value=True)
    confirm = mocker.patch.object(wb, "_confirm_dialog", return_value=True)
    switch = mocker.patch.object(wb, "_switch_quiz_view", return_value=True)
    fb = mocker.patch.object(wb, "_write_feedback_via_editor", return_value=True)
    commit = mocker.patch.object(wb, "_click_commit", return_value=True)
    mocker.patch("time.sleep")  # skip the inter-step sleeps
    o = wb.StudentWriteOutcome(student_key="k", display_name="Jane", matched=True)
    item = wb.GradeWriteItem("k", "Jane", 80, 90, 100, "<p>fb</p>")

    wb._write_one_quiz_student(driver, MagicMock(), item, o, lambda *_: None, dry_run=False)

    assert o.score_written == 90
    assert o.feedback_written is True
    assert o.saved is True and "posted score" in o.note
    fill.assert_called_once_with(driver, 90)         # keystroke score fill
    fb.assert_called_once()
    switch.assert_called_once_with(driver, "completion summary")
    assert commit.call_count == 2            # post score, then save feedback
    assert confirm.called                    # the score-sum warning is confirmed


@pytest.mark.unit
def test_write_one_quiz_student_attach_mode_uploads_doc_not_inline(mocker, tmp_path):
    """Attach mode uploads the .docx (not inline HTML) and marks feedback_attached."""
    doc = tmp_path / "Jane_Feedback.docx"
    doc.write_bytes(b"PK\x03\x04docx")
    driver = MagicMock()
    mocker.patch.object(wb, "_wait_for_write_targets", return_value={"score": True, "feedback": True})
    mocker.patch.object(wb, "_fill_score", return_value=True)
    mocker.patch.object(wb, "_confirm_dialog", return_value=True)
    mocker.patch.object(wb, "_switch_quiz_view", return_value=True)
    mocker.patch.object(wb, "_click_commit", return_value=True)
    inline = mocker.patch.object(wb, "_write_feedback_via_editor", return_value=True)
    attach = mocker.patch.object(wb, "_attach_feedback_file", return_value=True)
    mocker.patch("time.sleep")
    o = wb.StudentWriteOutcome(student_key="k", display_name="Jane", matched=True)
    item = wb.GradeWriteItem("k", "Jane", 80, 90, 100, "<p>fb</p>",
                             feedback_doc_path=str(doc))

    wb._write_one_quiz_student(driver, MagicMock(), item, o, lambda *_: None,
                               dry_run=False, feedback_mode="attach")

    attach.assert_called_once_with(driver, str(doc), mocker.ANY)
    inline.assert_not_called()
    assert o.feedback_attached is True and o.feedback_written is False
    assert o.saved is True and "feedback doc" in o.note


@pytest.mark.unit
def test_write_one_quiz_student_attach_mode_falls_back_to_inline_without_doc(mocker):
    """Attach mode with no doc path falls back to writing inline feedback."""
    driver = MagicMock()
    mocker.patch.object(wb, "_wait_for_write_targets", return_value={"score": True, "feedback": True})
    mocker.patch.object(wb, "_fill_score", return_value=True)
    mocker.patch.object(wb, "_confirm_dialog", return_value=True)
    mocker.patch.object(wb, "_switch_quiz_view", return_value=True)
    mocker.patch.object(wb, "_click_commit", return_value=True)
    inline = mocker.patch.object(wb, "_write_feedback_via_editor", return_value=True)
    attach = mocker.patch.object(wb, "_attach_feedback_file", return_value=True)
    mocker.patch("time.sleep")
    o = wb.StudentWriteOutcome(student_key="k", display_name="Jane", matched=True)
    item = wb.GradeWriteItem("k", "Jane", 80, 90, 100, "<p>fb</p>")  # no doc path

    wb._write_one_quiz_student(driver, MagicMock(), item, o, lambda *_: None,
                               dry_run=False, feedback_mode="attach")

    attach.assert_not_called()
    inline.assert_called_once()
    assert o.feedback_written is True and o.feedback_attached is False


@pytest.mark.unit
def test_write_one_student_attach_mode_uploads_doc(mocker, tmp_path):
    """Assignment attach mode uploads the .docx instead of typing inline feedback."""
    doc = tmp_path / "Jane_Feedback.docx"
    doc.write_bytes(b"PK\x03\x04docx")
    driver = MagicMock()
    driver.execute_script.return_value = {"score": True}
    mocker.patch.object(wb, "_wait_for_write_targets", return_value={"score": True, "feedback": True})
    mocker.patch.object(wb, "_select_rubric_levels", return_value={"selected": [], "missing": []})
    mocker.patch.object(wb, "_save_draft", return_value=True)
    inline = mocker.patch.object(wb, "_write_feedback_via_editor", return_value=True)
    attach = mocker.patch.object(wb, "_attach_feedback_file", return_value=True)
    o = wb.StudentWriteOutcome(student_key="k", display_name="Jane", matched=True)
    item = wb.GradeWriteItem("k", "Jane", 80, 90, 100, "<p>fb</p>",
                             feedback_doc_path=str(doc))

    wb._write_one_student(driver, MagicMock(), item, o, lambda *_: None,
                          dry_run=False, feedback_mode="attach")

    attach.assert_called_once_with(driver, str(doc), mocker.ANY)
    inline.assert_not_called()
    assert o.feedback_attached is True and o.saved is True


@pytest.mark.unit
def test_attach_feedback_file_missing_path_returns_false():
    assert wb._attach_feedback_file(MagicMock(), "/no/such/file.docx") is False


@pytest.mark.unit
def test_attach_feedback_file_happy_path(mocker, tmp_path):
    """Full flow: Attach -> File Upload -> My Computer -> Upload -> send_keys -> Add."""
    from selenium.webdriver.common.by import By
    doc = tmp_path / "Feedback.docx"
    doc.write_bytes(b"x")
    input_el = MagicMock()
    upload_btn = MagicMock()
    iframe = MagicMock()
    iframe.get_attribute.return_value = "Add a File"
    driver = MagicMock()
    # All the JS steps (clicks, file-listed, attachment-present) succeed.
    driver.execute_script.return_value = True

    def find_elements(by, value):
        if by == By.TAG_NAME and value == "iframe":
            return [iframe]
        if "d2l-fileinput-addbuttons" in value:
            return [upload_btn]
        if "input[type=file]" in value:
            return [input_el]
        return []
    driver.find_elements.side_effect = find_elements
    mocker.patch("time.sleep")

    assert wb._attach_feedback_file(driver, str(doc)) is True
    upload_btn.click.assert_called_once()                 # REAL click = trusted gesture
    input_el.send_keys.assert_called_once_with(str(doc))  # absolute path sent
    driver.switch_to.default_content.assert_called()      # frame restored


@pytest.mark.unit
def test_build_feedback_docs_zip_uses_id_bearing_folders(tmp_path):
    """Each doc is placed under its ID-bearing student_key folder; missing paths skipped."""
    import zipfile
    a = tmp_path / "a.docx"; a.write_bytes(b"x")
    b = tmp_path / "b.docx"; b.write_bytes(b"y")
    out = tmp_path / "fb.zip"
    z = wb.build_feedback_docs_zip(
        {
            "100003-600002 - Ben Sample - Jul 7, 2026": str(a),
            "99-1 - Jane Doe": str(b),
            "no-doc": None,                       # skipped (falsy path)
            "gone": str(tmp_path / "missing.docx"),  # skipped (not on disk)
        },
        out_path=str(out),
    )
    assert z == str(out)
    with zipfile.ZipFile(z) as zf:
        assert sorted(zf.namelist()) == [
            "100003-600002 - Ben Sample - Jul 7, 2026/a.docx",
            "99-1 - Jane Doe/b.docx",
        ]


@pytest.mark.unit
def test_build_feedback_docs_zip_returns_none_when_no_docs(tmp_path):
    assert wb.build_feedback_docs_zip({}) is None
    assert wb.build_feedback_docs_zip({"k": None}) is None
    assert wb.build_feedback_docs_zip({"k": str(tmp_path / "nope.docx")}) is None


@pytest.mark.unit
def test_import_feedback_zip_happy_path(mocker, tmp_path):
    """Add Feedback Files -> active iframe -> REAL Upload click -> send_keys -> Add."""
    from selenium.webdriver.common.by import By
    z = tmp_path / "fb.zip"; z.write_bytes(b"PK\x03\x04")
    input_el = MagicMock()
    upload_btn = MagicMock()
    iframe = MagicMock()
    iframe.get_attribute.return_value = "Add Feedback Files"
    driver = MagicMock()
    driver.execute_script.return_value = True   # button click, file-listed, Add all succeed

    def find_elements(by, value):
        if by == By.TAG_NAME and value == "iframe":
            return [iframe]
        if "d2l-fileinput-addbuttons" in value:
            return [upload_btn]
        if "input[type=file]" in value:
            return [input_el]
        return []
    driver.find_elements.side_effect = find_elements
    mocker.patch("time.sleep")

    assert wb.import_feedback_zip(driver, str(z)) is True
    upload_btn.click.assert_called_once()                # trusted gesture creates the input
    input_el.send_keys.assert_called_once_with(str(z))   # absolute ZIP path sent
    driver.switch_to.default_content.assert_called()     # frame restored


@pytest.mark.unit
def test_import_feedback_zip_missing_button_returns_false(mocker, tmp_path):
    z = tmp_path / "fb.zip"; z.write_bytes(b"PK")
    driver = MagicMock()
    driver.execute_script.return_value = False   # 'Add Feedback Files' button not found
    mocker.patch("time.sleep")
    assert wb.import_feedback_zip(driver, str(z)) is False
    assert wb.import_feedback_zip(driver, "/no/such/file.zip") is False


@pytest.mark.unit
def test_import_assignment_feedback_docs_marks_outcomes(mocker, tmp_path):
    """Live import: submitters with a doc are flagged attached+saved; others noted."""
    doc = tmp_path / "Donovan_Feedback.docx"; doc.write_bytes(b"x")
    imp = mocker.patch.object(wb, "import_feedback_zip", return_value=True)
    items = [
        wb.GradeWriteItem("100003-600002 - Ben Sample", "Ben Sample",
                          80, 90, 100, "<p>fb</p>", feedback_doc_path=str(doc)),
        wb.GradeWriteItem("no-sub - Carol", "Carol", 0, 0, 100, "<p>fb</p>"),
    ]
    report = wb.GradeWriteReport(route="assignment", dry_run=False)
    out = wb._import_assignment_feedback_docs(
        MagicMock(), "url", items, [items[0]], report, lambda *_: None, dry_run=False)

    imp.assert_called_once()
    by_name = {o.display_name: o for o in out.outcomes}
    assert by_name["Ben Sample"].feedback_attached is True
    assert by_name["Ben Sample"].saved is True
    assert by_name["Carol"].feedback_attached is False
    assert "no feedback doc" in by_name["Carol"].note


@pytest.mark.unit
def test_import_assignment_feedback_docs_dry_run_does_not_upload(mocker, tmp_path):
    doc = tmp_path / "f.docx"; doc.write_bytes(b"x")
    imp = mocker.patch.object(wb, "import_feedback_zip", return_value=True)
    item = wb.GradeWriteItem("1-2 - Jane", "Jane", 80, 90, 100, "<p>fb</p>",
                             feedback_doc_path=str(doc))
    report = wb.GradeWriteReport(route="assignment", dry_run=True)
    out = wb._import_assignment_feedback_docs(
        MagicMock(), "url", [item], [item], report, lambda *_: None, dry_run=True)
    imp.assert_not_called()
    assert out.outcomes[0].feedback_attached is False
    assert "dry run" in out.outcomes[0].note


@pytest.mark.unit
def test_push_assignment_grades_attach_mode_uses_bulk_import(mocker, tmp_path):
    """Attach mode on the assignment route bulk-imports instead of per-student nav."""
    doc = tmp_path / "f.docx"; doc.write_bytes(b"x")
    mocker.patch("cqc_cpcc.utilities.brightspace_fetch._open_and_login")
    bulk = mocker.patch.object(wb, "_import_assignment_feedback_docs",
                               return_value=wb.GradeWriteReport(route="assignment", dry_run=False))
    gather = mocker.patch.object(wb, "_gather_assignment_learners", return_value=[])
    item = wb.GradeWriteItem("1-2 - Jane", "Jane", 80, 90, 100, "<p>fb</p>",
                             feedback_doc_path=str(doc))
    wb._push_assignment_grades(MagicMock(), MagicMock(), "url", [item], lambda *_: None,
                               None, dry_run=False, feedback_mode="attach")
    bulk.assert_called_once()
    gather.assert_not_called()   # no per-student navigation in bulk attach mode


@pytest.mark.unit
def test_confirm_dialog_excludes_destructive_by_default():
    """SAFETY: the discard/reset-auto-evaluation dialog must be in the exclude list."""
    driver = MagicMock()
    driver.execute_script.return_value = True
    assert wb._confirm_dialog(driver, ("continue anyway",)) is True
    include_arg, exclude_arg = driver.execute_script.call_args[0][1], driver.execute_script.call_args[0][2]
    assert "continue anyway" in include_arg
    # Never confirm the destructive dialog.
    assert "discard" in exclude_arg and "auto-evaluation" in exclude_arg and "reset to" in exclude_arg


@pytest.mark.unit
def test_fill_score_uses_real_keystrokes():
    driver = MagicMock()
    el = MagicMock()
    driver.execute_script.return_value = el   # _FIND_SCORE_INPUT_JS returns the input
    assert wb._fill_score(driver, 42) is True
    assert el.send_keys.called                # typed via real keystrokes, not a JS value set
    # No input element -> False.
    driver.execute_script.return_value = None
    assert wb._fill_score(driver, 42) is False


@pytest.mark.unit
def test_is_quiz_writeback_url():
    from cqc_streamlit_app.utils import _is_quiz_writeback_url
    assert _is_quiz_writeback_url(
        "https://brightspace.cpcc.edu/d2l/lms/quizzing/admin/mark/quiz_mark_users.d2l?qi=1&ou=2"
    )
    assert not _is_quiz_writeback_url(
        "https://brightspace.cpcc.edu/d2l/lms/dropbox/admin/folders_manage.d2l?ou=2"
    )
    assert not _is_quiz_writeback_url("")


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
