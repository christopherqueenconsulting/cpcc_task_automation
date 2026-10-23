#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""AppTest: the Student result LLM Prompt tab shows the exact request, or says it was not kept."""

import pytest
from streamlit.testing.v1 import AppTest

from cqc_cpcc.rubric_models import CriterionResult, RubricAssessmentResult

pytestmark = pytest.mark.unit

REQUEST = {"prompt_id": "rubric-grading", "prompt": "rubric-grading@7", "model": "openai/gpt-6-luna",
           "effort": "high", "timeout_s": 180, "correlation_id": "corr-1",
           "messages": [{"role": "user", "content": "Grade this submission: SENTINEL-PROMPT-TEXT"}],
           "params": {"reasoning": {"effort": "high"}}}


def _result(request) -> RubricAssessmentResult:
    return RubricAssessmentResult(
        rubric_id="csc134_cpp_exam_rubric", rubric_version="3.1",
        total_points_possible=30, total_points_earned=27,
        criteria_results=[CriterionResult(criterion_id="program_performance", criterion_name="Program Performance",
                                          points_possible=30, points_earned=27,
                                          selected_level_label="Good", feedback="Works.")],
        overall_feedback="note", llm_request=request)


def _page():
    import streamlit as st
    from cqc_streamlit_app.grade_assignment import display_rubric_assessment_result
    display_rubric_assessment_result(st.session_state["result"], "Student A")


def _app(result) -> AppTest:
    at = AppTest.from_function(_page, default_timeout=30)
    at.session_state["result"] = result
    return at.run()


def test_the_tab_shows_each_message_as_code_with_the_privacy_caption():
    from cqc_streamlit_app.grade_assignment import PROMPT_PRIVACY_CAPTION

    at = _app(_result(REQUEST))
    assert not at.exception
    assert "LLM Prompt" in [t.label for t in at.tabs]
    assert PROMPT_PRIVACY_CAPTION in [c.value for c in at.caption]
    blocks = [c for c in at.code if "SENTINEL-PROMPT-TEXT" in c.value]
    assert blocks and blocks[0].language == "markdown"
    # The prompt is never rendered as Markdown.
    assert not any("SENTINEL-PROMPT-TEXT" in m.value for m in at.markdown)
    assert any("corr-1" in c.value for c in at.caption)


def test_a_restored_result_says_the_prompt_was_not_kept():
    at = _app(_result(None))
    assert not at.exception
    assert any("Prompt not kept for restored runs" in i.value for i in at.info)
