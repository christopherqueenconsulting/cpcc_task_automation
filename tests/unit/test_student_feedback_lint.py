#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""The student-feedback lint, its seeded leaks, the synthetic fixtures and the golden files."""

import time
from pathlib import Path

import pytest

from cqc_cpcc.feedback_ids import build_id_glossary
from cqc_cpcc.rubric_models import CriterionResult, DetectedError, RubricAssessmentResult
from cqc_cpcc.student_feedback_builder import build_student_feedback
from cqc_cpcc.student_feedback_lint import Finding, lint_student_feedback, summarize_findings
from tests.fixtures.student_feedback.builders import CHECKLIST, FIXTURES, PLAN, STUDENT

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden" / "student_feedback"

#: Fixtures whose built feedback carries internal wording today. Each is fixed in a later PR.
LEAKS_TODAY = {
    "exec_format_only", "exec_text_differs", "exec_values_differ", "exec_order_differs",
    "exec_no_output", "exec_runtime_error", "req_partial", "req_missing", "compile_failure",
    "overlap", "level_band",
}


def _glossary(fx):
    return build_id_glossary(CHECKLIST if fx.uses_checklist else None, PLAN if fx.uses_plan else None)


def _feedback(fx) -> str:
    return build_student_feedback(fx.result, student_name=STUDENT)


def _tokens(text, **kw):
    return [(f.kind, f.token) for f in lint_student_feedback(text, **kw)]


# ---------- the lint ----------

@pytest.mark.unit
@pytest.mark.parametrize("token", [
    "format_only", "text_differs", "values_differ", "order_differs", "no_output", "runtime_error",
    "not_run", "values_may_differ", "VALUES_DIFFER", "MATCH"])
def test_verdict_tokens(token):
    assert _tokens(f"It said {token} here.") == [("verdict", token)]


@pytest.mark.unit
def test_match_is_case_sensitive():
    assert lint_student_feedback("Your output does match the sample.") == []


@pytest.mark.unit
@pytest.mark.parametrize("phrase", [
    "test case", "Test Cases", "test run", "the app", "output check", "grader", "reference solution",
    "the reference", "the solution", "required output", "verified by", "checklist", "(incomplete)",
    "(not implemented)"])
def test_mechanics_phrases(phrase):
    assert _tokens(f"Here is {phrase} text.") == [("phrase", phrase)]


@pytest.mark.unit
def test_phrases_respect_word_boundaries():
    assert lint_student_feedback("Open the application, then read the references and the solutions.") == []


@pytest.mark.unit
@pytest.mark.parametrize("text,token", [("R1 is incomplete", "R1"), ("see T12.", "T12"), ("M3a fails", "M3a"),
                                        ("the S1 case", "S1"), ("C4", "C4")])
def test_ids(text, token):
    assert ("id", token) in _tokens(text)


@pytest.mark.unit
def test_ordinary_words_are_not_ids():
    assert lint_student_feedback("Use R2D2, T-shirts, C++ and Mar 3 as examples; room M is fine.") == []


@pytest.mark.unit
def test_pattern_ids_in_backticks_are_student_code_but_glossary_ids_are_not():
    assert lint_student_feedback("Rename `T1` to something clearer.") == []
    assert _tokens("Rename `T1` to something clearer.", glossary={"T1": object()}) == [("id", "T1")]


@pytest.mark.unit
def test_glossary_ids_beyond_the_pattern():
    assert _tokens("REQ12 is missing", glossary={"REQ12": object()}) == [("id", "REQ12")]
    assert lint_student_feedback("REQ12 is missing") == []


@pytest.mark.unit
def test_hidden_inputs_even_inside_code_spans():
    text = "With the input `45 12.50` the total was wrong."
    assert _tokens(text, hidden_inputs=("45 12.50\n",)) == [("hidden_input", "45 12.50")]
    assert lint_student_feedback(text) == []


@pytest.mark.unit
def test_short_hidden_inputs_are_ignored_and_multiline_inputs_match_each_line():
    assert lint_student_feedback("You scored 5 items.", hidden_inputs=("5",)) == []
    assert _tokens("Typing 'Pat Kim' broke it.", hidden_inputs=("7\nPat Kim\n",)) == [("hidden_input", "Pat Kim")]


@pytest.mark.unit
def test_identifiers_outside_backticks_only():
    assert [t for _, t in _tokens("The variable runningTotal and total_pay are reset.")] == ["runningTotal", "total_pay"]
    assert lint_student_feedback("The variable `runningTotal` and `total_pay` are reset.") == []


@pytest.mark.unit
def test_verdict_token_is_not_also_reported_as_identifier():
    assert _tokens("It was values_differ.") == [("verdict", "values_differ")]


@pytest.mark.unit
def test_findings_carry_offsets_and_are_ordered():
    text = "First the reference, then R2."
    found = lint_student_feedback(text)
    assert all(text[f.start:f.end] == f.token for f in found)
    assert [f.start for f in found] == sorted(f.start for f in found)
    assert isinstance(found[0], Finding)
    with pytest.raises(Exception):
        found[0].kind = "x"  # frozen


@pytest.mark.unit
def test_empty_text_and_summary():
    assert lint_student_feedback("") == [] and lint_student_feedback(None) == []
    found = lint_student_feedback("R1 and R1 and R2 and the reference")
    assert summarize_findings(found) == "“R1”, “R2”, “the reference”"
    assert summarize_findings(found, limit=1).endswith("and 2 more")


@pytest.mark.unit
@pytest.mark.parametrize("make", [
    lambda: "word " * 10_000,
    lambda: "aA" * 25_000,
    lambda: "a_" * 25_000,
    lambda: "the “" * 10_000,
    lambda: "`" + "a" * 49_000 + "`" * 9,
    lambda: "`a" * 25_000,
    lambda: "R1 " * 17_000,
    lambda: "45 12.50 " * 5_500,
])
def test_fifty_kb_input_is_fast(make):
    text = make()
    assert len(text) >= 49_000
    glossary = build_id_glossary(CHECKLIST, PLAN)
    start = time.perf_counter()
    lint_student_feedback(text, glossary=glossary, hidden_inputs=("45 12.50\n",))
    assert time.perf_counter() - start < 2.0


# ---------- seeded leaks: today's leak classes in model-text fields ----------

def _seeded(overall="You wrote a working program.", crit="Your program works well.", desc="The total is off.",
            notes=None) -> str:
    result = RubricAssessmentResult(
        rubric_id="r", rubric_version="1", total_points_possible=30, total_points_earned=15,
        criteria_results=[CriterionResult(criterion_id="p", criterion_name="Program", points_possible=30,
                                          points_earned=15, feedback=crit)],
        overall_feedback=overall,
        detected_errors=[DetectedError(code="X", name="Wrong Total", severity="major", description=desc,
                                       notes=notes)])
    return build_student_feedback(result, student_name=STUDENT)


@pytest.mark.unit
def test_clean_seed_lints_clean():
    assert lint_student_feedback(_seeded()) == []


@pytest.mark.unit
@pytest.mark.parametrize("kwargs,kind,token", [
    ({"desc": "The check shows values_differ for the first input."}, "verdict", "values_differ"),
    ({"overall": "R3 is incomplete in your program."}, "id", "R3"),
    ({"notes": "Your output does not agree with the reference."}, "phrase", "the reference"),
    ({"crit": "Needs Improvement: the loop is wrong."}, "level_label", "Needs Improvement:"),
    ({"desc": "The variable totalPay is never updated."}, "identifier", "totalPay"),
    ({"notes": "See the “Typical order” test."}, "id_rewrite", "“Typical order” test"),
])
def test_seeded_leak_is_caught(kwargs, kind, token):
    assert (kind, token) in _tokens(_seeded(**kwargs))


# ---------- fixtures and goldens ----------

@pytest.mark.unit
def test_every_plan_path_has_a_fixture():
    assert set(FIXTURES) == {
        "no_plan", "exec_format_only", "exec_text_differs", "exec_values_differ", "exec_order_differs",
        "exec_no_output", "exec_runtime_error", "req_met", "req_partial", "req_missing", "compile_failure",
        "invalid_submission", "overlap", "level_band"}


@pytest.mark.unit
@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_golden_matches_current_output(name, request):
    path = GOLDEN_DIR / f"{name}.md"
    text = _feedback(FIXTURES[name])
    if request.config.getoption("--update-golden"):
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert path.read_text(encoding="utf-8") == text, "run pytest --update-golden to regenerate, then review the diff"


def _golden_params():
    return [pytest.param(name, id=name, marks=[pytest.mark.xfail(strict=True, reason="fixed in later PRs (plan §7)")]
                         if name in LEAKS_TODAY else [])
            for name in sorted(FIXTURES)]


@pytest.mark.unit
@pytest.mark.parametrize("name", _golden_params())
def test_golden_lints_clean(name):
    fx = FIXTURES[name]
    text = (GOLDEN_DIR / f"{name}.md").read_text(encoding="utf-8")
    found = lint_student_feedback(text, glossary=_glossary(fx), hidden_inputs=fx.hidden_inputs)
    assert found == [], summarize_findings(found)
