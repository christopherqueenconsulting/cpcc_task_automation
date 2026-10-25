#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Flowgorithm grading on the LLM gateway (prompt flowgorithm-grade v3)."""

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
    assert "Sum.fprg" not in prompt
    for text in ("ASSIGN", "| Criterion | Points |", "SUBMISSION", "(50)"):
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


# --- the file name never reaches the request; app-checked facts do ------------------------------

import json  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from cqc_cpcc.prompts.flowgorithm import FLOWGORITHM_GRADE_PROMPT  # noqa: E402

_NAME_PARTS = ("Ada", "Example", "AdaExample")
_LABELS = {  # builder-local labels: file name -> (lab line, name line) as sent
    "Ada_Example_Lab3.fprg": ("yes (Lab 3)", "yes"),
    "Ada Example.fprg": ("no", "yes"),
    "Lab3.fprg": ("yes (Lab 3)", "no"),
}


def _sent(mocker):
    return mocker.patch("cqc_cpcc.utilities.AI.llm_gateway.structured",
                        new_callable=AsyncMock, return_value=_grade())


def _serialized(structured):
    return json.dumps(structured.await_args.kwargs, default=str)


def _facts_lines(prompt):
    lab = next(l for l in prompt.splitlines() if l.startswith("- Lab number in file name: "))
    name = next(l for l in prompt.splitlines() if l.startswith("- Name in file name: "))
    return lab.split(": ", 1)[1], name.split(": ", 1)[1]


@pytest.mark.parametrize("file_name", ["Ada_Example_Lab3.fprg", "Ada Example.fprg", "/tmp/x/AdaExample_L3.fprg",
                                       "Lab03 - Ada Example", "ada.example@cpcc.edu"])
async def test_no_part_of_the_real_name_reaches_the_request(mocker, file_name):
    structured = _sent(mocker)
    await fg.grade_flowgorithm("A", "T", "SUB", file_name, "50")
    sent = _serialized(structured)
    for part in _NAME_PARTS:
        assert part not in sent
    assert "cpcc.edu" not in sent and "student_" not in sent and "tmp" not in sent


@pytest.mark.parametrize("file_name,expected", list(_LABELS.items()))
async def test_grade_path_sends_the_facts(mocker, file_name, expected):
    structured = _sent(mocker)
    await fg.grade_flowgorithm("A", "T", "SUB", file_name, "50")
    lab, name = _facts_lines(structured.await_args.kwargs["prompt"])
    assert lab == expected[0]
    assert name.split(" (")[0] == expected[1]


def test_facts_block_text_and_wording_in_the_built_prompt():
    prompt = fg.build_flowgorithm_prompt("A", "T", "SUB", "Ada_Example_Lab3.fprg", "50")
    assert "- Lab number in file name: yes (Lab 3)\n" in prompt
    assert "- Name in file name: yes\n" in prompt and "roster" not in prompt
    assert "never deduct for it" in prompt and "beyond the criterion decision" in prompt
    assert "Submission File Name:\n" not in prompt
    assert "decided ONLY by these app-checked facts" in prompt
    assert "unknown -> never deduct" in prompt
    assert "do not guess it or mention its absence as a defect" in prompt
    assert "Ada" not in prompt and "Example" not in prompt


def test_unknown_file_name_asks_for_no_deduction():
    prompt = fg.build_flowgorithm_prompt("A", "T", "SUB", "", "50")
    assert _facts_lines(prompt) == ("unknown", "unknown")


def test_the_rule_is_in_the_template_and_nothing_else_moved():
    assert "{file_name_facts}" in FLOWGORITHM_GRADE_PROMPT
    assert "{submission_file_name}" not in FLOWGORITHM_GRADE_PROMPT
    for needle in ("{assignment}", "{submission}", "{rubric_criteria_markdown_table}", "{total_possible_points}",
                   "Output Instructions (structured JSON):"):
        assert needle in FLOWGORITHM_GRADE_PROMPT


def test_registry_renderer_is_deterministic():
    from cqc_cpcc.model_eval import prompt_registry as pr

    assert pr._render_flowgorithm_grade() == pr._render_flowgorithm_grade()


async def test_the_eval_suite_path_sends_facts_not_the_name(mocker):
    from cqc_cpcc.model_eval.suites.flowgorithm import SUITE

    structured = _sent(mocker)
    case = SimpleNamespace(inputs={"assignment": "A", "rubric_criteria_markdown_table": "T",
                                   "submission": "SUB", "submission_file_name": "Ada Example",
                                   "total_possible_points": "50"})
    await SUITE.invoke(case)
    sent = _serialized(structured)
    assert "Ada" not in sent and "Example" not in sent
    assert _facts_lines(structured.await_args.kwargs["prompt"]) == (
        "no", "yes")
