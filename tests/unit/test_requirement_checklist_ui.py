#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""AppTest: the requirement checklist under the solution uploader (PR 9).

The model is replaced everywhere; the page itself runs for real. Synthetic text only.
"""

from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest
from streamlit.testing.v1 import AppTest

from cqc_cpcc import requirement_coverage as rc
from cqc_cpcc import requirement_solution_check as rsc

pytestmark = pytest.mark.unit

INSTRUCTIONS = "Write OrderTotal.java that reads a price, applies a discount and prints the total."
SOLUTION = "public class OrderTotal { /* reads and prints, no discount */ }"
EXTRACTED = rc.RequirementChecklist(requirements=[
    rc.RequirementItem(id="R1", text="Reads a price", weight="core"),
    rc.RequirementItem(id="R2", text="Applies a discount", weight="core"),
    rc.RequirementItem(id="R3", text="Prints the total", weight="secondary")])


def _page():
    import streamlit as st

    from cqc_streamlit_app.grade_assignment import render_requirement_checklist
    st.session_state["out"] = render_requirement_checklist(
        "Write OrderTotal.java that reads a price, applies a discount and prints the total.",
        "source-1", st.session_state.get("solution_text"))


def _verdicts(**statuses):
    return rsc.SolutionCheck(verdicts=[
        rsc.SolutionVerdict(requirement_id=k.upper(), does_it=v, evidence=f"evidence {k}")
        for k, v in statuses.items()])


class Page:
    """An AppTest with the model and the policy replaced."""

    def __init__(self, *, check=None, calibrated=False):
        self.check = check or AsyncMock(return_value=_verdicts(r1="yes", r2="no", r3="yes"))
        self.calibrated = calibrated
        self.at = AppTest.from_function(_page, default_timeout=30)
        rsc.clear_cache()

    def run(self):
        with ExitStack() as stack:
            stack.enter_context(patch("cqc_streamlit_app.grade_assignment.extract_requirements",
                                      new=AsyncMock(return_value=EXTRACTED.model_copy(deep=True))))
            stack.enter_context(patch.object(rsc, "check_requirements_against_solution", new=self.check))
            stack.enter_context(patch.object(rsc, "policy_floor_met", return_value=self.calibrated))
            self.at.run()
        assert not self.at.exception, self.at.exception
        return self

    def click(self, label, editor_state=None):
        """Click a button. AppTest keeps no state for data_editor, so a test that edited the table
        passes the same edits again for the run the click starts."""
        button = next(b for b in self.at.button if b.label == label)
        for key, value in (editor_state or {}).items():
            self.at.session_state[key] = value
        with ExitStack() as stack:
            stack.enter_context(patch.object(rsc, "check_requirements_against_solution", new=self.check))
            stack.enter_context(patch.object(rsc, "policy_floor_met", return_value=self.calibrated))
            button.click().run()
        assert not self.at.exception, self.at.exception
        return self

    @property
    def out(self):
        return self.at.session_state["out"]

    @property
    def labels(self):
        return [b.label for b in self.at.button]

    @property
    def captions(self):
        return [c.value for c in self.at.caption]


def test_without_a_solution_the_check_button_is_off_and_says_why():
    page = Page().run()
    button = next(b for b in page.at.button if b.label == "Check against the solution")
    assert button.disabled
    assert any("off because there is no solution yet" in c for c in page.captions)
    page.check.assert_not_awaited()
    assert [r.graded for r in page.out.requirements] == [True, True, True]


def test_no_widget_has_an_empty_label():
    page = Page()
    page.at.session_state["solution_text"] = SOLUTION
    page.run()
    assert all(b.label.strip() for b in page.at.button)
    assert all(t.label.strip() for t in page.at.toggle)


def test_a_solution_runs_the_check_once_and_a_no_is_only_a_suggestion_before_calibration():
    page = Page()
    page.at.session_state["solution_text"] = SOLUTION
    page.run()
    assert page.check.await_count == 1
    assert [r.solution_status for r in page.out.requirements] == ["yes", "no", "yes"]
    assert [r.graded for r in page.out.requirements] == [True, True, True]
    assert any("R2" in i.value and "does not seem to do" in i.value for i in page.at.info)
    assert any("only suggests" in c for c in page.captions)
    assert "Don't grade R2" in page.labels

    page.click("Don't grade R2")
    assert [r.graded for r in page.out.requirements] == [True, False, True]
    assert page.check.await_count == 1  # applying a suggestion is not a new call
    assert "Don't grade R2" not in page.labels
    assert any(c.startswith("Not graded") and "R2 Applies a discount" in c for c in page.captions)


def test_after_calibration_a_no_is_applied_without_a_click():
    page = Page(calibrated=True)
    page.at.session_state["solution_text"] = SOLUTION
    page.run()
    assert [r.graded for r in page.out.requirements] == [True, False, True]
    assert any(c.startswith("Not graded") for c in page.captions)  # visible, not collapsed
    assert not any("only suggests" in c for c in page.captions)


def test_an_unclear_answer_stays_graded_and_is_explained():
    page = Page(check=AsyncMock(return_value=_verdicts(r1="yes", r2="unclear", r3="yes")), calibrated=True)
    page.at.session_state["solution_text"] = SOLUTION
    page.run()
    assert [r.graded for r in page.out.requirements] == [True, True, True]
    assert any("could not tell for R2" in c for c in page.captions)


def test_a_failed_check_leaves_everything_graded_and_offers_a_retry():
    page = Page(check=AsyncMock(side_effect=RuntimeError("model unavailable")))
    page.at.session_state["solution_text"] = SOLUTION
    page.run()
    assert [r.graded for r in page.out.requirements] == [True, True, True]
    assert any("model unavailable" in w.value and "stays graded" in w.value for w in page.at.warning)
    assert page.check.await_count == 1  # no retry loop on its own
    page.check.side_effect = None
    page.check.return_value = _verdicts(r1="yes", r2="no", r3="yes")
    page.click("Try the check again")
    assert page.check.await_count == 2
    assert [r.solution_status for r in page.out.requirements] == ["yes", "no", "yes"]


def test_removing_the_solution_clears_the_check():
    page = Page(calibrated=True)
    page.at.session_state["solution_text"] = SOLUTION
    page.run()
    assert not page.out.requirements[1].graded
    page.at.session_state["solution_text"] = None
    page.run()
    assert all(r.graded and r.solution_status is None for r in page.out.requirements)


def test_a_new_solution_is_checked_again_and_the_same_one_is_not():
    page = Page()
    page.at.session_state["solution_text"] = SOLUTION
    page.run().run()
    assert page.check.await_count == 1
    page.at.session_state["solution_text"] = SOLUTION + "\n// second version"
    page.run()
    assert page.check.await_count == 2


def test_the_manual_check_button_asks_again():
    page = Page()
    page.at.session_state["solution_text"] = SOLUTION
    page.run()
    page.click("Check all against the solution")
    assert page.check.await_count == 2


def test_an_edited_row_shows_a_recheck_button_that_updates_only_that_row():
    page = Page()
    page.at.session_state["solution_text"] = SOLUTION
    page.run()
    assert not any(label.startswith("Re-check") for label in page.labels)
    key = rc.instructions_hash("source-1")
    editor_key = f"requirements_editor_{key[:12]}_{page.at.session_state['requirement_editor_generation'][key]}"
    edits = {editor_key: {"edited_rows": {1: {"text": "Applies a ten percent discount"}},
                          "added_rows": [], "deleted_rows": []}}
    page.at.session_state[editor_key] = edits[editor_key]
    page.check.return_value = _verdicts(r1="no", r2="yes", r3="no")
    page.run()
    assert "Re-check R2" in page.labels and "Re-check R1" not in page.labels
    assert page.out.requirements[1].text == "Applies a ten percent discount"
    assert page.out.requirements[1].solution_status is None  # the old verdict was for the old text
    page.click("Re-check R2", editor_state=edits)
    statuses = {r.id: r.solution_status for r in page.out.requirements}
    assert statuses == {"R1": "yes", "R2": "yes", "R3": "yes"}  # R1 and R3 keep the earlier answer
    texts = [c.args[1].requirements[1].text for c in page.check.await_args_list[-1:]]
    assert texts == ["Applies a ten percent discount"]
