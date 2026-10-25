#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Flowgorithm grading on the LLM gateway (prompt flowgorithm-grade v2)."""

from unittest.mock import AsyncMock

import pytest

from cqc_cpcc import flowgorithm_grading as fg

pytestmark = pytest.mark.unit


def _grade(deductions=(("Comments", 5),), final=45):
    return fg.FlowgorithmGrade(
        requirements_summary="Read two numbers and print the sum.",
        deductions=[fg.Deduction(criterion=c, points=p, reason=f"{c} reason") for c, p in deductions],
        final_grade=final,
        overall_feedback="Good start.",
    )


def test_prompt_contains_every_input():
    prompt = fg.build_flowgorithm_prompt("ASSIGN", "| Criterion | Points |", "SUBMISSION", "Sum.fprg", "50")
    for text in ("ASSIGN", "| Criterion | Points |", "SUBMISSION", "Sum.fprg", "## Total Possible Points\n50"):
        assert text in prompt
    assert "{" not in prompt.replace("{}", "")  # every placeholder filled


def test_backend_grade_is_total_minus_deductions_floored_at_zero():
    assert fg.backend_grade(_grade((("A", 5), ("B", 2.5))), "50") == 42.5
    assert fg.backend_grade(_grade((("A", 80),)), 50) == 0.0
    assert fg.backend_grade(_grade(), "not a number") is None


def test_render_uses_backend_grade_not_the_models_arithmetic():
    text = fg.render_markdown(_grade((("Comments", 5),), final=49), 50)
    assert "- **Comments** (-5): Comments reason" in text
    assert "**Final grade: 45 / 50**" in text
    assert text.rstrip().endswith("Good start.")


def test_render_with_no_deductions():
    assert "None: every rubric criterion is met." in fg.render_markdown(_grade(()), 50)


async def test_grade_calls_gateway_on_flowgorithm_role(mocker):
    structured = mocker.patch("cqc_cpcc.utilities.AI.llm_gateway.structured",
                              new_callable=AsyncMock, return_value=_grade())
    result = await fg.grade_flowgorithm("A", "T", "S", "f.fprg", "50", model_name="openai/x")
    assert result.final_grade == 45
    kwargs = structured.call_args.kwargs
    assert kwargs["role"] == "flowgorithm"
    assert kwargs["schema_model"] is fg.FlowgorithmGrade
    assert kwargs["override"] == "openai/x"
    assert kwargs["use_auto_route"] is False
    assert kwargs["prompt_id"] == "flowgorithm-grade"
    # The call sends the split messages: system first, the fenced submission in the user message.
    assert "Step 1: Summarize the requirements" in kwargs["system"]
    assert "<<<UNTRUSTED" not in kwargs["system"].replace("<<<UNTRUSTED ...", "")
    assert kwargs["prompt"].startswith("## Assignment\nA\n")
    assert "\nS\n<<<UNTRUSTED SUBMISSION" in kwargs["prompt"]


async def test_auto_route_model_turns_on_auto_routing(mocker):
    structured = mocker.patch("cqc_cpcc.utilities.AI.llm_gateway.structured",
                              new_callable=AsyncMock, return_value=_grade())
    await fg.grade_flowgorithm("A", "T", "S", "f.fprg", "50", model_name="openrouter/auto")
    assert structured.call_args.kwargs["override"] is None
    assert structured.call_args.kwargs["use_auto_route"] is True


async def test_test_mode_returns_a_valid_grade(monkeypatch):
    from cqc_cpcc.utilities.AI import llm_gateway

    monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
    grade = await fg.grade_flowgorithm("A", "T", "S", "f.fprg", "50")
    assert isinstance(grade, fg.FlowgorithmGrade)
    assert "Final grade: 45 / 50" in fg.render_markdown(grade, 50)


# --- file-name scrub (same pii_redaction.scrub as the rubric ZIP path) -------------------------

from cqc_cpcc.utilities import pii_redaction  # noqa: E402
from cqc_cpcc.utilities.AI import untrusted_fence  # noqa: E402

_NONCE = "n0nce1234"


@pytest.fixture
def ada_registered():
    pii_redaction.clear_registry()
    pii_redaction.register_student("Ada Example")
    yield pii_redaction.alias("Ada Example")
    pii_redaction.clear_registry()


def _file_name_block(user, nonce=_NONCE):
    opening, closing = untrusted_fence.markers("File Name", nonce)
    return user.split(opening, 1)[1].split(closing, 1)[0].strip("\n")


def test_registered_student_name_in_file_name_is_scrubbed_in_the_user_message(ada_registered):
    _, user = fg.build_flowgorithm_messages("A", "T", "SUB", "Ada_Example_Sum", "50", nonce=_NONCE)
    assert _file_name_block(user) == f"{ada_registered}_Sum"
    assert "Ada" not in user and "Example" not in user


def test_folder_style_and_email_file_names_are_scrubbed():
    pii_redaction.clear_registry()
    _, user = fg.build_flowgorithm_messages("A", "T", "SUB", "12345-67890 - Ada Example", "50", nonce=_NONCE)
    assert _file_name_block(user) == pii_redaction.alias("Ada Example")
    _, user = fg.build_flowgorithm_messages("A", "T", "SUB", "ada.example@cpcc.edu", "50", nonce=_NONCE)
    assert _file_name_block(user) == "<email>"


@pytest.mark.parametrize("name", ["Sum", "Sum.fprg", "Student_A", "OrderTotal", "Project2_20241031"])
def test_ordinary_file_names_pass_unchanged(name):
    pii_redaction.clear_registry()
    _, user = fg.build_flowgorithm_messages("A", "T", "SUB", name, "50", nonce=_NONCE)
    assert _file_name_block(user) == name


def test_the_prompt_builder_scrubs_too(ada_registered):
    prompt = fg.build_flowgorithm_prompt("A", "T", "SUB", "Ada_Example_Sum", "50", nonce=_NONCE)
    assert "Ada" not in prompt and ada_registered in prompt


async def test_grade_flowgorithm_scrubs_when_called_directly(monkeypatch, ada_registered):
    from cqc_cpcc.utilities.AI import llm_gateway

    structured = AsyncMock(return_value=_grade())
    monkeypatch.setattr(llm_gateway, "structured", structured)
    await fg.grade_flowgorithm("A", "T", "SUB", "Ada_Example_Sum", "50")
    prompt = structured.await_args.kwargs["prompt"]
    assert "Ada" not in prompt and "Example" not in prompt
    assert f"{ada_registered}_Sum" in prompt
