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
    "output check", "Output Check", "reference solution", "(incomplete)", "(not implemented)",
    "verified by g++", "Verified by javac", "verified by python", "verified by the compiler check",
    "the grader reports", "the app checks", "the app graded", "scored by the grader"])
def test_mechanics_phrases(phrase):
    assert _tokens(f"Here is {phrase} text.") == [("phrase", phrase)]


@pytest.mark.unit
@pytest.mark.parametrize("sentence", [
    "Open the application, then read the references.",
    "a better solution is a for loop",
    "write three test cases",
    "run the test run again",
    "the grader of the contest was kind",
    "this checklist helps you plan",
    "your reference variable is null",
    "verified by hand",
    "call nextInt() before nextLine()",
    "the M1 chip and the C11 standard",
    "type T1 is a template parameter",
    "R1+ and T# are not ids",
    "out of 100 points",
    "rename lab_3.cpp and keep MAX_SIZE",
    "the \"while\" condition is fine",
    "the “while” loop is fine",
    "Needs Improvement is a label that only counts at the start of a line",
])
def test_ordinary_feedback_lints_clean(sentence):
    assert lint_student_feedback(sentence) == []


@pytest.mark.unit
def test_phrases_and_identifiers_that_the_instructions_use_are_skipped():
    text = "The required output is two lines. Use maxValue and the reference solution."
    instructions = "The required output is two lines. Declare maxValue. See the Reference Solution."
    assert lint_student_feedback(text, instructions_text=instructions) == []
    assert _tokens(text) == [("identifier", "maxValue"), ("phrase", "reference solution")]


@pytest.mark.unit
def test_known_source_tokens_are_skipped():
    assert lint_student_feedback("Rename runningTotal.", known_tokens={"runningTotal"}) == []


@pytest.mark.unit
@pytest.mark.parametrize("text,token", [("R1 is incomplete", "R1"), ("see T12.", "T12"), ("R3a fails", "R3a")])
def test_generic_ids_without_a_glossary_are_r_and_t_only(text, token):
    assert ("id", token) in _tokens(text)
    assert lint_student_feedback("M3a, S1 and C4 are not flagged") == []


@pytest.mark.unit
def test_with_a_glossary_only_its_ids_are_flagged():
    glossary = {"T1": object()}
    assert _tokens("R2 and T1 here", glossary=glossary) == [("id", "T1")]
    assert _tokens("Rename `T1` to something clearer.", glossary=glossary) == [("id", "T1")]
    assert lint_student_feedback("Rename `T1` to something clearer.") == []
    assert lint_student_feedback("the M1 chip, C11 standard, type T1", glossary={"R9": object()}) == []


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
def test_hidden_single_token_lines_are_ordinary_words():
    assert lint_student_feedback("You reach the end; out of 100 points", hidden_inputs=("100\nyes\nend",)) == []
    assert lint_student_feedback("You scored 5 items.", hidden_inputs=("5",)) == []
    assert _tokens("Typing 'Pat Kim' broke it.", hidden_inputs=("7\nPat Kim\n",)) == [("hidden_input", "Pat Kim")]
    assert _tokens("It broke on 100 yes end.", hidden_inputs=("100\nyes\nend",)) == [("hidden_input", "100 yes end")]
    assert _tokens("Try verylongword.", hidden_inputs=("verylongword",)) == [("hidden_input", "verylongword")]


@pytest.mark.unit
def test_hidden_inputs_in_the_instructions_are_skipped():
    assert lint_student_feedback("Try 45 12.50 again.", hidden_inputs=("45 12.50",),
                                 instructions_text="Sample run: 45 12.50") == []


@pytest.mark.unit
def test_identifiers_are_review_severity_and_outside_backticks_only():
    found = lint_student_feedback("The variable runningTotal and total_pay are reset.")
    assert [(f.token, f.severity) for f in found] == [("runningTotal", "review"), ("total_pay", "review")]
    assert lint_student_feedback("The variable `runningTotal` and `total_pay` are reset.") == []


@pytest.mark.unit
def test_other_kinds_are_block_severity():
    assert {f.severity for f in lint_student_feedback("R1 values_differ the reference solution")} == {"block"}


@pytest.mark.unit
def test_level_label_only_at_the_start_of_a_line_or_bullet():
    assert _tokens("Overview.\n- Logic: Needs Improvement: loop") == [("level_label", "Needs Improvement:")]
    assert _tokens("Developing: your loop") == [("level_label", "Developing:")]
    assert lint_student_feedback("Your loop is developing: it needs work.") == []


@pytest.mark.unit
def test_id_rewrite_needs_curly_quotes_and_a_leading_the():
    assert _tokens("See the “Typical order” test.") == [("id_rewrite", "the “Typical order” test")]
    assert lint_student_feedback("See “Typical order” test.") == []


@pytest.mark.unit
def test_verdict_token_is_not_also_reported_as_identifier():
    assert _tokens("It was values_differ.") == [("verdict", "values_differ")]


@pytest.mark.unit
def test_findings_carry_offsets_and_are_ordered():
    text = "First the output check, then R2."
    found = lint_student_feedback(text)
    assert all(text[f.start:f.end] == f.token for f in found)
    assert [f.start for f in found] == sorted(f.start for f in found)
    assert isinstance(found[0], Finding)
    with pytest.raises(Exception):
        found[0].kind = "x"  # frozen


@pytest.mark.unit
def test_empty_text_and_grouped_summary():
    assert lint_student_feedback("") == [] and lint_student_feedback(None) == []
    found = lint_student_feedback("R1 and R1 and R2 and the output check and runningTotal")
    summary = summarize_findings(found)
    assert summary.startswith("These words come from grading, not teaching, and a student may not understand them: "
                              "“R1” (requirement or test id), “R2” (requirement or test id), "
                              "“output check” (grading wording). Rewrite them or delete the line")
    assert "Check that these code names come from the student's own work: “runningTotal”." in summary
    assert summarize_findings(found, limit=1).count("and 2 more") == 1
    assert summarize_findings([]) == ""


@pytest.mark.unit
def test_hidden_line_scan_is_capped():
    hidden = "\n".join(f"filler line {i}" for i in range(50)) + "\nsecret late line"
    assert lint_student_feedback("It had secret late line in it.", hidden_inputs=(hidden,)) == []


def _best_of(fn, runs=3) -> float:
    best = float("inf")
    for _ in range(runs):
        start = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - start)
    return best


@pytest.mark.unit
@pytest.mark.parametrize("make", [
    lambda n: "word " * (n // 5),
    lambda n: "aA" * (n // 2),
    lambda n: "a_" * (n // 2),
    lambda n: "the “" * (n // 5),
    lambda n: "`" + "a" * (n - 10) + "`" * 9,
    lambda n: "`a" * (n // 2),
    lambda n: "R1 " * (n // 3),
    lambda n: "45 12.50 " * (n // 9),
])
def test_fifty_kb_input_grows_roughly_linearly(make):
    glossary = build_id_glossary(CHECKLIST, PLAN)

    def run(size):
        text = make(size)
        return _best_of(lambda: lint_student_feedback(text, glossary=glossary, hidden_inputs=("45 12.50\n",)))

    small, large = run(5_000), run(50_000)
    assert large < 10.0  # generous ceiling for a loaded CI shard
    # 10x the input: linear is about 10x; quadratic would be about 100x. Allow a floor for timer noise.
    assert large < max(small, 0.005) * 40


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
    ({"notes": "The output check disagrees."}, "phrase", "output check"),
    ({"crit": "Needs Improvement: the loop is wrong."}, "level_label", "Needs Improvement:"),
    ({"desc": "The variable totalPay is never updated."}, "identifier", "totalPay"),
    ({"notes": "See the “Typical order” test."}, "id_rewrite", "the “Typical order” test"),
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
