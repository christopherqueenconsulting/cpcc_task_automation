#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Output that legitimately varies: the "values may differ" flag, its detection, comparison and gate.

Synthetic names only. The sandbox is mocked: no Docker, no model.
"""

import glob
import json
from pathlib import Path

import pandas as pd
import pytest

from cqc_cpcc import execution_gate as eg
from cqc_cpcc import test_case_generation as tcg
from cqc_cpcc.execution_check import CaseResult, ExecutionPlan, ExecutionReport, SolutionSources, SourceFile, TestCase
from cqc_cpcc.utilities import output_compare as oc
from cqc_cpcc.utilities import program_runner as pr
from cqc_cpcc.utilities.output_compare import Verdict
from cqc_streamlit_app import execution_view as ev

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #
FLAGGED = [
    ("Print a random number between 1 and 6.", "random"),
    ("The program displays a randomly chosen card.", "random"),
    ("The random number is printed on its own line.", "random"),
    ("Display the current date.", "time"),
    ("It prints today's date and the grade.", "time"),
    ("Show a timestamp next to each result.", "time"),
    ("The output will vary each time you run it.", "varies"),
    ("The values may differ from run to run.", "varies"),
    ("The output is different each time.", "varies"),
    ("Printed results are user-dependent.", "varies"),
]
NOT_FLAGGED = [
    "Choose a random student from the roster and discuss.",  # random, not tied to output
    "Operandom values are printed.",  # inside a word
    "Your output may differ in spacing from the sample.",  # format, not values
    "Do not use random numbers in the output.",  # negated
    "Seed the generator with 42 and print a random number.",  # fixed seed: reproducible
    "Print the date the student entered.",  # a date, not the current one
    "The grades will vary by student.",  # no output tie
    "Prints the total with two decimals.",
    "",
]


@pytest.mark.parametrize("text,code", FLAGGED)
def test_instructions_that_tie_a_varying_value_to_the_output_flag(text, code):
    found = tcg.detect_variable_values(text)
    assert [f.code for f in found if f.flags] == [code]
    assert tcg.variation_reason(found) == found[0].text


@pytest.mark.parametrize("text", NOT_FLAGGED)
def test_other_instructions_never_flag(text):
    assert not [f for f in tcg.detect_variable_values(text) if f.flags]
    assert tcg.variation_reason(tcg.detect_variable_values(text)) == ""


def test_an_unrelated_random_mention_is_only_a_hint():
    [hint] = tcg.detect_variable_values("Choose a random student from the roster and discuss.")
    assert hint.code == "random" and not hint.flags


def test_reasons_are_fixed_text_and_never_echo_the_instructions():
    secret = "Zebra-Quartz-77"
    found = tcg.detect_variable_values(f"Print a random {secret} value.")
    assert found and all(secret not in f.text for f in found)


def test_detection_is_bounded_and_ascii_safe():
    hostile = ("random " * 5000 + "‮\u0000print " * 2000)
    assert isinstance(tcg.detect_variable_values(hostile), list)
    assert tcg.detect_variable_values("print a rаndom number") == []  # a look-alike letter is not "random"


JAVA_DICE = """import java.util.*;
public class Dice {
  public static void main(String[] a) {
    Random r = new Random();
    int roll = r.nextInt(6) + 1;
    System.out.println("Roll: " + roll);
  }
}"""
JAVA_INTERNAL = """import java.util.*;
public class Dice {
  public static void main(String[] a) {
    Random r = new Random();
    int unused = r.nextInt(6);
    System.out.println("Hello");
  }
}"""
PY_DATE = "import datetime\nprint('Today is', datetime.date.today())\n"
PY_COMMENT = "# random.randint is not used\nprint('hi')\n"


def test_a_solution_flags_only_when_the_api_result_reaches_a_print():
    assert [(f.code, f.flags) for f in tcg.detect_variable_values("", JAVA_DICE)] == [("srandom", True)]
    assert [(f.code, f.flags) for f in tcg.detect_variable_values("", PY_DATE)] == [("stime", True)]
    assert [(f.code, f.flags) for f in tcg.detect_variable_values("", JAVA_INTERNAL)] == [("srandom", False)]
    assert tcg.detect_variable_values("", PY_COMMENT) == []
    assert tcg.detect_variable_values("", SolutionSources("python", (SourceFile("a.py", PY_DATE),)))[0].flags


def test_the_false_flag_rate_over_the_eval_datasets_is_pinned():
    """Over every assignment text in evals/datasets: 4 flagged (all true positives), 6 hints, 393 silent."""
    ins_keys = ("instructions", "assignment_instructions", "exam_instructions", "assignment")
    sol_keys = ("solution", "exam_solution", "assignment_solution")
    flagged, hinted, total = [], [], 0
    for path in sorted(glob.glob(str(ROOT / "evals/datasets/**/cases.jsonl"), recursive=True)):
        ds = "/".join(Path(path).parts[-3:-1])
        for line in open(path, encoding="utf-8"):
            row = json.loads(line)
            ins = next((row["inputs"][k] for k in ins_keys if isinstance(row["inputs"].get(k), str)), None)
            if ins is None:
                continue
            sol = next((row["inputs"][k] for k in sol_keys if isinstance(row["inputs"].get(k), str)), None)
            total += 1
            found = tcg.detect_variable_values(ins, sol)
            (flagged if any(f.flags for f in found) else hinted if found else []).append(f"{ds}:{row['case_id']}")
    for case_json in sorted(glob.glob(str(ROOT / "evals/datasets/grading-solution/*/cases/*/case.json"))):
        case = json.load(open(case_json, encoding="utf-8"))
        folder = Path(case_json).parent / "solution"
        solution = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in sorted(folder.glob("*")))
        total += 1
        found = tcg.detect_variable_values(case.get("instructions", ""), solution)
        if any(f.flags for f in found):
            flagged.append(f"grading-solution:{case['case_id']}")
    # The four rows dataset v2 adds on purpose (a dice roll and a timestamp) are the only true positives.
    assert total == 403
    assert flagged == [f"test-case-generation/v2:{k}__{w}" for k in ("dice", "greeting")
                       for w in ("numbered", "prose")]
    assert sorted(hinted) == sorted(
        f"requirement-extraction/v1:{k}__{w}" for k in ("rock_paper_scissors", "number_guess")
        for w in ("numbered", "bulleted", "prose"))


# --------------------------------------------------------------------------- #
# Model fields, plan hash, old plans
# --------------------------------------------------------------------------- #
def test_plan_hash_is_unchanged_for_plans_without_the_flag():
    cases = [TestCase(id="T1", label="a", stdin="5\n", source="llm", boundary_of=["C1"],
                      expected_output="Total: 5\n", validated=True),
             TestCase(id="T2", stdin="7\n", source="manual", expected_output="Total: 7\n", expected_reads=[3],
                      validated=True)]
    from cqc_cpcc.execution_check import Condition
    plan = ExecutionPlan(language="java", solution_sha256="ab" * 32, main="A", cases=cases,
                         conditions=[Condition(id="C1", text="over 5")], condition_map={"C1": ["T1"]},
                         ignore_patterns=["x"])
    # Computed from origin/master's execution_check.py before this change.
    assert plan.plan_hash == "5ad77dffab61e94055b53dc98247902a2cb05db4895ccb6da6c4c8ee28c2661c"
    flagged = plan.model_copy(update={"cases": [cases[0].model_copy(update={"values_may_differ": True}), cases[1]]})
    assert flagged.plan_hash != plan.plan_hash
    marked = plan.model_copy(update={"cases": [cases[0].model_copy(
        update={"values_may_differ": True, "varying_markers": ["Roll:"]}), cases[1]]})
    assert marked.plan_hash not in (plan.plan_hash, flagged.plan_hash)
    reasoned = flagged.model_copy(update={"cases": [flagged.cases[0].model_copy(update={"variance_reason": "x"}),
                                                    cases[1]]})
    assert reasoned.plan_hash == flagged.plan_hash  # the reason text is display only


def test_an_old_saved_plan_loads_with_the_defaults():
    old = {"language": "java", "solution_sha256": "ab", "cases": [{"id": "T1", "stdin": "5\n"}]}
    plan = ExecutionPlan.model_validate(old)
    [case] = plan.cases
    assert case.values_may_differ is False and case.varying_markers == [] and case.variance_reason == ""
    assert not case.fully_unverifiable


def _raw(cases):
    return tcg.TestCaseSuggestion.model_validate({"conditions": [], "cases": cases})


def test_model_output_parses_with_and_without_the_new_fields():
    old = _raw([{"id": "T1", "label": "a", "inputs": ["1"], "boundary_of": []}])
    assert old.cases[0].values_may_differ is False and old.cases[0].varying_markers == []
    new = tcg.normalize_suggestion(_raw([
        {"id": "T1", "label": "dice", "inputs": ["1"], "boundary_of": [], "values_may_differ": True,
         "varying_markers": [" Roll: ", "roll:", "x", "R" * 200, "\u0000\u0001"]},
        {"id": "T2", "label": "b", "inputs": ["2"], "boundary_of": [], "values_may_differ": False,
         "varying_markers": ["ignored"]}]))
    first, second = new.cases
    assert first.values_may_differ and first.varying_markers == ["Roll:", "R" * 60]
    assert not second.values_may_differ and second.varying_markers == []
    cases, _ = tcg.suggestion_to_plan_parts(new)
    assert cases[0].values_may_differ and cases[0].variance_reason == tcg.REASON_MODEL
    assert not cases[1].values_may_differ and cases[1].variance_reason == ""


def test_code_ors_with_the_model_flag_and_keeps_its_markers():
    model = TestCase(id="T1", stdin="1\n", source="llm", values_may_differ=True, varying_markers=["Roll:"])
    plain = TestCase(id="T2", stdin="2\n", source="llm")
    out = tcg.flag_cases([model, plain], "why")
    assert [c.values_may_differ for c in out] == [True, True]
    assert out[0].varying_markers == ["Roll:"] and out[1].varying_markers == []
    assert tcg.flag_cases([plain], "")[0].values_may_differ is False


def test_the_prompt_asks_for_the_flag_only_when_values_vary():
    prompt = tcg.build_test_case_prompt("Roll a die and print it.", "x", "java", "0123456789abcdef")
    assert "values_may_differ" in prompt and "varying_markers" in prompt


# --------------------------------------------------------------------------- #
# Comparator
# --------------------------------------------------------------------------- #
def test_a_flagged_case_with_different_values_is_never_a_difference():
    cmp = oc.compare_output("Roll: 3\n", "Roll: 5\n", values_may_differ=True)
    assert cmp.verdict is Verdict.VALUES_MAY_DIFFER and cmp.diff_excerpt == ""
    assert oc.compare_output("Roll: 3\n", "Roll: 5\n").verdict is Verdict.VALUES_DIFFER  # unflagged: unchanged


def test_a_flagged_case_still_reports_no_output_a_crash_and_a_timeout():
    assert oc.compare_output("Roll: 3\n", "", values_may_differ=True).verdict is Verdict.NO_OUTPUT
    crashed = oc.RunOutcome(exit_code=1)
    assert oc.compare_output("Roll: 3\n", "Roll: 5\n", run=crashed,
                             values_may_differ=True).verdict is Verdict.RUNTIME_ERROR
    assert oc.compare_output("a\n", "b\n", run=oc.RunOutcome(timed_out=True),
                             values_may_differ=True).verdict is Verdict.TIMEOUT


def test_markers_exclude_only_the_varying_lines_and_the_rest_is_compared():
    exp = "Roll: 3\nTotal: 10\n"
    same = oc.compare_output(exp, "roll: 6\nTotal: 10\n", values_may_differ=True, varying_markers=["Roll:"])
    assert same.verdict is Verdict.MATCH and same.detail == oc.VARYING
    other = oc.compare_output(exp, "Roll: 6\nTotal: 11\n", values_may_differ=True, varying_markers=["Roll:"])
    assert other.verdict is Verdict.VALUES_DIFFER and "Roll" not in other.diff_excerpt
    words = oc.compare_output(exp, "Roll: 6\nSum: 10\n", values_may_differ=True, varying_markers=["Roll:"])
    assert words.verdict is Verdict.TEXT_DIFFERS


def test_markers_are_literal_text_never_patterns():
    cmp = oc.compare_output("a+\nx\n", "a+\nx\n", values_may_differ=True, varying_markers=["(a+)+$", "["])
    assert cmp.verdict is Verdict.MATCH


# --------------------------------------------------------------------------- #
# Validation of a case whose solution prints something different each run
# --------------------------------------------------------------------------- #
def _outcome(case_id, nondeterministic):
    return pr.CaseOutcome(id=case_id, status="OK", reason="", exit_code=0, stdout="Roll: 4\n", stderr="",
                          stdout_bytes=8, stderr_bytes=0, stdout_truncated=False, stderr_truncated=False,
                          wall_ms=3, segmented=False, stdin_reads=(), eof_error=False,
                          nondeterministic=nondeterministic)


def _runner(language, files, cases=(), **kw):
    return pr.RunOutcome(status="ok", compile=pr.CompileOutcome(ok=True),
                         cases=tuple(_outcome(c.id, True) for c in cases))


def test_a_flagged_case_survives_the_solutions_two_different_runs_and_an_unflagged_one_does_not():
    solution = SolutionSources("java", (SourceFile("Dice.java", JAVA_DICE),))
    flagged = TestCase(id="T1", stdin="1\n", source="llm", values_may_differ=True, varying_markers=["Roll:"])
    plain = TestCase(id="T2", stdin="2\n", source="llm")
    result = tcg.validate_cases_on_solution(solution, [flagged, plain], runner=_runner)
    assert [c.id for c in result.cases] == ["T1"] and result.cases[0].validated
    assert [d.case_id for d in result.dropped] == ["T2"]
    assert any("values may differ" in w for w in result.warnings)


# --------------------------------------------------------------------------- #
# Gate
# --------------------------------------------------------------------------- #
class _Runner:
    def __init__(self, outputs):
        self.outputs = outputs

    def __call__(self, language, files, cases=(), **kw):
        outs = [pr.CaseOutcome(id=c.id, status="OK", reason="", exit_code=0, stdout=self.outputs[c.id], stderr="",
                               stdout_bytes=1, stderr_bytes=0, stdout_truncated=False, stderr_truncated=False,
                               wall_ms=1, segmented=False, stdin_reads=(), eof_error=False, nondeterministic=False)
                for c in cases]
        return pr.RunOutcome(status="ok", compile=pr.CompileOutcome(ok=True), cases=tuple(outs))


def _plan(flag_ids=(), markers=()):
    cases = [TestCase(id=f"T{i}", label=f"Case {i}", stdin=f"{i}\n", source="llm", validated=True,
                      expected_output=f"Roll: 3\nTotal: {i}\n",
                      values_may_differ=f"T{i}" in flag_ids, varying_markers=list(markers) if f"T{i}" in flag_ids else [])
             for i in (1, 2, 3)]
    solution = SolutionSources("java", (SourceFile("Dice.java", JAVA_DICE),))
    return ExecutionPlan(language="java", solution_sha256=solution.sha256, solution_files=solution.solution_files(),
                         cases=cases)


@pytest.fixture
def sandbox(monkeypatch):
    monkeypatch.delenv("CQC_EXECUTION", raising=False)
    monkeypatch.setattr(pr, "runner_status", lambda: pr.RunnerStatus(True))


def _run(plan, outputs):
    return eg.run_student_execution(plan, {"Dice.java": "class Dice {}"}, runner=_Runner(outputs))


def _result():
    from tests.unit.test_execution_gate import unscored
    return unscored()


def test_flagged_cases_with_different_random_lines_get_the_new_verdict_and_no_deduction(sandbox):
    plan = _plan(flag_ids={"T1", "T2", "T3"})
    exe = _run(plan, {f"T{i}": f"Roll: 6\nTotal: {i + 9}\n" for i in (1, 2, 3)})
    assert {r.verdict for r in exe.report.cases} == {"values_may_differ"}
    out, info = eg.apply_execution_gate(_result(), exe, [])
    assert not info["disagrees"] and info["action"] == "evidence" and not out.detected_errors
    gate = out.execution_report["gate"]
    assert gate["mode"] == "values_may_differ" and gate["values_may_differ"] == ["T1", "T2", "T3"]
    assert any("not compared" in n.reason for n in out.reconciliation)


def test_an_unflagged_difference_still_disagrees(sandbox):
    plan = _plan()
    exe = _run(plan, {f"T{i}": f"Roll: 6\nTotal: {i + 9}\n" for i in (1, 2, 3)})
    assert {r.verdict for r in exe.report.cases} == {"values_differ"}
    _, info = eg.apply_execution_gate(_result(), exe, [])
    assert info["disagrees"]


def test_a_mixed_plan_compares_the_unflagged_cases_and_notes_the_flagged_one(sandbox):
    plan = _plan(flag_ids={"T1"})
    outs = {"T1": "Roll: 6\nTotal: 99\n", "T2": "Roll: 3\nTotal: 2\n", "T3": "Roll: 3\nTotal: 3\n"}
    exe = _run(plan, outs)
    assert [r.verdict for r in exe.report.cases] == ["values_may_differ", "match", "match"]
    out, info = eg.apply_execution_gate(_result(), exe, [])
    assert not info["disagrees"] and out.execution_report["gate"]["mode"] == "match"
    assert any("Values may differ on 1 of 3" in n.reason for n in out.reconciliation)


def test_marker_lines_are_skipped_but_other_lines_still_count(sandbox):
    plan = _plan(flag_ids={"T1", "T2", "T3"}, markers=["Roll:"])
    exe = _run(plan, {f"T{i}": f"Roll: 6\nTotal: {i + 9}\n" for i in (1, 2, 3)})
    assert {r.verdict for r in exe.report.cases} == {"values_differ"}  # Total lines really differ
    ok = _run(plan, {f"T{i}": f"Roll: 6\nTotal: {i}\n" for i in (1, 2, 3)})
    assert {r.verdict for r in ok.report.cases} == {"match"}


def test_fully_flagged_cases_do_not_count_as_independent_evidence():
    plan = _plan(flag_ids={"T1", "T2", "T3"})
    assert plan.independent_cases() == [] and plan.sample_only
    assert len(_plan(flag_ids={"T1"}, markers=["Roll:"]).independent_cases()) == 3


# --------------------------------------------------------------------------- #
# Setup table
# --------------------------------------------------------------------------- #
def test_the_table_shows_the_flag_and_the_instructor_can_toggle_it():
    frame = ev.cases_frame([TestCase(id="M1", stdin="5\n", source="manual", values_may_differ=True, validated=True)])
    assert frame.loc[0, "May differ"] == True and frame.loc[0, "Status"] == "Ready (values may differ)"  # noqa: E712
    base = [TestCase(id="T1", stdin="5\n", source="llm", values_may_differ=True, varying_markers=["Roll:"],
                     variance_reason="why", validated=True, expected_output="x")]
    edited = frame.copy()
    edited.loc[0, "id"] = "T1"
    edited.loc[0, "May differ"] = False
    [off] = ev.fold_editor(base, edited)
    assert off.values_may_differ is False and off.varying_markers == [] and off.variance_reason == ""
    edited.loc[0, "May differ"] = True
    [on] = ev.fold_editor([off], edited)
    assert on.values_may_differ and on.variance_reason == ev.MARKED_BY_YOU
    added = pd.DataFrame([{"Use": True, "Label": "mine", "Input": "3", "May differ": False, "id": ""}])
    [new] = ev.fold_editor([], added, "because")
    assert new.values_may_differ and new.variance_reason == "because"


# --------------------------------------------------------------------------- #
# Eval suite (report-only grader, dataset v2)
# --------------------------------------------------------------------------- #
def test_the_report_only_grader_and_the_v2_dataset():
    from types import SimpleNamespace

    from cqc_cpcc.model_eval import suite_datasets
    from cqc_cpcc.model_eval.suites import stdin_cases

    assert stdin_cases.SUITE.dataset == "test-case-generation/v2"
    assert "values_may_differ" not in stdin_cases.SUITE.weights
    rows = suite_datasets.load_rows("test-case-generation")
    assert len(rows) == 16 and [r["case_id"] for r in rows[12:]] == [
        "dice__numbered", "dice__prose", "greeting__numbered", "greeting__prose"]
    assert all("varies" in r["tags"] for r in rows[12:]) and not any("varies" in r["tags"] for r in rows[:12])
    v1 = [json.loads(line) for line in (ROOT / "evals/datasets/test-case-generation/v1/cases.jsonl")
          .read_text(encoding="utf-8").splitlines()]
    assert rows[:12] == v1  # v1 stays frozen and is the first 12 rows of v2
    varies, stable = SimpleNamespace(tags=["varies"]), SimpleNamespace(tags=["numbered"])
    marked = {"cases": [{"values_may_differ": True}, {"values_may_differ": False}]}
    none = {"cases": [{"values_may_differ": False}]}
    grade = stdin_cases._values_may_differ
    assert (grade(varies, marked), grade(varies, none), grade(stable, none), grade(stable, marked)) == (
        1.0, 0.0, 1.0, 0.0)
    suggestion = tcg.normalize_suggestion(_raw([{"id": "T1", "label": "a", "inputs": ["1"], "boundary_of": [],
                                                "values_may_differ": True, "varying_markers": ["Roll:"]}]))
    payload = stdin_cases.to_payload(suggestion)
    assert payload["cases"][0]["values_may_differ"] is True and payload["cases"][0]["varying_markers"] == ["Roll:"]


# --------------------------------------------------------------------------- #
# Hostile input: every detector regex stays linear
# --------------------------------------------------------------------------- #
HOSTILE = [
    "!" * 100000,
    "random " * 50000,
    ". " * 50000,
    "?" * 100000 + "x",
    " " * 100000 + "random print",
    "may " * 30000,
    "print " + "a[" * 30000,
    "\n" * 100000,
]


@pytest.mark.parametrize("text", HOSTILE, ids=range(len(HOSTILE)))
def test_the_detector_is_linear_on_hostile_instructions_and_solutions(text):
    import time

    start = time.perf_counter()
    tcg.detect_variable_values(text)
    tcg.detect_variable_values("", text)
    tcg.detect_variable_values("", "/*" * 50000)
    tcg.detect_variable_values("", "x = random.randint(1, 6)\n" + "print (" + " " * 50000 + "x\n" * 2000)
    assert time.perf_counter() - start < 1.0


def test_marker_cleaning_skips_non_strings_and_caps_the_count():
    assert tcg.clean_markers(None) == []
    assert tcg.clean_markers([5, None, "a", "ok"]) == ["ok"]
    assert len(tcg.clean_markers([f"marker {i}" for i in range(20)])) == tcg.MAX_MARKERS


def test_block_comments_are_removed_without_regex_and_an_open_one_ends_the_text():
    text = "a /* hide random.randint( */ b // tail\n# py\n#include <random>\nc /* open"
    out = tcg._strip_comments(text)
    assert "hide" not in out and "tail" not in out and "py" not in out and "#include <random>" in out
    assert out.rstrip().endswith("c")
    # A commented-out API call is not a finding.
    assert tcg.detect_variable_values("", "/* x = random.randint(1,2) */\nprint('hi')\n") == []
