#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Grade assignment, end to end, in CQC_TEST_MODE with synthetic data only.

Covers the main weekly task (docs/ui/UX-GOALS.md §3, hard checks H-5/H-6): Setup →
Submissions & run → Results & review for a ZIP batch on the rubric_and_errors path, and
counts the user actions from page open to pressing Grade.
"""

import re
import zipfile

import pytest
from playwright.async_api import Page, expect

from cqc_cpcc.model_eval.dataset_builder import CPP_CLEAN, CPP_INSTRUCTIONS


class Actions:
    """Counts user actions: one click, one file set, or one type-and-enter."""

    def __init__(self):
        self.n = 0

    async def click(self, locator):
        await locator.click()
        self.n += 1

    async def files(self, locator, path, shown_in=None):
        """Set a file; when ``shown_in`` is given, wait until the uploader lists it.

        A rerun that lands while the file is uploading (the Setup stage advancing on
        a slow CI runner) can drop it, so set it again, up to three times. That is still
        one action for the user, who would simply see the file and carry on."""
        for attempt in range(3):
            # Right after a stage change the previous tabs can linger in the DOM for a moment.
            await expect(locator).to_have_count(1, timeout=20000)
            await locator.set_input_files(str(path))
            if shown_in is None:
                break
            try:
                await expect(shown_in.get_by_text(path.name).first).to_be_visible(timeout=15000)
                break
            except AssertionError:
                if attempt == 2:
                    raise
        self.n += 1


async def _choose(page: Page, actions: Actions, label: str, option: str):
    box = page.get_by_test_id("stSelectbox").filter(has=page.get_by_text(label, exact=True))
    await actions.click(box)
    await actions.click(page.get_by_role("option", name=re.compile(option)).first)


def _uploader(page: Page, label: str):
    return page.get_by_test_id("stFileUploader").filter(has_text=label).locator("input[type=file]")


def _synthetic_batch(tmp_path, extra_student: bool = False):
    instructions = tmp_path / "instructions.txt"
    instructions.write_text(CPP_INSTRUCTIONS)
    batch = tmp_path / "submissions.zip"
    with zipfile.ZipFile(batch, "w") as z:
        z.writestr("101 - Ada Example - Oct 1, 2026 900 AM/payroll.cpp", CPP_CLEAN.replace("{author}", "Ada Example"))
        z.writestr("102 - Ben Sample - Oct 1, 2026 900 AM/payroll.cpp", "")
        if extra_student:  # a different batch, so it is not the cached run
            z.writestr("103 - Cal Fixture - Oct 8, 2026 900 AM/payroll.cpp", CPP_CLEAN.replace("{author}", "Cal Fixture"))
    return instructions, batch


async def _run_batch(page: Page, url: str, tmp_path, fresh: bool) -> int:
    instructions, batch = _synthetic_batch(tmp_path, extra_student=not fresh)
    actions = Actions()
    await page.goto(f"{url}/grade_assignment")
    await expect(page.get_by_role("tab", name="Setup")).to_be_visible(timeout=60000)
    if fresh:
        await _choose(page, actions, "Course", "CSC 134")
        await _choose(page, actions, "Rubric", "CSC 134 C\\+\\+ Program Performance Rubric .*30 pts")
        await _choose(page, actions, "Assignment", r"\(Project\)")
    await actions.files(_uploader(page, "Instructions file"), instructions)
    # Setup completes (canned checklist in test mode) and moves on by itself.
    await expect(page.get_by_role("tab", name="Submissions & run", selected=True)).to_be_visible(timeout=60000)
    await actions.files(_uploader(page, "Student submissions"), batch,
                        shown_in=page.get_by_test_id("stFileUploader").filter(has_text="Student submissions"))
    students = 3 if not fresh else 2
    grade = page.get_by_role("button", name=re.compile(r"Grade \d+ submission"))
    await expect(grade).to_be_enabled(timeout=60000)
    await expect(grade).to_contain_text(f"Grade {students} submissions")
    # The batch preview flags the empty file before any AI call, and the cost is shown.
    await expect(page.get_by_text(f"Batch: {students} student(s)")).to_be_visible()
    await expect(page.get_by_text(re.compile(r"^1 will be scored 0 without an AI call"))).to_be_visible()
    await expect(page.get_by_text("Needs review: Empty submission").first).to_be_attached()
    await expect(page.get_by_text(re.compile(r"(about \$|under \$0\.01).* on "))).to_be_visible()
    await actions.click(grade)
    await expect(page.get_by_role("tab", name="Results & review", selected=True)).to_be_visible(timeout=120000)
    return actions.n


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_zip_batch_fresh_then_same_assignment(page: Page, streamlit_app_url: str, tmp_path):
    first = await _run_batch(page, streamlit_app_url, tmp_path, fresh=True)
    # The empty submission is held for review, the real one graded.
    await expect(page.get_by_text(re.compile(r"Needs review \(1\)")).first).to_be_visible(timeout=60000)
    # First-ever use (reported, not a target): course 2 + rubric 2 + assignment 2 +
    # instructions 1 + submissions 1 + Grade 1.
    assert first == 9

    # Same assignment next time: course, rubric and assignment are remembered.
    second_dir = tmp_path / "second"
    second_dir.mkdir()
    page2 = await page.context.new_page()
    again = await _run_batch(page2, streamlit_app_url, second_dir, fresh=False)
    assert again <= 3  # UX goal: instructions 1 + submissions 1 + Grade 1 (baseline N = 10)
