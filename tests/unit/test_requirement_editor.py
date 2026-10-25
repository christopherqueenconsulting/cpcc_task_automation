#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Requirement checklist editor: row checks (pure) and the page (AppTest). Synthetic text only."""

from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from cqc_cpcc import requirement_coverage as rc
from cqc_cpcc.requirement_coverage import requirements_prompt_section
from cqc_streamlit_app import requirement_editor as re_
from cqc_streamlit_app.grade_assignment import _checklist_from_editor

pytestmark = pytest.mark.unit


def _row(id_="R1", text="Reads a price", weight="core"):
    return {"id": id_, "text": text, "weight": weight}


def test_valid_rows_pass_and_blank_id_is_allowed():
    valid, errors = re_.validate_rows([_row(), _row("", "Prints the total", "secondary")])
    assert errors == [] and [v["id"] for v in valid] == ["R1", ""]


def test_missing_weight_defaults_to_core_and_nan_is_blank():
    valid, errors = re_.validate_rows([{"id": float("nan"), "text": "Reads a price", "weight": None}])
    assert errors == [] and valid[0]["weight"] == "core" and valid[0]["id"] == ""


def test_empty_text_is_skipped_with_a_message():
    valid, errors = re_.validate_rows([_row(), _row("R2", "   ")])
    assert len(valid) == 1 and errors[0].row == 2 and not errors[0].quiet
    assert str(errors[0]).startswith("Row 2 (R2): has no requirement text")


def test_a_completely_blank_row_is_a_quiet_note_not_an_error():
    valid, errors = re_.validate_rows([_row(), _row("", "", "core")])
    assert len(valid) == 1 and errors[0].quiet and re_.loud(errors) == []
    assert str(errors[0]) == "Row 2 is empty, so it is skipped. Fill it in or delete it."
    # an id or a different weight with no text is still an error
    assert not re_.validate_rows([_row("", "", "secondary")])[1][0].quiet


def test_errors_use_the_numbers_the_table_shows():
    # row 1 was deleted: the table now shows 2 and 3, and a new row has no number yet
    rows = [_row("R2", "Reads a price"), _row("R2", "Duplicate"), _row("", "", "core"),
            _row("R9", "x" * (re_.MAX_TEXT_CHARS + 1))]
    valid, errors = re_.validate_rows(rows, [2, 3, None, None])
    msgs = [str(e) for e in errors]
    assert [v["row"] for v in valid] == [2]
    assert msgs[0] == "Row 3 (R2): the id R2 is already used by row 2. Give this row a different id, or clear it."
    assert msgs[1] == "A new row is empty, so it is skipped. Fill it in or delete it."
    assert msgs[2].startswith("A new row (R9): the requirement is")
    dup_new = re_.validate_rows([_row("R1"), _row("R1", "Other")], [None, 1])[1][0]
    assert "already used by the new row" in str(dup_new)
    assert re_.row_label({"row": 0, "id": "", "text": "x"}) == "New row (id made when saved)"


def test_row_labels_come_from_the_edited_frames_index():
    from cqc_streamlit_app.grade_assignment import _row_labels
    frame = pd.DataFrame([_row(), _row()], index=[2, float("nan")])
    assert _row_labels(frame) == [2, None]
    assert _row_labels(pd.DataFrame([_row()], index=["x"])) == [None]


def test_whitespace_in_text_is_collapsed_to_one_line():
    valid, _ = re_.validate_rows([_row(text="Reads  a\n price\t now ")])
    assert valid[0]["text"] == "Reads a price now"


def test_row_label_never_holds_text():
    assert re_.row_label({"row": 3, "id": "R3", "text": "![x](http://evil.example/p)"}) == "Row 3 (R3)"
    assert re_.row_label({"row": 4, "id": "", "text": "x"}) == "Row 4 (id made when saved)"
    assert re_.row_label({"row": 5, "id": "![x](y)", "text": "x"}) == "Row 5 (id made when saved)"


def test_invalid_and_duplicate_ids():
    valid, errors = re_.validate_rows([_row("R1"), _row("r1", "Other"), _row("a b", "Third"),
                                       _row("X" * 13, "Fourth")])
    assert [v["row"] for v in valid] == [1]
    msgs = [str(e) for e in errors]
    assert msgs[0] == "Row 2 (R1): the id R1 is already used by row 1. Give this row a different id, or clear it."
    assert msgs[1].startswith("Row 3: the id is not allowed") and "not allowed" in msgs[2]


def test_too_long_and_bad_weight_report_every_problem_on_the_row():
    valid, errors = re_.validate_rows([_row(text="x" * (re_.MAX_TEXT_CHARS + 1), weight="huge")])
    assert valid == [] and {e.field for e in errors} == {"text", "weight"}


def test_count_over_the_cap_skips_the_extra_rows():
    rows = [_row(f"R{i}", f"Does thing {i}") for i in range(1, rc.MAX_REQUIREMENTS + 3)]
    valid, errors = re_.validate_rows(rows)
    assert len(valid) == rc.MAX_REQUIREMENTS and len(errors) == 2
    assert all(e.field == "table" and "at most" in str(e) for e in errors)


def test_render_errors_caps_the_list_and_says_how_many_more():
    def page():
        from cqc_streamlit_app import requirement_editor as r
        bad = [r.RowError(i, "text", "has no requirement text.") for i in range(1, 31)]
        r.render_errors(bad + [r.RowError(31, "blank", "is empty, so it is skipped. Fill it in or delete it.", quiet=True)], 5, 30)

    at = AppTest.from_function(page, default_timeout=30).run()
    assert not at.exception, at.exception
    text = at.error[0].value
    assert text.startswith("Grading with 5 of 30 rows. These rows are left out and will NOT be graded")
    assert text.count("\n- Row ") == 20 and text.endswith("... and 10 more")
    assert at.caption[0].value == "Row 31 is empty, so it is skipped. Fill it in or delete it."


def test_reminder_only_when_there_are_real_errors():
    def page():
        import streamlit as st

        from cqc_streamlit_app import requirement_editor as r
        r.render_reminder()
        r.render_reminder([r.RowError(1, "blank", "is empty, so it is skipped. Fill it in or delete it.", quiet=True)])
        st.session_state["done"] = True

    at = AppTest.from_function(page, default_timeout=30)
    at.session_state["requirement_row_errors"] = [re_.RowError(2, "id", "bad")]
    at.run()
    assert [w.value for w in at.warning] == [
        "Some requirement rows are left out and will not be graded. See the list under the Requirement "
        "checklist table."]
    at.session_state["requirement_row_errors"] = []
    at.run()
    assert not at.warning


def test_row_error_without_a_row_number_prints_plain():
    assert str(re_.RowError(0, "table", "Too many.")) == "Too many."


def test_prompt_line_matches_the_real_prompt():
    item = rc.RequirementItem(id="R7", text="Prints the total", weight="secondary")
    line = re_.prompt_line(item.id, item.weight, item.text)
    assert line in requirements_prompt_section(rc.RequirementChecklist(requirements=[item]))


def test_page_text_cannot_be_mistaken_for_the_cost_line():
    # tests/e2e/test_grade_flow.py finds the cost line with a pattern that needs " on "
    assert " on " not in re_.RULES_CAPTION and " on " not in re_.OFF_MESSAGE


def test_effect_sentences():
    assert "major" in re_.effect_sentence("core") and "major" not in re_.effect_sentence("secondary")


def test_content_has_three_examples_and_no_empty_text():
    assert len(re_.EXAMPLES) == 3 and len(re_.BAD_REQUIREMENTS) >= 3
    assert all(all(part.strip() for part in e) for e in re_.EXAMPLES + re_.BAD_REQUIREMENTS)


def test_checklist_from_editor_skips_bad_rows_and_reports_them():
    base = rc.RequirementChecklist(requirements=[rc.RequirementItem(id="R1", text="Reads a price", weight="core")])
    frame = pd.DataFrame([_row(), _row("R1", "Duplicate id"), _row("", "Prints the total", "secondary")])
    errors = []
    out = _checklist_from_editor(frame, base, errors)
    assert [r.text for r in out.requirements] == ["Reads a price", "Prints the total"]
    assert out.requirements[1].id != "R1" and len(errors) == 1
    assert _checklist_from_editor(pd.DataFrame([_row(text="")]), base) is None


# ---- page ----

EXTRACTED = rc.RequirementChecklist(requirements=[
    rc.RequirementItem(id="R1", text="Reads a price", weight="core"),
    rc.RequirementItem(id="R2", text="Applies a discount " + "very " * 40 + "carefully", weight="secondary")])
TAG = rc.instructions_hash("source-1")[:12]


def _page():
    import streamlit as st

    from cqc_streamlit_app.grade_assignment import render_requirement_checklist
    st.session_state["out"] = render_requirement_checklist("Write OrderTotal.java.", "source-1", None)


def _run(edits=None, at=None, **state):
    at = at or AppTest.from_function(_page, default_timeout=30)
    if edits is not None:
        at.session_state[f"requirements_editor_{TAG}_0"] = edits
    with patch("cqc_streamlit_app.grade_assignment.extract_requirements",
               new=AsyncMock(return_value=EXTRACTED.model_copy(deep=True))):
        at.run()
    assert not at.exception, at.exception
    return at


def test_page_has_how_to_button_and_keeps_old_checklists():
    at = _run()
    assert any(b.label == "How to add your own requirement" for b in at.button)
    assert [r.id for r in at.session_state["out"].requirements] == ["R1", "R2"]


def test_full_text_panel_shows_the_whole_requirement():
    at = _run()
    assert any(c.value.startswith("- **R1** (core): Reads a price") for c in at.code)
    at.selectbox(key=f"requirement_fulltext_{TAG}").select("R2")
    at = _run(at=at)
    assert any(c.value.endswith("carefully") and "(secondary)" in c.value for c in at.code)


def test_row_errors_listed_under_the_table_and_bad_row_not_graded():
    edits = {"edited_rows": {1: {"id": "R1"}}, "added_rows": [{"id": "", "text": "", "weight": "core"}],
             "deleted_rows": []}
    at = _run(edits)
    text = " ".join(e.value for e in at.error)
    assert text.startswith("Grading with 1 of 2 rows. These rows are left out and will NOT be graded "
                           "(row numbers match the # column):")
    assert "Row 2 (R1): the id R1 is already used by row 1" in text
    assert any(c.value == "Row 3 is empty, so it is skipped. Fill it in or delete it." for c in at.caption)  # quiet, not an error
    assert [r.id for r in at.session_state["out"].requirements] == ["R1"]
    assert any("left out and will not be graded" in w.value for w in at.warning)  # the reminder
    assert any(c.value == re_.RULES_CAPTION for c in at.caption)


def test_no_usable_rows_turns_coverage_off_and_says_why():
    edits = {"edited_rows": {0: {"text": ""}, 1: {"text": ""}}, "added_rows": [], "deleted_rows": []}
    at = _run(edits)
    assert at.session_state["out"] is None
    assert any(w.value == re_.OFF_MESSAGE for w in at.warning)
    assert "Requirements will not be checked in this run" in re_.OFF_MESSAGE


def test_how_to_dialog_opens_with_preview():
    at = _run()
    next(b for b in at.button if b.label == "How to add your own requirement").click()
    at = _run(at=at)
    body = " ".join(m.value for m in at.markdown)
    for heading in ("#### Good requirements", "#### Requirements to avoid", "#### How it affects the grade",
                    "#### What the marks mean"):
        assert heading in body
    assert "Re-extract" in body and "Requirements to grade" in body and "what it costs" not in body.lower()
    assert "**met**: fully done and working as required." in body
    assert "**partial**: attempted, but incomplete or not working as required" in body
    assert "**missing**: not done at all. A missing core requirement is a major error" in body
    assert any(c.value == "- **R1** (core): Reads a price" for c in at.code)


def test_preview_follows_the_chosen_row():
    rows = [{"row": 1, "id": "R1", "text": "Reads a price", "weight": "core"},
            {"row": 2, "id": "", "text": "Prints the total", "weight": "secondary"}]

    def page():
        import streamlit as st

        from cqc_streamlit_app import requirement_editor
        requirement_editor._dialog_body(st.session_state["rows"])

    at = AppTest.from_function(page, default_timeout=30)
    at.session_state["rows"] = rows
    at.run()
    assert at.code[0].value == "- **R1** (core): Reads a price"
    at.selectbox(key="requirement_howto_preview").select(1).run()
    assert at.code[0].value == "- **R?** (secondary): Prints the total"
    assert "minor" in at.caption[0].value


def test_hostile_requirement_text_never_reaches_a_label_or_markdown():
    evil = "![x](http://evil.example/p) [a](http://evil.example)"
    rows = [{"row": 1, "id": "R1", "text": evil, "weight": "core"}]

    def page():
        import streamlit as st

        from cqc_streamlit_app import requirement_editor
        requirement_editor._dialog_body(st.session_state["rows"])

    at = AppTest.from_function(page, default_timeout=30)
    at.session_state["rows"] = rows
    at.run()
    assert not at.exception, at.exception
    shown = [str(o) for o in at.selectbox[0].options] + [m.value for m in at.markdown]
    shown += [c.value for c in at.caption]
    assert all("![" not in t and "](" not in t for t in shown)
    assert at.code[0].value == f"- **R1** (core): {evil}"  # the text appears only inside the code block


def test_dialog_with_no_rows_says_so():
    def page():
        from cqc_streamlit_app import requirement_editor
        requirement_editor.show_how_to_dialog([])
    at = AppTest.from_function(page, default_timeout=30).run()
    assert not at.exception, at.exception
    assert any("no usable rows" in c.value for c in at.caption)


def test_render_helpers_return_early_when_empty():
    assert re_.render_full_text([], "t") is None
    re_.render_errors([])
