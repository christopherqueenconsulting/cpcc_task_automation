#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Case validation against the real exec-runner container (PR 13).

Skipped when Docker, its daemon or the image is missing, exactly like
tests/integration/test_program_runner_docker.py. Build the image first:

    docker build -t cpcc_task_automation/exec-runner:2 docker/exec-runner

The solutions here run INSIDE the container only.
"""

import textwrap

import pytest

from cqc_cpcc import execution_check as ec
from cqc_cpcc import test_case_generation as tcg
from cqc_cpcc.utilities import program_runner as pr

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def sandbox(_no_docker_sandbox_unless_asked, monkeypatch):
    monkeypatch.delenv("CQC_EXECUTION", raising=False)
    pr.reset_runner_status_cache()
    status = pr.runner_status()
    if not status.available:
        pytest.skip(f"exec-runner sandbox not usable: {status.reason}. "
                    f"Build it: {' '.join(pr.build_image_command())}")
    yield
    pr.reset_runner_status_cache()


def _solution(code: str) -> ec.SolutionSources:
    return ec.SolutionSources("python", (ec.SourceFile("main.py", textwrap.dedent(code)),))


def _case(cid, stdin, **kw):
    return ec.TestCase(id=cid, stdin=stdin, source="llm", **kw)


PAYROLL = """\
    hours = float(input("Hours: "))
    rate = float(input("Rate: "))
    pay = hours * rate if hours <= 40 else 40 * rate + (hours - 40) * rate * 1.5
    print(f"Pay: {pay:.2f}")
"""


def test_good_and_bad_cases_on_a_real_solution():
    conditions = [ec.Condition(id="C1", text="overtime over 40 hours")]
    cases = [
        _case("T1", "40\n10\n", boundary_of=["C1"]),
        _case("T2", "41\n10\n"),
        _case("T3", "40\n"),          # runs out of input
        _case("T4", "abc\n10\n"),     # crashes on float("abc")
    ]
    result = tcg.validate_cases_on_solution(_solution(PAYROLL), cases, conditions=conditions)
    assert result.ok, result.reason
    assert [c.id for c in result.cases] == ["T1", "T2"]
    assert {d.case_id: d.reason for d in result.dropped} == {"T3": "eof", "T4": "crash"}
    assert result.cases[0].expected_output.strip().endswith("Pay: 400.00")
    assert result.condition_map == {"C1": ["T1"]}
    plan = tcg.build_plan(_solution(PAYROLL), result, conditions)
    assert plan.enabled and plan.uncovered_conditions() == []


def test_a_nondeterministic_solution_case_is_dropped():
    solution = _solution("""\
        import random
        input()
        print(random.random())
    """)
    result = tcg.validate_cases_on_solution(solution, [_case("T1", "x\n")])
    assert result.status == "no_valid_cases"
    assert [(d.case_id, d.reason) for d in result.dropped] == [("T1", "nondeterministic")]


def test_infinite_loop_case_times_out_and_is_dropped():
    solution = _solution("""\
        n = int(input())
        while n != 5:
            pass
        print("done")
    """)
    result = tcg.validate_cases_on_solution(
        solution, [_case("T1", "1\n"), _case("T2", "5\n")])
    assert [d.reason for d in result.dropped] == ["timeout"]
    assert [c.id for c in result.cases] == ["T2"]


def test_solution_that_does_not_compile_turns_the_feature_off():
    java = ec.SolutionSources("java", (ec.SourceFile("Main.java", "public class Main { oops }"),))
    result = tcg.validate_cases_on_solution(java, [_case("T1", "1\n")])
    assert result.status == "solution_compile_failed"
    assert tcg.build_plan(java, result).enabled is False
