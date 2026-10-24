#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Requirement / model-error overlap (Design A): classifier, pairing, modes, Reinstate, run key."""

import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from cqc_cpcc import reconciliation as rec
from cqc_cpcc import requirement_coverage as rc
from cqc_cpcc import requirement_overlap as ro
from cqc_cpcc.grading_diagnostics import find_rubric
from cqc_cpcc.grading_run_key import generate_grading_run_key
from cqc_cpcc.model_eval import dataset as ds
from cqc_cpcc.rubric_grading import apply_backend_scoring, grade_with_rubric
from cqc_cpcc.rubric_models import (CriterionResult, DetectedError, RequirementResult, RubricAssessmentResult)

pytestmark = pytest.mark.unit

RUBRIC = find_rubric("csc_java_exam_rubric")

FORMAT = "CSC_151_EXAM_1_OUTPUT_FORMATTING"          # minor
IMPACT = "CSC_151_EXAM_1_OUTPUT_IMPACT_ERROR"        # major
SEQ = "CSC_151_EXAM_1_SEQUENCE_AND_SELECTION_ERROR"  # major
NAMING = "CSC_151_EXAM_1_NAMING_CONVENTION"          # minor
SCANNER = "CSC_151_EXAM_1_SCANNER_CLASS"             # minor
_SEV = {FORMAT: "minor", IMPACT: "major", SEQ: "major", NAMING: "minor", SCANNER: "minor"}

CHECKLIST = rc.RequirementChecklist(requirements=[
    rc.RequirementItem(id="R1", text="Read the price and quantity with a Scanner", weight="core"),
    rc.RequirementItem(id="R2", text="Compute the subtotal by multiplying price by quantity", weight="core"),
    rc.RequirementItem(id="R3", text="Print the total with two decimal places", weight="secondary"),
    rc.RequirementItem(id="R4", text="Reject negative input with an error message", weight="core"),
    rc.RequirementItem(id="R5", text="Define a method named calculateTotal", weight="core"),
    rc.RequirementItem(id="R6", text="Use String methods rather than regular expressions", weight="secondary"),
])


def _err(code, severity=None, source=None):
    if rc.is_requirement_error(code):
        severity = severity or ("major" if code.startswith("MISSING") else "minor")
    return DetectedError(code=code, name=code, severity=severity or _SEV.get(code, "minor"),
                         description=code, occurrences=1, source=source)


def _result(*errors):
    return RubricAssessmentResult(
        rubric_id=RUBRIC.rubric_id, rubric_version=RUBRIC.rubric_version,
        total_points_possible=sum(c.max_points for c in RUBRIC.criteria), total_points_earned=0,
        overall_feedback="x", detected_errors=list(errors),
        criteria_results=[CriterionResult(criterion_id=c.criterion_id, criterion_name=c.name,
                                          points_possible=c.max_points, points_earned=None,
                                          selected_level_label=None, feedback="x") for c in RUBRIC.criteria])


def _score(result):
    return float(apply_backend_scoring(RUBRIC, result).total_points_earned)


def _codes(result):
    return sorted(e.code for e in result.detected_errors)


def _run(errors, mode, checklist=CHECKLIST):
    return ro.reconcile_overlaps(_result(*errors), checklist, None, mode)


# --------------------------------------------------------------------------- classifier

KIND_TABLE = [
    # CSC151 style
    ("Print the total with two decimal places", "format"),
    ("Display the average to one decimal place and display the letter grade", "format"),
    ("Print the monthly payment and total amount paid as currency", "format"),
    ("Calculate and print the shipping cost with two decimal places", "format"),
    ("When the user asks to see the balance, print it with two decimal places", "format"),
    ("Show a menu with deposit, withdraw, balance, and quit options", "format"),
    ("Tell the user whether each guess is too high or too low", "format"),
    ("Use a Scanner to read an item price as a double and a quantity as an int", "input"),
    ("Read the loan amount, annual interest rate, and loan term in years", "input"),
    ("Prompt for and read the hours worked and hourly pay rate", "input"),
    ("Ask the user for a temperature and a unit, C or F", "input"),
    ("Let the player choose rock, paper, or scissors", "input"),
    ("Compute the subtotal by multiplying the price by the quantity", "calc"),
    ("Calculate the average of the five scores", "calc"),
    ("Count the number of vowels in the line", "calc"),
    ("Convert Celsius to Fahrenheit or Fahrenheit to Celsius based on the unit", "calc"),
    ("Generate a random number from 1 to 100", "calc"),
    ("Use Math.pow to calculate exponents", "calc"),
    ("Add 7% sales tax to the discounted subtotal", "calc"),
    ("Apply a 10% discount to the subtotal when the quantity is 10 or more", "select"),
    ("Add a $5 surcharge when the package weighs more than 20 pounds", "select"),
    ("Determine the letter grade (A, B, C, D, or F) from the average", "select"),
    ("Look up the rate per pound for the selected zone", "select"),
    ("Use a switch statement where it fits", "select"),
    ("Withdraw an amount only when the balance is large enough", "select"),
    ("Reject any zero or negative input and display an error message", "valid"),
    ("Validate that hours are between 0 and 80 and the rate is greater than 0", "valid"),
    ("Re-prompt until the hours are between 0 and 80", "valid"),
    ("Print an error message if the unit is not C or F", "valid"),
    ("Refuse to reserve a seat that is already taken", "valid"),
    ("Keep prompting until the rate is greater than 0", "valid"),
    # CSC134 style
    ("Declare, define, and call a function named calculatePay that takes hours and rate", "func"),
    ("Define functions for searching and listing products", "func"),
    ("Implement each menu choice as a function", "func"),
    ("Use a separate function for each menu choice", "func"),
    ("Define a method named calculateTotal", "func"),
    ("Use a do-while loop for the menu", "loop"),
    ("Use a while loop to repeat the guessing process", "loop"),
    ("Repeat rounds until the player chooses to quit", "loop"),
    ("Ask the user to guess repeatedly until they guess the number correctly", "loop"),
    # CSC251 style
    ("Store the seating chart in a two-dimensional array with 5 rows and 4 columns", "data"),
    ("Initialize the account balance to $100", "data"),
    ("Start the account with a balance of $100", "data"),
    ("Display the invoice total with a dollar sign and a comma separator", "format"),
    # left alone
    ("Use String methods rather than regular expressions to process the line", None),
    ("Do not perform additional input validation", None),
    ("Do not validate anything beyond what the assignment explicitly requires", None),
    ("", None),
]


@pytest.mark.parametrize("text,kind", KIND_TABLE)
def test_requirement_kind_table(text, kind):
    assert ro.requirement_kind(text) == kind


def test_the_table_has_at_least_40_phrasings_and_every_kind():
    assert len(KIND_TABLE) >= 40
    assert {k for _, k in KIND_TABLE if k} == set(ro.ALL_KINDS)


FALLBACK_TABLE = [
    ("The program should display a menu", "format"),
    ("The program reads from the keyboard", "input"),
    ("Use an else branch for everything else", "select"),
    ("The program must compute a formula", "calc"),
    ("The program needs a variable for the rate", "data"),
    ("Use a constant for the tax rate", "data"),
    ("Select a random card", "calc"),
    ("Use a Scanner object", "input"),
    ("Use a switch", "select"),
    ("Use Math.pow for the exponent", "calc"),
    ("The weather is nice", None),
]


@pytest.mark.parametrize("text,kind", FALLBACK_TABLE)
def test_requirement_kind_keyword_fallback(text, kind):
    assert ro.requirement_kind(text) == kind


def test_the_info_serialises_for_the_gate_report():
    _, _, info = _run([_err(FORMAT), _err("PARTIAL_REQUIREMENT_R3")], "notes")
    d = info.as_dict()
    assert d["mode"] == "notes" and d["matched"] == [["R3", "PARTIAL_REQUIREMENT_R3", FORMAT]]
    assert d["requirements"] == 1 and d["classified"] == 1 and d["dropped"] == []


def test_the_classifier_ignores_case_hyphens_and_curly_quotes():
    assert ro.requirement_kind("RE-PROMPT until valid") == "valid"
    assert ro.requirement_kind("Print the product’s quantity if found") == "format"


ERROR_TABLE = [
    ("CSC_151_EXAM_1_OUTPUT_FORMATTING", {"format"}),
    ("CSC_251_EXAM_1_DECIMAL_SCALE", {"format"}),
    ("CSC_251_EXAM_2_MINOR_FORMATTING", {"format"}),
    ("CSC_134_PROJECT_1_MAJOR_FORMATTING", {"format"}),
    ("CSC_134_PROJECT_1_CALCULATION_ERROR", {"calc", "select"}),
    ("CSC_151_EXAM_1_OUTPUT_IMPACT_ERROR", {"calc", "select"}),
    ("CSC_151_EXAM_1_SEQUENCE_AND_SELECTION_ERROR", {"select", "calc", "loop"}),
    ("CSC_134_PROJECT_1_SEQUENCE_SELECTION_ERROR", {"select", "calc", "loop"}),
    ("CSC_134_PROJECT_1_INPUT_VALIDATION", {"valid"}),
    ("CSC_134_PROJECT_1_FUNCTION_PROTOTYPE_ERROR", {"func"}),
    ("CSC_151_EXAM_2_METHOD_ERRORS", {"func"}),
    ("CSC_151_EXAM_1_SCANNER_CLASS", {"input"}),
    ("CSC_134_PROJECT_1_INCORRECT_DATA_TYPE", {"input", "data"}),
    ("CSC_151_EXAM_1_NAMING_CONVENTION", set()),
    ("CSC_151_EXAM_1_INSUFFICIENT_DOCUMENTATION", set()),
    ("CSC_134_PROJECT_1_CONSTANTS_ERROR", set()),
    ("CSC_134_PROJECT_1_DOES_NOT_COMPILE", set()),
    ("CSC_151_EXAM_1_SYNTAX_ERROR", set()),
    ("CSC_251_EXAM_1_ARRAYLIST_ERRORS", set()),
    ("MISSING_REQUIREMENT_R1", set()),
    ("PARTIAL_REQUIREMENT_R1", set()),
    ("NO_SUBMISSION", set()),
]


@pytest.mark.parametrize("code,kinds", ERROR_TABLE)
def test_error_kinds_table(code, kinds):
    assert ro.error_kinds(code) == frozenset(kinds)


def test_a_custom_error_is_read_from_its_description_but_style_codes_never_are():
    class Defn:
        description = "Rounds the output to the wrong decimal places"
    assert ro.error_kinds("CUSTOM_ROUNDING", Defn()) == {"format"}
    assert ro.error_kinds("CUSTOM_NAMING_CONVENTION", Defn()) == frozenset()
    assert ro.error_kinds("CUSTOM_ROUNDING") == frozenset()


# --------------------------------------------------------------------------- pairing

def test_a_tie_keeps_the_requirement_error_and_drops_the_model_error():
    result, notes, info = _run([_err(FORMAT), _err("PARTIAL_REQUIREMENT_R3")], "drop")
    assert _codes(result) == ["PARTIAL_REQUIREMENT_R3"]
    [n] = notes
    assert (n.stage, n.action, n.code, n.kept_code, n.requirement_id, n.suppressed) == (
        "overlap", "removed", FORMAT, "PARTIAL_REQUIREMENT_R3", "R3", True)
    assert n.error.code == FORMAT
    assert info.dropped == [FORMAT] and info.matched == [("R3", "PARTIAL_REQUIREMENT_R3", FORMAT)]


def test_a_major_model_error_beats_a_minor_requirement_error():
    result, notes, _ = _run([_err(IMPACT), _err("PARTIAL_REQUIREMENT_R2")], "drop")
    assert _codes(result) == [IMPACT]
    assert notes[0].code == "PARTIAL_REQUIREMENT_R2" and notes[0].kept_code == IMPACT


def test_a_missing_requirement_error_is_never_dropped():
    # A major requirement error ties a major model error: the requirement stays.
    result, _, _ = _run([_err(IMPACT), _err("MISSING_REQUIREMENT_R2")], "drop")
    assert _codes(result) == ["MISSING_REQUIREMENT_R2"]
    # A lesser model error never outranks it either.
    result, _, _ = _run([_err(FORMAT), _err("MISSING_REQUIREMENT_R3")], "drop")
    assert _codes(result) == ["MISSING_REQUIREMENT_R3"]


def test_no_missing_requirement_error_is_dropped_in_any_combination():
    model = [FORMAT, IMPACT, SEQ, SCANNER]
    for r in range(1, 4):
        for chosen in itertools.combinations(model, r):
            for statuses in itertools.product(("MISSING", "PARTIAL"), repeat=5):
                reqs = [_err(f"{s}_REQUIREMENT_R{i + 1}") for i, s in enumerate(statuses)]
                _, _, info = _run([_err(c) for c in chosen] + reqs, "drop")
                assert not [c for c in info.dropped if c.startswith("MISSING_REQUIREMENT")]


def test_two_candidate_model_errors_for_one_requirement_are_only_a_note():
    result, notes, info = _run([_err(IMPACT), _err(SEQ), _err("PARTIAL_REQUIREMENT_R2")], "drop")
    assert _codes(result) == sorted([IMPACT, SEQ, "PARTIAL_REQUIREMENT_R2"])
    assert [(n.action, n.suppressed) for n in notes] == [("note", False)]
    assert "Possible overlap" in notes[0].reason and info.ambiguous == ["R2"] and info.dropped == []


def test_one_model_error_for_two_requirements_is_only_a_note():
    # R2 (calc) and R5 is func; use two calc requirements instead.
    checklist = rc.RequirementChecklist(requirements=[
        rc.RequirementItem(id="R1", text="Compute the subtotal", weight="core"),
        rc.RequirementItem(id="R2", text="Calculate the average", weight="core")])
    result, notes, info = _run([_err(IMPACT), _err("PARTIAL_REQUIREMENT_R1"), _err("PARTIAL_REQUIREMENT_R2")],
                               "drop", checklist)
    assert len(result.detected_errors) == 3 and info.dropped == []
    assert {n.requirement_id for n in notes} == {"R1", "R2"} and all(n.action == "note" for n in notes)


def test_pairing_is_one_to_one_one_error_never_pays_for_two_requirements():
    result, _, info = _run([_err(FORMAT), _err(SCANNER), _err("PARTIAL_REQUIREMENT_R3"),
                            _err("PARTIAL_REQUIREMENT_R1")], "drop")
    # FORMAT pairs only with R3 and SCANNER only with R1: each used once.
    assert sorted(info.dropped) == [FORMAT, SCANNER]
    assert _codes(result) == ["PARTIAL_REQUIREMENT_R1", "PARTIAL_REQUIREMENT_R3"]


def test_an_unclassified_requirement_and_an_unmapped_error_are_left_alone():
    result, notes, _ = _run([_err(IMPACT), _err(NAMING), _err("PARTIAL_REQUIREMENT_R6")], "drop")
    assert len(result.detected_errors) == 3 and notes == []


def test_an_error_of_another_kind_is_not_paired():
    result, notes, _ = _run([_err(FORMAT), _err("PARTIAL_REQUIREMENT_R2")], "drop")
    assert len(result.detected_errors) == 2 and notes == []


def test_compiler_and_execution_errors_and_does_not_compile_are_never_dropped():
    for source in ("compiler", "execution"):
        result, notes, _ = _run([_err(FORMAT, source=source), _err("PARTIAL_REQUIREMENT_R3")], "drop")
        assert len(result.detected_errors) == 2 and notes == [], source
    dnc = DetectedError(code="CSC_151_EXAM_1_DOES_NOT_COMPILE", name="Does Not Compile", severity="major",
                        description="x", occurrences=1)
    result, notes, _ = ro.reconcile_overlaps(_result(dnc, _err("PARTIAL_REQUIREMENT_R2")), CHECKLIST, None, "drop")
    assert len(result.detected_errors) == 2 and notes == []


def test_a_requirement_error_whose_id_is_not_in_the_checklist_is_ignored():
    result, notes, _ = _run([_err(FORMAT), _err("PARTIAL_REQUIREMENT_R99")], "drop")
    assert len(result.detected_errors) == 2 and notes == []


def test_no_checklist_is_a_no_op():
    result, notes, info = ro.reconcile_overlaps(_result(_err(FORMAT)), None, None, "drop")
    assert _codes(result) == [FORMAT] and notes == [] and info.dropped == []


# --------------------------------------------------------------------------- modes

PAIRS = [_err(FORMAT), _err("PARTIAL_REQUIREMENT_R3")]


def test_notes_mode_changes_no_error_and_no_score_but_explains_the_overlap():
    master = _result(*PAIRS)
    result, notes, info = ro.reconcile_overlaps(master, CHECKLIST, None, "notes")
    assert result.detected_errors == master.detected_errors
    assert _score(result) == _score(master) == 180
    [n] = notes
    assert n.action == "note" and not n.suppressed and "not applied" in n.reason
    assert n.code == FORMAT and n.kept_code == "PARTIAL_REQUIREMENT_R3" and info.dropped == []


def test_drop_mode_scores_as_simulated():
    master = _result(*PAIRS)
    assert _score(master) == 180  # two distinct minors
    dropped, _, _ = ro.reconcile_overlaps(master, CHECKLIST, None, "drop")
    assert _score(dropped) == 190  # counted once
    major = _result(_err(IMPACT), _err("MISSING_REQUIREMENT_R2"))
    assert _score(major) == 140  # two majors
    after, _, _ = ro.reconcile_overlaps(major, CHECKLIST, None, "drop")
    assert _score(after) == 160  # one major, the requirement kept


def test_off_mode_does_nothing():
    master = _result(*PAIRS)
    result, notes, info = ro.reconcile_overlaps(master, CHECKLIST, None, "off")
    assert result is master and notes == [] and info.dropped == []


def test_an_unknown_mode_falls_back_to_the_environment_default(monkeypatch):
    monkeypatch.delenv(ro.ENV_VAR, raising=False)
    assert ro.overlap_mode() == "notes"
    for raw, want in (("DROP", "drop"), (" off ", "off"), ("notes", "notes"), ("banana", "notes"), ("", "notes")):
        monkeypatch.setenv(ro.ENV_VAR, raw)
        assert ro.overlap_mode() == want, raw


def test_the_stage_is_idempotent_and_deterministic():
    master = _result(*PAIRS, _err(IMPACT), _err("PARTIAL_REQUIREMENT_R2"))
    first, notes1, _ = ro.reconcile_overlaps(master, CHECKLIST, None, "drop")
    again, notes2, _ = ro.reconcile_overlaps(master, CHECKLIST, None, "drop")
    assert first.detected_errors == again.detected_errors and notes1 == notes2
    twice, notes3, info3 = ro.reconcile_overlaps(first, CHECKLIST, None, "drop")
    assert twice.detected_errors == first.detected_errors and notes3 == [] and info3.dropped == []


def _synthetic_results():
    """Every labelled case of the grading dataset, every allowed requirement-status combination,
    built the way the production pipeline builds them: model errors, then coverage."""
    out = []
    for case in ds.load_cases():
        checklist = ds.checklist_model(case)
        if not checklist or case.gated:
            continue
        defs = {d.error_id: d for d in ds.error_definitions(case.course_id, case.assignment_id)}
        rubric = ds.rubric(case.rubric_id)
        ids = sorted(case.requirements)
        combos = [dict(zip(ids, c)) for c in itertools.product(*(case.requirements[i] for i in ids))] or [{}]
        for statuses in combos:
            model = [DetectedError(code=e, name=defs[e].name, severity=defs[e].severity_category,
                                   description=defs[e].description, occurrences=1, source="model")
                     for e in sorted(case.error_ids | case.acceptable_error_ids) if e in defs]
            result = RubricAssessmentResult(
                rubric_id=rubric.rubric_id, rubric_version=rubric.rubric_version,
                total_points_possible=rubric.total_points_possible, total_points_earned=0,
                criteria_results=[CriterionResult(criterion_id=c.criterion_id, criterion_name=c.name,
                                                  points_possible=c.max_points, points_earned=0, feedback="-")
                                  for c in rubric.criteria if c.enabled],
                overall_feedback="-", detected_errors=model,
                requirement_results=[RequirementResult(requirement_id=r.id, status=statuses.get(r.id, "met"))
                                     for r in checklist.requirements])
            result, _ = rc.apply_requirement_coverage(result, checklist)
            out.append((case, rubric, defs, checklist, result))
    return out


def test_across_the_synthetic_set_notes_mode_equals_master_and_drop_never_lowers():
    synthetic = _synthetic_results()
    assert len(synthetic) > 100
    changed = 0
    for case, rubric, defs, checklist, master in synthetic:
        base = float(apply_backend_scoring(rubric, master).total_points_earned)
        noted, _, _ = ro.reconcile_overlaps(master, checklist, list(defs.values()), "notes")
        assert noted.detected_errors == master.detected_errors
        assert float(apply_backend_scoring(rubric, noted).total_points_earned) == base, case.case_id
        off, offnotes, _ = ro.reconcile_overlaps(master, checklist, list(defs.values()), "off")
        assert off is master and offnotes == []
        dropped, _, info = ro.reconcile_overlaps(master, checklist, list(defs.values()), "drop")
        after = float(apply_backend_scoring(rubric, dropped).total_points_earned)
        assert after >= base, (case.case_id, base, after)
        assert not [c for c in info.dropped if c.startswith("MISSING_REQUIREMENT")]
        changed += after != base
    assert changed > 0  # the stage does something on the synthetic set


# --------------------------------------------------------------------------- pipeline

def _unscored(*errors, requirement_results=None):
    return _result(*errors).model_copy(update={"requirement_results": requirement_results})


async def _grade(mocker, model_result, checklist=CHECKLIST):
    mocker.patch("cqc_cpcc.rubric_grading.llm_gateway.structured", new_callable=AsyncMock,
                 return_value=model_result)
    return await grade_with_rubric(rubric=RUBRIC, assignment_instructions="Write a program.",
                                   student_submission="class Main {}", model_name="openai/gpt-6-luna",
                                   requirements=checklist)


def _verdicts(**kw):
    return [RequirementResult(requirement_id=r.id, status=kw.get(r.id, "met")) for r in CHECKLIST.requirements]


@pytest.mark.asyncio
async def test_the_pipeline_default_scores_exactly_as_before_and_leaves_notes(mocker, monkeypatch):
    monkeypatch.delenv(ro.ENV_VAR, raising=False)
    model = _unscored(_err(FORMAT), requirement_results=_verdicts(R3="partial"))
    result = await _grade(mocker, model)
    assert sorted(e.code for e in result.detected_errors) == sorted([FORMAT, "PARTIAL_REQUIREMENT_R3"])
    assert result.total_points_earned == 180
    overlap = [n for n in result.reconciliation if n.stage == "overlap"]
    assert len(overlap) == 1 and overlap[0].action == "note"


@pytest.mark.asyncio
async def test_the_pipeline_in_drop_mode_scores_once_and_reinstate_restores(mocker, monkeypatch):
    monkeypatch.setenv(ro.ENV_VAR, "drop")
    model = _unscored(_err(FORMAT), requirement_results=_verdicts(R3="partial"))
    result = await _grade(mocker, model)
    assert [e.code for e in result.detected_errors] == ["PARTIAL_REQUIREMENT_R3"]
    assert result.total_points_earned == 190
    idx = next(i for i, n in enumerate(result.reconciliation) if n.stage == "overlap")
    assert result.reconciliation[idx].suppressed
    back = rec.reinstate(result, RUBRIC, idx)
    assert sorted(e.code for e in back.detected_errors) == sorted([FORMAT, "PARTIAL_REQUIREMENT_R3"])
    assert back.total_points_earned == 180  # what master scored
    assert back.reconciliation[idx].reinstated


@pytest.mark.asyncio
async def test_the_pipeline_in_off_mode_adds_no_overlap_note(mocker, monkeypatch):
    monkeypatch.setenv(ro.ENV_VAR, "off")
    model = _unscored(_err(FORMAT), requirement_results=_verdicts(R3="partial"))
    result = await _grade(mocker, model)
    assert result.total_points_earned == 180
    assert not [n for n in result.reconciliation if n.stage == "overlap"]


@pytest.mark.asyncio
async def test_a_gate_added_compiler_error_is_not_touched(mocker, monkeypatch):
    monkeypatch.setenv(ro.ENV_VAR, "drop")
    compiler_err = _err(FORMAT, source="compiler")
    model = _unscored(requirement_results=_verdicts(R3="partial"))

    def gate(result, source_files, error_definitions=None):
        return (result.model_copy(update={"detected_errors": [compiler_err]}),
                {"ran": True, "action": "added", "language": "java", "compiles": False, "tool": "javac"})
    mocker.patch("cqc_cpcc.rubric_grading.apply_compile_gate", gate)
    mocker.patch("cqc_cpcc.rubric_grading.llm_gateway.structured", new_callable=AsyncMock, return_value=model)
    result = await grade_with_rubric(rubric=RUBRIC, assignment_instructions="x", student_submission="class Main {}",
                                     model_name="openai/gpt-6-luna", requirements=CHECKLIST,
                                     source_files={"Main.java": "/no/such/file"})
    assert FORMAT in [e.code for e in result.detected_errors]
    assert not [n for n in result.reconciliation if n.stage == "overlap"]


# --------------------------------------------------------------------------- run key

BASE_KEY = dict(course_id="CSC151", assignment_id="Exam1", rubric_id="r", rubric_version=1)


def test_the_run_key_is_unchanged_in_notes_and_off_and_changes_in_drop(monkeypatch):
    plain = generate_grading_run_key(**BASE_KEY)
    for mode in ("notes", "off", "banana"):
        monkeypatch.setenv(ro.ENV_VAR, mode)
        assert ro.run_key_overlap() is None
        assert generate_grading_run_key(**BASE_KEY, requirement_overlap=ro.run_key_overlap()) == plain
    monkeypatch.setenv(ro.ENV_VAR, "drop")
    assert ro.run_key_overlap() == "drop"
    assert generate_grading_run_key(**BASE_KEY, requirement_overlap=ro.run_key_overlap()) != plain


# --------------------------------------------------------------------------- prompts

def test_the_grading_prompt_and_the_answer_schema_are_byte_identical_to_master():
    from cqc_cpcc.rubric_grading import build_rubric_grading_prompt
    prompt = build_rubric_grading_prompt(RUBRIC, "Write a program.", "class Main {}", requirements=CHECKLIST)
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    assert digest == PINNED_GRADING_PROMPT_SHA256
    schema = json.dumps(RubricAssessmentResult.model_json_schema(), sort_keys=True)
    assert "requirement_ids" not in schema
    assert "requirement_ids" not in json.dumps(DetectedError.model_json_schema())
    assert "possible overlap" not in prompt.lower()


PINNED_GRADING_PROMPT_SHA256 = "e12ff7c189c49219d546041f09c95ef299fb4956549e0fb266539f4ddae55c0a"


# --------------------------------------------------------------------------- offline gate script

def _load_script():
    path = Path(__file__).resolve().parents[2] / "scripts" / "requirement_overlap_rescore.py"
    spec = importlib.util.spec_from_file_location("requirement_overlap_rescore", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_offline_gate_reports_coverage_and_passes_on_a_synthetic_extraction(tmp_path):
    script = _load_script()
    lines = [json.dumps({"ok": True, "payload": {"items": [
        {"id": "R1", "text": "Print the total with two decimal places", "weight": "core"},
        {"id": "R2", "text": "Read a line of text from the user.", "weight": "core"},
        {"id": "R3", "text": "Use String methods rather than regular expressions", "weight": "core"}]}})]
    path = tmp_path / "extraction.jsonl"
    path.write_text("\n".join(lines), encoding="utf-8")
    cov = script.classifier_coverage(path)
    assert (cov["items"], cov["classified"]) == (3, 2)
    assert cov["by_kind"]["format"] == 1 and cov["by_kind"]["input"] == 1


def test_the_offline_gate_rescore_matches_the_scorer_and_never_goes_down(tmp_path):
    script = _load_script()
    cases = {c.case_id: c for c in ds.load_cases()}
    rubrics = script.load_rubrics_from_config()
    case = next(c for c in cases.values() if c.checklist and "ok" in c.validity and c.error_ids)
    fmt = next((e for e in case.error_ids), None)
    rows = [{"ok": True, "case_id": case.case_id,
             "payload": {"validity": "ok", "detected": sorted(case.error_ids), "total": 0.0, "requirements": {}}}]
    path = tmp_path / "raw.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    out = script.rescore(path, cases, rubrics)["rows"]
    assert len(out) == 1 and fmt
    assert out[0]["notes"] == out[0]["base"] and out[0]["drop"] >= out[0]["base"]
