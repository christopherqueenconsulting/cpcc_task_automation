#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""AppTest: the Student result Feedback tab renders Markdown and can show the source."""

import re
import string

import pytest
from streamlit.testing.v1 import AppTest

from cqc_cpcc.rubric_models import CriterionResult, DetectedError, RubricAssessmentResult
from cqc_cpcc.student_feedback_builder import build_student_feedback
from cqc_streamlit_app.grade_assignment import render_safe_markdown

TOGGLE = "student_feedback_show_source_Student A"


def _result(note: str = "Instructor note.") -> RubricAssessmentResult:
    return RubricAssessmentResult(
        rubric_id="csc134_cpp_exam_rubric", rubric_version="3.1",
        total_points_possible=30, total_points_earned=27,
        criteria_results=[CriterionResult(criterion_id="program_performance", criterion_name="Program Performance",
                                          points_possible=30, points_earned=27,
                                          selected_level_label="Good", feedback="Works.")],
        overall_feedback=note,
        detected_errors=[DetectedError(code="X", name="Off by one", severity="minor",
                                       description="Gross pay printed as $5.00 instead of $10")],
    )


def _page():
    import streamlit as st

    from cqc_streamlit_app.grade_assignment import display_rubric_assessment_result
    for name in st.session_state["students"]:
        display_rubric_assessment_result(st.session_state["result"], name)


def _app(result, students=("Student A",)) -> AppTest:
    at = AppTest.from_function(_page, default_timeout=30)
    at.session_state["result"] = result
    at.session_state["students"] = list(students)
    return at.run()


def _feedback(result) -> str:
    return build_student_feedback(result, student_name="Student A")


@pytest.mark.unit
def test_feedback_renders_as_markdown_by_default() -> None:
    result = _result()
    at = _app(result)

    assert not at.exception
    assert not at.text_area
    assert render_safe_markdown(_feedback(result)) in [m.value for m in at.markdown]
    assert not [c for c in at.code if c.language == "markdown"]


@pytest.mark.unit
def test_toggle_shows_markdown_source_with_copy() -> None:
    result = _result()
    at = _app(result)

    at.toggle(key=TOGGLE).set_value(True).run()

    sources = [c for c in at.code if c.language == "markdown"]
    assert [c.value for c in sources] == [_feedback(result)]


@pytest.mark.unit
def test_dollar_signs_do_not_become_math() -> None:
    result = _result()
    at = _app(result)
    assert any(r"\$5.00" in m.value for m in at.markdown)

    at.toggle(key=TOGGLE).set_value(True).run()
    assert "$5.00" in at.code[0].value and r"\$" not in at.code[0].value


@pytest.mark.unit
def test_links_and_images_are_inert() -> None:
    note = "See ![x](http://example.invalid/p.png) and [click](http://example.invalid) or http://example.invalid/a"
    at = _app(_result(note=note))

    notes = next(m.value for m in at.markdown if "example.invalid" in m.value)
    assert not re.search(r"(?<!\\)[\[\]]", notes)
    assert "` http://example.invalid/p.png `" in notes and "` http://example.invalid/a `" in notes


@pytest.mark.unit
def test_code_keeps_its_formatting_and_text() -> None:
    assert render_safe_markdown('pay `printf("$%.2f")` costs $5') == 'pay  ` printf("$%.2f") `  costs \\$5'
    for fence in ("```", "~~~"):
        safe = render_safe_markdown(f'{fence}java\nSystem.out.printf("$%.2f");\n{fence}')
        assert safe == '```java\nSystem.out.printf("$%.2f");\n```'
    # A fence whose content holds a longer backtick run gets a longer fence.
    assert render_safe_markdown("```\nx ```` y\n```") == "`````\nx ```` y\n`````"


BYPASSES = [
    "\\`![x](https://e.invalid/p.png)`",            # escaped backtick hiding an image
    "```[x](//e.invalid)`",                          # mismatched backtick runs
    "[c][1]\n\n[1]: //e.invalid",                   # reference link
    "[e]\n\n[e]: //e.invalid",                      # shortcut reference link
    "HTTPS://e.invalid and WWW.e.invalid.",          # uppercase autolinks
    "<ftp://e.invalid> <http:/e.invalid> <javascript:alert(1)>",
    "- item\n  ```\n  x\n[a](//e.invalid)",         # indented fence closed early by the list
    "```x`\n[a](//e.invalid)",                       # backtick in a fence info string: not a fence
    "www.e.invalid``[b](//e.invalid)`` and `www.f.invalid`[c](//e.invalid)",
    "| a | b | c |\n|---|---|---|\n| `x | [l](//e.invalid) | y` |",   # table cell splits a code span
    "| a | b |\n|---|---|\n| `x | ![i](https://e.invalid/p.png) | y` |",
    "\\https://e.invalid \\mailto:x@e.invalid \\a@e.invalid",           # backslash before a letter
    " ~~~\n~~~\n[x](//e.invalid)\n~~~",                                  # indented tilde fence
    "  ~~~\n~~~\n[x](//e.invalid)\n~~~",
    "   ~~~\n~~~\n[x](//e.invalid)\n~~~",
]

_PUNCT = re.escape(string.punctuation)


def _prose(md: str) -> str:
    """What CommonMark reads as prose, outside fences: escaped punctuation and code spans
    removed. A backtick run with no same-length closer is literal text, so what follows it
    stays prose. Fenced blocks are removed whole."""
    md = re.sub(r"(?ms)^ {0,3}(`{3,}|~{3,}).*?^ {0,3}\1[`~]*\s*$", "", md)
    md = re.sub(rf"\\[{_PUNCT}]", "", md)
    out, i = [], 0
    while i < len(md):
        if md[i] != "`":
            out.append(md[i])
            i += 1
            continue
        run = len(md[i:]) - len(md[i:].lstrip("`"))
        close = re.compile(rf"(?<!`)`{{{run}}}(?!`)").search(md, i + run)
        i = close.end() if close else i + run
    return "".join(out)


@pytest.mark.unit
@pytest.mark.parametrize("payload", BYPASSES)
def test_link_bypasses_are_neutralised(payload: str) -> None:
    prose = _prose(render_safe_markdown(payload))
    assert not re.search(r"[\[\]<|~`]", prose), prose
    assert not re.search(r"(?i)https?://|www\.", prose), prose


@pytest.mark.unit
def test_an_escaped_dollar_stays_escaped() -> None:
    assert "$" not in _prose(render_safe_markdown("\\$5 and $10"))


@pytest.mark.unit
def test_www_and_email_autolinks_are_inert() -> None:
    safe = render_safe_markdown("Visit www.example.invalid or mail ada@example.invalid.")
    assert "` www.example.invalid `" in safe and "` ada@example.invalid `" in safe


@pytest.mark.unit
def test_a_batch_renders_each_student_with_its_own_toggle() -> None:
    at = _app(_result(), students=("Student A", "Student B"))
    assert not at.exception

    at.toggle(key=TOGGLE).set_value(True).run()

    assert at.toggle(key="student_feedback_show_source_Student B").value is False
    assert len([c for c in at.code if c.language == "markdown"]) == 1


@pytest.mark.unit
def test_choice_carries_to_the_next_student_opened() -> None:
    at = _app(_result())
    at.toggle(key=TOGGLE).set_value(True).run()

    at.session_state["students"] = ["Student B"]
    at.run()

    assert at.toggle(key="student_feedback_show_source_Student B").value is True
    assert [c.language for c in at.code if c.language == "markdown"] == ["markdown"]
