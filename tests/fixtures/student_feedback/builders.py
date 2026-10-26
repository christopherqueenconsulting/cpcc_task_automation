#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Synthetic graded results, one per feedback path (plan section 6).

Every result is what the pipeline hands ``build_student_feedback`` today, including the wording the
backend stages and the model currently add. The student is "Student A"; no real names, ids, work or
grades appear. ``FIXTURES`` maps a fixture name to a ``FeedbackFixture``; the goldens under
``tests/golden/student_feedback`` are the builder's output for each.
"""

from __future__ import annotations

from dataclasses import dataclass

from cqc_cpcc.execution_check import Condition, ExecutionPlan, TestCase
from cqc_cpcc.requirement_coverage import RequirementChecklist, RequirementItem
from cqc_cpcc.rubric_models import (
    CriterionResult,
    DetectedError,
    RequirementResult,
    RubricAssessmentResult,
)

STUDENT = "Student A"

CHECKLIST = RequirementChecklist(requirements=[
    RequirementItem(id="R1", text="Reads the hours worked and the hourly rate from the keyboard", weight="core"),
    RequirementItem(id="R2", text="Prints the gross pay with two decimal places", weight="core"),
    RequirementItem(id="R3", text="Pays time and a half for hours over 40", weight="secondary"),
])

PLAN = ExecutionPlan(
    language="cpp", solution_sha256="0" * 64,
    cases=[
        TestCase(id="T1", label="Typical week", stdin="45 12.50\n", source="llm"),
        TestCase(id="T2", label="Ordinary hours", stdin="30 10\n", source="sample"),
        TestCase(id="M1", label="Hours over forty", stdin="52 18.25\n", source="llm"),
    ],
    conditions=[Condition(id="C1", text="hours are over 40")],
)
HIDDEN_INPUTS = ("45 12.50\n", "52 18.25\n")


@dataclass(frozen=True)
class FeedbackFixture:
    name: str
    result: RubricAssessmentResult
    hidden_inputs: tuple = ()
    uses_plan: bool = False
    uses_checklist: bool = False


def _criterion(points=(30, 24), label="Good", feedback="Your program works well and the output is clear.",
               cid="program", name="Program Performance") -> CriterionResult:
    return CriterionResult(criterion_id=cid, criterion_name=name, points_possible=points[0],
                           points_earned=points[1], selected_level_label=label, feedback=feedback)


def _result(criteria, overall, errors=None, **extra) -> RubricAssessmentResult:
    errors = errors or []
    counts: dict = {}
    for e in errors:
        counts[e.severity] = counts.get(e.severity, 0) + 1
    return RubricAssessmentResult(
        rubric_id="synthetic_rubric", rubric_version="1.0",
        total_points_possible=sum(c.points_possible for c in criteria),
        total_points_earned=sum(c.points_earned or 0 for c in criteria),
        criteria_results=criteria, overall_feedback=overall,
        detected_errors=errors or None, error_counts_by_severity=counts or None, **extra)


def _exec_error(code, name, severity, description, notes) -> DetectedError:
    return DetectedError(code=code, name=name, severity=severity, description=description,
                         occurrences=1, notes=notes, source="execution")


def _output_note(stdin: str, expected: str, actual: str) -> str:
    # The shape execution_gate._transparency_note writes today (code spans from inert()).
    return (f"Output check: with the input `{' '.join(stdin.split())}`, the expected line was "
            f"`{expected}` but your program printed `{actual}`.")


def _build() -> dict:
    out: dict = {}

    def add(name, result, **kw):
        out[name] = FeedbackFixture(name=name, result=result, **kw)

    add("no_plan", _result(
        [_criterion(feedback="Your program is well organized and reads its input clearly.")],
        "You built a working pay calculator. Check how you round the total.",
        [DetectedError(code="FORMAT", name="Missing Decimal Places", severity="minor",
                       description="The gross pay is printed without two decimal places.", occurrences=1)]))

    add("exec_format_only", _result(
        [_criterion((30, 27), "Good", "Your program works well; only the layout differs.")],
        "Your calculation is right, and the layout needs a small fix.",
        [_exec_error("OUT_FORMAT", "Output Format", "minor",
                     "The output spacing does not match the assignment.",
                     _output_note("45 12.50", "Gross pay: 618.75", "Gross pay:618.75"))]),
        uses_plan=True, hidden_inputs=HIDDEN_INPUTS)

    add("exec_text_differs", _result(
        [_criterion((30, 26), "Good", "Your logic is sound and the wording needs attention.")],
        "Your program calculates pay correctly. The label text differs from the assignment.",
        [_exec_error("OUT_TEXT", "Output Wording", "minor",
                     "The prompt wording differs. Case T1 returned text_differs against the reference.",
                     _output_note("45 12.50", "Enter hours worked:", "Hours?"))]),
        uses_plan=True, hidden_inputs=HIDDEN_INPUTS)

    add("exec_values_differ", _result(
        [_criterion((30, 18), "Needs Improvement", "The totals are wrong for some inputs.")],
        "Your program runs, but the pay it prints is wrong for a normal week.",
        [_exec_error("OUT_VALUE", "Incorrect Output Value", "major",
                     "The gross pay is wrong: the table shows values_differ for T1.",
                     _output_note("45 12.50", "Gross pay: 618.75", "Gross pay: 562.50"))]),
        uses_plan=True, hidden_inputs=HIDDEN_INPUTS)

    add("exec_order_differs", _result(
        [_criterion((30, 25), "Good", "Everything is calculated; the lines print in a different order.")],
        "The numbers are right. They appear in a different order than the assignment asks for.",
        [_exec_error("OUT_ORDER", "Output Order", "minor",
                     "The lines print in a different order from the assignment.",
                     _output_note("52 18.25", "Regular pay: 730.00", "Overtime pay: 333.00"))]),
        uses_plan=True, hidden_inputs=HIDDEN_INPUTS)

    add("exec_no_output", _result(
        [_criterion((30, 12), "Needs Improvement", "Nothing is printed when the program runs.")],
        "Your program starts but never prints a result.",
        [_exec_error("OUT_NONE", "No Output", "major",
                     "The program prints nothing for the test inputs.",
                     "Output check: your program's output differs from the expected output in format.")]),
        uses_plan=True, hidden_inputs=HIDDEN_INPUTS)

    add("exec_runtime_error", _result(
        [_criterion((30, 10), "Needs Improvement", "The program stops with an error on some inputs.")],
        "Your program crashes before it prints the pay.",
        [_exec_error("RUNTIME", "Program Crashes", "major",
                     "The program ends with a runtime_error on the second input.",
                     _output_note("52 18.25", "Gross pay: 1096.88", "(no output)"))]),
        uses_plan=True, hidden_inputs=HIDDEN_INPUTS)

    met = [RequirementResult(requirement_id=r.id, status="met", evidence="Found in main.")
           for r in CHECKLIST.requirements]
    add("req_met", _result(
        [_criterion((30, 30), "Exemplary", "Excellent work. Every part of the assignment is present.")],
        "You met every requirement and the code is easy to read.",
        requirement_results=met), uses_checklist=True)

    add("req_partial", _result(
        [_criterion((30, 24), "Good", "The program works well and handles most of the assignment.")],
        "You are close. One requirement is only partly done.",
        [DetectedError(code="REQ_PARTIAL_R3", name="Incomplete Requirement", severity="minor",
                       description="Pays time and a half for hours over 40 (incomplete)", occurrences=1,
                       notes="R3: overtime is computed for hours over 40 but the rate is not multiplied by 1.5.")],
        requirement_results=[met[0], met[1], RequirementResult(
            requirement_id="R3", status="partial", evidence="overtime is computed but the rate is not multiplied")]),
        uses_checklist=True)

    add("req_missing", _result(
        [_criterion((30, 15), "Needs Improvement", "Two parts of the assignment are missing.")],
        "Your program is a start. A core part of the assignment is not there yet.",
        [DetectedError(code="REQ_MISSING_R2", name="Missing Requirement", severity="major",
                       description="Prints the gross pay with two decimal places (not implemented)",
                       occurrences=1, notes="No fixed or setprecision call anywhere in the program.")],
        requirement_results=[met[0], RequirementResult(requirement_id="R2", status="missing"), met[2]]),
        uses_checklist=True)

    add("compile_failure", _result(
        [_criterion((30, 0), "Needs Improvement", "The program does not compile, so it cannot run.")],
        "Your program does not compile, so none of it could be tested.",
        [DetectedError(code="DOES_NOT_COMPILE", name="Does Not Compile", severity="major",
                       description="The program does not compile.", occurrences=1,
                       notes="Verified by g++: the submission does not compile.", source="compiler",
                       compiler_diagnostics="main.cpp:12:5: error: expected ';' before 'return'")]))

    add("invalid_submission", _result(
        [_criterion((30, 0), "Needs Improvement", "No source code was found in the upload.")],
        "No gradable source file was received for this assignment.",
        validity_status="empty", validity_reason="The upload contained no source files."))

    add("overlap", _result(
        [_criterion((30, 21), "Good", "The program is organized and mostly correct.")],
        "You are close; one problem is reported by two checks.",
        [DetectedError(code="REQ_MISSING_R2", name="Missing Requirement", severity="major",
                       description="Prints the gross pay with two decimal places (not implemented)",
                       occurrences=1, notes="The total is printed as an integer."),
         DetectedError(code="FORMAT", name="Missing Decimal Places", severity="minor",
                       description="The gross pay is printed as a whole number.", occurrences=1,
                       notes="The total is printed as an integer.")],
        requirement_results=[met[0], RequirementResult(requirement_id="R2", status="missing",
                                                       evidence="integer total"), met[2]]),
        uses_checklist=True)

    add("level_band", _result(
        [_criterion((20, 14), "Developing",
                    "Needs Improvement: the loop that totals the items uses a variable called runningTotal "
                    "that is reset on every pass.", cid="logic", name="Logic"),
         _criterion((10, 9), "Proficient", "Good use of functions and clear names.", cid="style", name="Style")],
        "Your structure is good. The totals loop needs one change.",
        overall_band_label="Developing"))
    return out


FIXTURES = _build()
