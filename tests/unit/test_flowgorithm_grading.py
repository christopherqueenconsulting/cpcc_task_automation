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
    for text in ("ASSIGN", "| Criterion | Points |", "SUBMISSION", "Sum.fprg", "Total Possible Points:\n50"):
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
    # One user message (no system message); the fenced submission comes last.
    assert "system" not in kwargs
    assert kwargs["prompt"].startswith("You are a community college professor")
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


# --- the file name never reaches the request; a neutral alias does ------------------------------

import json  # noqa: E402
import re  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from cqc_cpcc.utilities import pii_redaction  # noqa: E402
from cqc_cpcc.utilities.AI import untrusted_fence  # noqa: E402

# The alias sits alone inside the nonce-fenced File Name block of the user message.
_ALIAS_RE = re.compile(r"<<<UNTRUSTED FILE NAME ([0-9a-f]{16}) BEGIN>>>\nstudent_[0-9a-f]{8}\.fprg\n"
                       r"<<<UNTRUSTED FILE NAME \1 END>>>")


def _sent(mocker):
    return mocker.patch("cqc_cpcc.utilities.AI.llm_gateway.structured",
                        new_callable=AsyncMock, return_value=_grade())


def _serialized(structured):
    return json.dumps(structured.await_args.kwargs, default=str)


async def test_a_bare_name_never_reaches_the_request(mocker):
    pii_redaction.clear_registry()
    structured = _sent(mocker)
    await fg.grade_flowgorithm("A", "T", "SUB", "Ada Example.fprg", "50")
    sent = _serialized(structured)
    assert "Ada" not in sent and "Example" not in sent
    assert _ALIAS_RE.search(structured.await_args.kwargs["prompt"])


async def test_a_registered_name_and_a_student_prefixed_name_are_aliased(mocker):
    pii_redaction.clear_registry()
    pii_redaction.register_student("Ada Example")
    try:
        structured = _sent(mocker)
        for name in ("Ada_Example_Sum.fprg", "student_Ada_Example.fprg", "/tmp/x/Ada Example.fprg"):
            await fg.grade_flowgorithm("A", "T", "SUB", name, "50")
            sent = _serialized(structured)
            assert "Ada" not in sent and "Example" not in sent
            assert _ALIAS_RE.search(structured.await_args.kwargs["prompt"])
    finally:
        pii_redaction.clear_registry()


async def test_ordinary_names_are_aliased_too(mocker):
    structured = _sent(mocker)
    await fg.grade_flowgorithm("A", "T", "SUB", "OrderTotal.fprg", "50")
    prompt = structured.await_args.kwargs["prompt"]
    assert "OrderTotal" not in prompt and _ALIAS_RE.search(prompt)


def test_alias_is_stable_per_file_and_distinct_across_files():
    a1, a2 = fg.file_name_alias("Student A.fprg"), fg.file_name_alias("Student A.fprg")
    b = fg.file_name_alias("Student B.fprg")
    assert a1 == a2 and a1 != b
    assert a1.endswith(".fprg") and fg.file_name_alias("Student A") == a1  # extension never matters
    assert "Student" not in a1


def test_build_prompt_still_scrubs_a_name_it_is_handed_directly():
    pii_redaction.clear_registry()
    prompt = fg.build_flowgorithm_prompt("A", "T", "SUB", "ada.example@cpcc.edu", "50")
    assert "ada.example" not in prompt and "<email>" in prompt


async def test_the_eval_suite_path_sends_the_alias(mocker):
    from cqc_cpcc.model_eval.suites.flowgorithm import SUITE

    structured = _sent(mocker)
    case = SimpleNamespace(inputs={"assignment": "A", "rubric_criteria_markdown_table": "T",
                                   "submission": "SUB", "submission_file_name": "Ada Example",
                                   "total_possible_points": "50"})
    await SUITE.invoke(case)
    sent = _serialized(structured)
    assert "Ada" not in sent and _ALIAS_RE.search(structured.await_args.kwargs["prompt"])


async def test_the_fenced_file_name_block_holds_the_alias_and_not_the_real_name(mocker):
    structured = _sent(mocker)
    await fg.grade_flowgorithm("A", "T", "SUB", "Grace_Hopper_Sum.fprg", "50")
    prompt = structured.await_args.kwargs["prompt"]
    nonce = _ALIAS_RE.search(prompt).group(1)
    opening, closing = untrusted_fence.markers("File Name", nonce)
    block = prompt[prompt.index(opening) + len(opening):prompt.index(closing)].strip()
    assert block == fg.file_name_alias("Grace_Hopper_Sum.fprg")
    assert re.fullmatch(r"student_[0-9a-f]{8}\.fprg", block)
    assert "Grace" not in prompt and "Hopper" not in prompt
