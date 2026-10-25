#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""The execution gate against the real exec-runner container (PR 14).

Skipped when Docker, its daemon or the image is missing (never builds or pulls). The programs
below are synthetic and run only inside the container.
"""

import pytest

from cqc_cpcc import execution_gate as eg
from cqc_cpcc import test_case_generation as tcg
from cqc_cpcc.error_definitions_config import get_error_definitions
from cqc_cpcc.execution_check import Condition, SolutionSources, SourceFile
from cqc_cpcc.grading_diagnostics import find_rubric
from cqc_cpcc.rubric_models import CriterionResult, DetectedError, RubricAssessmentResult
from cqc_cpcc.utilities import program_runner as pr

pytestmark = pytest.mark.integration

SOLUTION = "n = int(input())\nprint('Total:', n * 2)\nprint('Done')\n"
RUBRIC = find_rubric("csc_java_exam_rubric")


@pytest.fixture(autouse=True)
def sandbox(_no_docker_sandbox_unless_asked, monkeypatch):
    monkeypatch.delenv("CQC_EXECUTION", raising=False)
    pr.reset_runner_status_cache()
    status = pr.runner_status()
    if not status.available:
        pytest.skip(f"exec-runner sandbox not usable: {status.reason}")
    yield
    pr.reset_runner_status_cache()


def _plan():
    solution = SolutionSources("python", (SourceFile("main.py", SOLUTION),))
    cases = [tcg.manual_case(f"case {n}", f"{n}\n", case_id=f"M{n}", boundary_of=["C1"] if n == 3 else [])
             for n in (1, 3, 7)]
    validation = tcg.validate_cases_on_solution(solution, cases, conditions=[Condition(id="C1", text="n over 2")])
    assert validation.ok, validation
    return tcg.build_plan(solution, validation, [Condition(id="C1", text="n over 2")], all_cases=cases)


def _unscored(*errors):
    return RubricAssessmentResult(
        rubric_id=RUBRIC.rubric_id, rubric_version=RUBRIC.rubric_version,
        total_points_possible=sum(c.max_points for c in RUBRIC.criteria), total_points_earned=0,
        overall_feedback="x", detected_errors=list(errors),
        criteria_results=[CriterionResult(criterion_id=c.criterion_id, criterion_name=c.name,
                                          points_possible=c.max_points, points_earned=None,
                                          selected_level_label=None, feedback="x") for c in RUBRIC.criteria])


def test_a_correct_program_matches_and_the_models_output_errors_are_removed():
    plan = _plan()
    exe = eg.run_student_execution(plan, {"main.py": SOLUTION})
    assert exe.report.status == "ok" and {c.verdict for c in exe.report.cases} == {"match"}
    definitions = get_error_definitions("CSC_251", "Exam1")
    d = next(x for x in definitions if x.error_id.endswith("_OUTPUT_IMPACT_ERROR"))
    model = DetectedError(code=d.error_id, name=d.name, severity=d.severity_category, description="wrong total",
                          source="model")
    result, info = eg.apply_execution_gate(_unscored(model), exe, definitions, reference_solution=SOLUTION)
    assert not result.detected_errors and info["verified"]


def test_a_wrong_program_keeps_the_error_and_a_crash_is_not_a_match():
    plan = _plan()
    wrong = eg.run_student_execution(plan, {"main.py": "n = int(input())\nprint('Total:', n * 3)\nprint('Done')\n"})
    assert {c.verdict for c in wrong.report.cases} == {"values_differ"}
    crash = eg.run_student_execution(plan, {"main.py": SOLUTION + "raise SystemExit(3)\n"})
    assert {c.verdict for c in crash.report.cases} == {"runtime_error"}


def test_a_syntax_error_gives_no_evidence_and_stays_with_the_compile_gate():
    exe = eg.run_student_execution(_plan(), {"main.py": "def broken(:\n"})
    assert exe.report.status == "student_compile_failed" and not exe.has_evidence
