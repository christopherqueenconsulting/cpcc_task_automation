#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""The execution gate: run the student's program on the plan's cases, then judge the model's errors.

Pipeline (``rubric_grading.grade_with_rubric``):

    validity gate -> :func:`run_student_execution` -> prompt + model -> compile gate ->
    :func:`apply_execution_gate` -> requirement coverage -> ``apply_backend_scoring``

With no plan (``execution_plan=None``) nothing here runs and the grade is what it was before.
``CQC_EXECUTION=off``, a language mismatch, a student compile failure and any runner error give
a report with no evidence: the prompt is unchanged and the score is unchanged ("cannot verify"
never becomes a deduction; "Does Not Compile" stays with the compile gate).

What the gate may do, all deterministic (``docs/execution-gate.md`` has the table):

* **Cannot verify** (no usable runner result, fewer than 3 validated cases, or every case a
  sample run from the instructions): evidence only, no removal and no addition.
* **Remove** a model error of category ``sequence``, ``output_values`` or ``output_format`` only
  when the plan has at least one independent (llm or manual) validated case and an independent
  boundary case for every condition the instructions name (``ExecutionPlan.covered_by_independent``),
  and only when every case matches (or, for sequence and value errors, matches apart from format).
  An error that names a structural thing (a loop, method, array, constant, class, recursion) is
  never removed, and neither is one that cites a concrete input unless that input, run as an
  extra case on the solution and on the student's program, also matches.
* **Set the format error** per the course's own definitions (:data:`FORMAT_POLICY`) when the only
  differences are format differences. A gate-added error carries the case input and the expected
  and actual line in its ``notes`` (student feedback shows them; the solution source never).
* **Keep** the model's errors, with evidence, when anything worse than a format difference shows
  up (a "disagrees" flag in the report when the model reported no output error at all).
* Never touches requirement errors or compiler errors, never sets ``needs_review``, never adds a
  major beyond the course mapping.

Every decision is a ``ReconciliationNote(stage="execution")``. A gate-added error can be taken out
by the instructor with ``reconciliation.remove_added`` (UI in PR 15).

No program text is logged. Student stdout, stderr and file names only ever go to the sandbox
result, the report's diff excerpt and the fenced prompt fragment.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, Sequence

from cqc_cpcc.error_definitions_models import infer_execution_category
from cqc_cpcc.execution_evidence import clean_block, clean_text
from cqc_cpcc.execution_check import (CaseResult, ExecutionPlan, ExecutionReport, SolutionSources,
                                      TestCase)
from cqc_cpcc.reconciliation import SOURCE_EXECUTION, may_drop, note, note_kept, note_removed
from cqc_cpcc.requirement_coverage import is_requirement_error
from cqc_cpcc.rubric_models import DetectedError, ReconciliationNote, RubricAssessmentResult
from cqc_cpcc.utilities import output_compare as oc
from cqc_cpcc.utilities.output_compare import Verdict

logger = logging.getLogger("cpcc_logger")

STAGE = "execution"

#: Fewer validated cases than this cannot remove or add anything.
MIN_VALIDATED_CASES = 3
#: Container wall time one student may use in all (the main run plus cited-input checks).
STUDENT_BUDGET_SECONDS = 180.0
#: Concrete inputs one error may cite and still be re-checked; more is "cannot check", error kept.
MAX_CITED_INPUTS = 3
MAX_CITED_TOKENS = 12
MAX_ERROR_TEXT_CHARS = 2000
MAX_SNIPPET_CHARS = 80
MAX_STDERR_CHARS = 400

CAT_SEQUENCE = "sequence"
CAT_VALUES = "output_values"
CAT_FORMAT = "output_format"
OUTPUT_CATEGORIES = (CAT_SEQUENCE, CAT_VALUES, CAT_FORMAT)

_FORMAT_DETAILS = (oc.SPACING, oc.CASE, oc.PUNCTUATION, oc.NUMBER_FORMAT, oc.DECIMAL_SCALE, oc.PROMPT_TEXT)

# --------------------------------------------------------------------------- #
# Per-course format policy (decision 2 of the plan: this table as written)
# --------------------------------------------------------------------------- #
#: course -> FORMAT_ONLY detail -> (error id suffix in that course's definitions, severity).
#: A trailing-whitespace or blank-line difference is MATCH and costs nothing (not in this table).
#: Change a cell here (and ``docs/execution-gate.md``) to change what a format difference costs.
_MINOR = ("MINOR_FORMATTING", "minor")
_DECIMAL = ("DECIMAL_SCALE", "major")
_MAJOR = ("MAJOR_FORMATTING", "major")
_OUT = ("OUTPUT_FORMATTING", "minor")
FORMAT_POLICY: dict = {
    # "formatting (spacing, decimal places, etc.)": separators fall under "etc."
    "CSC_151": {d: _OUT for d in _FORMAT_DETAILS},
    # MINOR: "spacing, missing dollar sign, not using print/println appropriately, etc."
    # DECIMAL_SCALE (major): "decimal scale is not correct and/or missing commas separators"
    "CSC_251": {oc.SPACING: _MINOR, oc.CASE: _MINOR, oc.PUNCTUATION: _MINOR, oc.PROMPT_TEXT: _MINOR,
                oc.NUMBER_FORMAT: _DECIMAL, oc.DECIMAL_SCALE: _DECIMAL},
    # MAJOR: "output and formatting must match sample output exactly"
    # MINOR: "decimal scale (incorrect decimal places or missing separators)"
    "CSC_134": {oc.SPACING: _MAJOR, oc.CASE: _MAJOR, oc.PUNCTUATION: _MAJOR, oc.PROMPT_TEXT: _MAJOR,
                oc.NUMBER_FORMAT: _MINOR, oc.DECIMAL_SCALE: _MINOR},
}
#: CSC134's MISSPELLING covers output strings: a single misspelled output word goes there, not to
#: MAJOR_FORMATTING.
MISSPELLING_SUFFIX = "MISSPELLING"
MISSPELLING_COURSES = frozenset({"CSC_134"})

_COURSE = re.compile(r"^CSC_?(\d{3})(?=_|$)")


def course_of(error_id: str) -> Optional[str]:
    m = _COURSE.match((error_id or "").strip().upper())
    return f"CSC_{m.group(1)}" if m else None


# --------------------------------------------------------------------------- #
# Error classification
# --------------------------------------------------------------------------- #
# Words that mean the error is about HOW the program is written, which matching output cannot
# disprove. Over-matching only costs a missed removal (the safe direction). Every alternative is a
# plain word or a short fixed phrase: no nested quantifiers.
_STRUCTURAL = re.compile(
    r"\b(?:loops?|looping|methods?|functions?|arrays?|arraylists?|vectors?|constants?|const|classes|class|"
    r"recursi(?:on|ve)|switch|iterat\w*|if|else|nested|statements?|lists?|dictionary|dictionaries|tuples?|"
    r"structs?|objects?|constructors?|getters?|setters?|prototypes?|void|final|named|data\s*type|reference|"
    r"pointers?|parameters?|arguments?|operators?|ternary|boolean|while|for-?each|static|import|scanner)\b"
    r"|\bmath\.|\bfor\s*\(|\bdo\s*-?\s*while\b|\bread(?:s|ing)?\s+(?:the\s+)?input",
    re.IGNORECASE)

# The program may print the right answers without computing them.
_UNVERIFIABLE = re.compile(
    r"hard-?cod|literal|constant answer|not calculat|does not calculate|does not compute|without comput|"
    r"print(?:s|ed)? the (?:expected|answer|result|total)|fixed output|no computation|magic number|"
    r"doesn.?t (?:calculate|compute)",
    re.IGNORECASE)

# The error cites a specific value, comparison or edge that the cases may not contain.
_VALUE = re.compile(
    r"(?<![\w.])-?\d+(?:\.\d+)?(?!\d|\.\d|\s*(?:decimal|digit|place))|[<>]=?|==|!=|"
    r"\b(?:zero|negative|empty|exactly|equal|greater|less|boundary|edge|maximum|minimum|over|under|exceed\w*|"
    r"above|below)\b",
    re.IGNORECASE)


def _error_text(error: DetectedError, with_code: bool) -> str:
    parts = [error.name or "", error.description or "", error.notes or ""]
    if with_code:
        parts.insert(0, (error.code or "").replace("_", " "))
    return " ".join(parts)[:MAX_ERROR_TEXT_CHARS]


def is_structural(error: DetectedError) -> bool:
    """The error's code, name, description or notes names how the program is built (a loop, method,
    array, constant, class, branch, data type, parameter, operator, ...). The output check cannot judge
    that from output, so it never removes one."""
    return bool(_STRUCTURAL.search(_error_text(error, True)))


def is_unverifiable(error: DetectedError) -> bool:
    """The error says the program does not really compute its output (hard-coded, a literal, a magic
    number). A program can print the right answers without computing them, so matching output never
    removes one."""
    return bool(_UNVERIFIABLE.search(_error_text(error, True)))


def cites_value(error: DetectedError) -> bool:
    """The error text names a specific number, a comparison or an edge (zero, negative, over, exactly)."""
    return bool(_VALUE.search(_error_text(error, False)))


def hold_reason(error: DetectedError) -> Optional[str]:
    """Why the gate must keep this error whatever the output says, or None."""
    if is_structural(error):
        return ("It names a structural requirement (how the code is written: a loop, branch, method, array, "
                "constant, type, parameter or the like), which the output check cannot judge: kept.")
    if is_unverifiable(error):
        return ("It says the program does not really compute its output (hard-coded or similar), which "
                "matching output cannot disprove: kept.")
    return None


def _defs_by_id(error_definitions: Optional[Sequence]) -> dict:
    return {str(getattr(d, "error_id", "")).strip().upper(): d for d in (error_definitions or [])}


def category_of(error: DetectedError, by_id: dict) -> Optional[str]:
    """``sequence``, ``output_values``, ``output_format`` or None. The definition's own
    ``execution_category`` wins; custom definitions fall back to ``infer_execution_category``."""
    definition = by_id.get((error.code or "").strip().upper())
    if definition is not None:
        tagged = getattr(definition, "execution_category", None)
        if tagged:
            return tagged
        return infer_execution_category(definition.error_id, getattr(definition, "name", ""))
    return infer_execution_category(error.code or "", error.name or "")


_CUE = r"(?:inputs?|enter(?:s|ed|ing)?|typ(?:e|es|ed|ing)|given)"
_NUM = r"-?\d+(?:\.\d+)?(?!\d|\.\d|\s*(?:decimal|digit|place|%))"
_CITED_NUMBERS = re.compile(
    rf"\b{_CUE}\b[^0-9`'\"\n]{{0,25}}?({_NUM}(?:[\s,&]+(?:and\s+)?{_NUM}){{0,{MAX_CITED_TOKENS - 1}}})",
    re.IGNORECASE)
_CITED_QUOTED = re.compile(rf"\b{_CUE}\b[^`'\"\n]{{0,25}}?[`'\"]([^`'\"\n]{{1,60}})[`'\"]", re.IGNORECASE)


def cited_inputs(error: DetectedError) -> list:
    """The concrete stdin values the error text cites ("when the input is 100", "entering `5 10`"),
    one stdin string per citation, one token per line. Empty when it cites none."""
    from cqc_cpcc.test_case_generation import normalize_stdin

    text = f"{error.description or ''} {error.notes or ''}"[:MAX_ERROR_TEXT_CHARS]
    found = []
    for pattern in (_CITED_NUMBERS, _CITED_QUOTED):
        for m in pattern.finditer(text):
            tokens = [t for t in re.split(r"[\s,&]+", m.group(1).strip()) if t and t.lower() != "and"]
            if not tokens or len(tokens) > MAX_CITED_TOKENS:
                continue
            stdin = normalize_stdin("\n".join(tokens))
            if stdin and stdin not in found:
                found.append(stdin)
    return found


def first_diff_pair(diff_excerpt: str) -> Optional[tuple]:
    """``(expected_line, actual_line)`` from the first removed and added line of a diff excerpt."""
    expected = actual = None
    for line in (diff_excerpt or "").splitlines():
        if line.startswith(("--- expected", "+++ actual", "@@")):
            continue
        if line.startswith("-") and expected is None:
            expected = line[1:]
        elif line.startswith("+") and actual is None:
            actual = line[1:]
        if expected is not None and actual is not None:
            return expected, actual
    return None


def is_single_word_misspelling(diff_excerpt: str) -> bool:
    """The diff is one expected line against one actual line that differ in exactly one word, and
    the two words are close in spelling (a typo, not a different word)."""
    lines = [ln for ln in (diff_excerpt or "").splitlines()
             if not ln.startswith(("--- expected", "+++ actual", "@@")) and ln[:1] in ("+", "-")]
    if len(lines) != 2:
        return False
    pair = first_diff_pair(diff_excerpt)
    if pair is None:
        return False
    e_words, a_words = pair[0].split(), pair[1].split()
    if len(e_words) != len(a_words):
        return False
    differing = [(e, a) for e, a in zip(e_words, a_words) if e != a]
    if len(differing) != 1:
        return False
    e, a = differing[0]
    return e.isalpha() and a.isalpha() and _is_typo(e.lower(), a.lower())


def _is_typo(a: str, b: str) -> bool:
    """Two different words of at least 5 letters that differ by one inserted or dropped letter, or by two
    neighbouring letters swapped. A substitution (Pass/Past, five/fine) is a different word, not a typo."""
    if len(a) < 5 or len(b) < 5 or a == b:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]
    short, long_ = sorted((a, b), key=len)
    return len(long_) - len(short) == 1 and any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def inert(text: str, limit: int = MAX_SNIPPET_CHARS) -> str:
    """Program text for a student-visible note: one line, no control characters, backticks turned
    into quotes, cut at ``limit``, in a code span so Markdown and HTML in it stay literal."""
    one = " ".join(clean_text(text).split()).replace("`", "'")
    if len(one) > limit:
        one = one[: limit - 3] + "..."
    return f"`{one}`" if one else "(nothing)"


# --------------------------------------------------------------------------- #
# Running the student's program
# --------------------------------------------------------------------------- #
@dataclass
class ExecutionBudget:
    """Container wall time one student may use. The main run and cited-input checks all count."""

    limit: float = STUDENT_BUDGET_SECONDS
    spent: float = 0.0

    def charge(self, seconds: float) -> None:
        self.spent += max(0.0, seconds)

    def allows(self, n_cases: int, repeat: int = 1) -> bool:
        from cqc_cpcc.utilities.program_runner import max_run_seconds

        return self.limit - self.spent >= max_run_seconds(n_cases, repeat)

    def allows_cited_check(self, n_cases: int) -> bool:
        """Room for the reference run (twice) and the student's run together, not each on its own."""
        from cqc_cpcc.utilities.program_runner import max_run_seconds

        return self.limit - self.spent >= max_run_seconds(n_cases, 2) + max_run_seconds(n_cases, 1)


@dataclass
class StudentExecution:
    """One student's output check: the report, plus what the gate needs to re-run an input."""

    plan: ExecutionPlan
    report: ExecutionReport
    language: str = ""
    files: tuple = ()
    main: Optional[str] = None
    owner: Optional[str] = None
    runner: Optional[Callable] = None
    budget: ExecutionBudget = field(default_factory=ExecutionBudget)
    #: Student stderr for cases that crashed, for the prompt fragment only (never stored).
    stderr_by_case: dict = field(default_factory=dict)

    @property
    def has_evidence(self) -> bool:
        return self.report.status == "ok" and bool(self.report.cases)


#: runner_error reasons that are the student's or the job's, not the runner being lost.
_NOT_THE_RUNNER = ("driver_", "compile_timeout", "bad_input", "job_too_large")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,80}$")


def _judge(expected: str, expected_reads: Sequence[int], got, ignore_patterns: Sequence[str]):
    """Compare one case's student outcome with the expected output. A crash, a timeout or output cut
    at the cap is never trusted as MATCH or FORMAT_ONLY."""
    run = oc.RunOutcome(ran=True, timed_out=got.status == "TIMEOUT", exit_code=got.exit_code,
                        eof_error=got.eof_error)
    reads = [int(r[2]) for r in got.stdin_reads] if got.segmented else []
    segments = (list(expected_reads), reads) if expected_reads else None
    cmp = oc.compare_output(expected, got.stdout, run=run, ignore_patterns=ignore_patterns,
                            prompt_segments=segments)
    if cmp.verdict in (Verdict.MATCH, Verdict.FORMAT_ONLY) and (
            got.status != "OK" or run.crashed or got.stdout_truncated):
        return oc.CaseComparison(Verdict.RUNTIME_ERROR, got.reason or oc.CRASH, cmp.similarity, cmp.diff_excerpt)
    return cmp


def run_student_execution(plan: Optional[ExecutionPlan], source_files: Optional[dict], *,
                          runner: Optional[Callable] = None,
                          owner: Optional[str] = None) -> Optional[StudentExecution]:
    """Run the student's program on the plan's usable cases in the sandbox and compare each output.

    Returns None when there is no plan. Otherwise the report's ``status`` is ``ok`` (with one
    result per case), ``skipped`` (execution off, plan disabled, no usable case, unsafe or
    oversized files, a driver or compile-timeout problem), ``language_mismatch``,
    ``student_compile_failed`` or ``runner_unavailable`` (the sandbox could not answer). Only ``ok``
    is evidence; every other status leaves the prompt and the grade as they were. Synchronous: run
    it in a worker thread.
    """
    if plan is None:
        return None
    from cqc_cpcc.utilities import program_runner as pr
    from cqc_cpcc.utilities.code_preview import collect_code_files, runnable_files, runner_language

    exe = StudentExecution(plan=plan, report=ExecutionReport(plan_hash=plan.plan_hash, status="skipped"),
                           runner=runner)

    def finish(status: str, reason: str) -> StudentExecution:
        exe.report = ExecutionReport(plan_hash=plan.plan_hash, status=status, reason=reason)
        return exe

    if pr._execution_forced_off():
        return finish("skipped", "execution_off")
    if not plan.enabled:
        return finish("skipped", "plan_disabled")
    cases = plan.usable_cases()
    if not cases:
        return finish("skipped", "no_usable_cases")
    try:
        for pattern in plan.ignore_patterns:
            re.compile(pattern)
    except re.error:
        return finish("skipped", "bad_ignore_pattern")

    code_files, _ = collect_code_files(source_files)
    language = runner_language(code_files)
    if language is None or language != plan.language:
        return finish("language_mismatch", "no_runnable_source" if language is None else "other_language")
    sources = runnable_files(code_files, language)
    if not sources or any(f.truncated for f in code_files):
        return finish("skipped", "file_too_large")
    names = [f.name for f in sources]
    if len({n.lower() for n in names}) != len(names):
        return finish("skipped", "duplicate_file_name")
    if not all(_SAFE_NAME.match(n) for n in names):
        return finish("skipped", "unsafe_file_name")
    exe.language = language
    exe.files = tuple(pr.SandboxFile(f.name, f.content) for f in sources)
    exe.owner = owner or pr.new_owner_id()

    if not pr.runner_status().available:
        return finish("runner_unavailable", "unavailable")
    run = runner or pr.run_program
    started = time.monotonic()
    outcome = run(language, list(exe.files), [pr.SandboxCase(c.id, c.stdin) for c in cases],
                  mode="run", main=None, repeat=1, owner=exe.owner)
    exe.budget.charge(time.monotonic() - started)
    if outcome.status != "ok":
        if outcome.reason.startswith(_NOT_THE_RUNNER):
            return finish("skipped", outcome.reason)
        pr.reset_runner_status_cache()  # the next student re-checks Docker instead of a stale "available"
        return finish("runner_unavailable", outcome.reason or "runner_error")
    if outcome.compile is None or not outcome.compile.ok:
        return finish("student_compile_failed", "")

    by_id = {c.id: c for c in outcome.cases}
    results = []
    for case in cases:
        got = by_id.get(case.id)
        if got is None:
            results.append(CaseResult(case_id=case.id, verdict=Verdict.NOT_RUN.value))
            continue
        cmp = _judge(case.expected_output or "", case.expected_reads, got, plan.ignore_patterns)
        results.append(CaseResult(case_id=case.id, verdict=cmp.verdict.value, detail=cmp.detail,
                                  similarity=round(cmp.similarity, 4), diff_excerpt=clean_block(cmp.diff_excerpt)))
        if cmp.verdict in (Verdict.RUNTIME_ERROR, Verdict.TIMEOUT) and got.stderr:
            exe.stderr_by_case[case.id] = clean_block(got.stderr)[:MAX_STDERR_CHARS]
    exe.report = ExecutionReport(plan_hash=plan.plan_hash, status="ok", cases=results)
    logger.info("Output check: %d case(s) run, verdicts=%s", len(results),
                sorted({r.verdict for r in results}))
    return exe


# --------------------------------------------------------------------------- #
# Cited-input re-check
# --------------------------------------------------------------------------- #
def solution_files_from_text(plan: ExecutionPlan, text: Optional[str]) -> Optional[list]:
    """The solution's files rebuilt from the prompt text (``SolutionSources.combined_text``), or None.

    The plan stores only hashes, so the text is accepted only when every file's SHA-256 matches the
    plan's. Anything else (edited text, a different solution) returns None and no cited input is re-run.
    """
    import hashlib

    from cqc_cpcc.utilities.program_runner import SandboxFile

    if not text or not plan.solution_files:
        return None
    declared = plan.solution_files

    def ok(name: str, content: str, sha: str) -> bool:
        return hashlib.sha256(content.encode("utf-8")).hexdigest() == sha

    if len(declared) == 1:
        f = declared[0]
        return [SandboxFile(f.name, text)] if ok(f.name, text, f.sha256) else None
    out, pos = [], 0
    for i, f in enumerate(declared):
        header = f"// File: {f.name}\n"
        if not text.startswith(header, pos):
            return None
        start = pos + len(header)
        if i + 1 < len(declared):
            nxt = f"\n\n// File: {declared[i + 1].name}\n"
            end = text.find(nxt, start)
            if end == -1:
                return None
            content, pos = text[start:end], end + 2
        else:
            content = text[start:]
        if not ok(f.name, content, f.sha256):
            return None
        out.append(SandboxFile(f.name, content))
    return out


def check_cited_inputs(execution: StudentExecution, solution_files: Optional[list],
                       stdins: Sequence[str]) -> Optional[dict]:
    """Run each ``stdin`` on the solution (twice) and on the student's program; ``{stdin: Verdict}``.

    None when it cannot be done: no solution text, the budget is spent, the runner failed, or the
    solution did not give a clean reference output. Same stdin cap and per-run limits as any case,
    and the elapsed container time is charged to the student's budget.
    """
    from cqc_cpcc.utilities import program_runner as pr

    if not stdins or not solution_files or not execution.files:
        return None
    n = len(stdins)
    if not execution.budget.allows_cited_check(n):
        return None
    plan = execution.plan
    run = execution.runner or pr.run_program
    cases = [pr.SandboxCase(f"X{i + 1}", s) for i, s in enumerate(stdins)]

    started = time.monotonic()
    ref = run(plan.language, list(solution_files), cases, mode="run", main=plan.main, repeat=2,
              owner=execution.owner)
    execution.budget.charge(time.monotonic() - started)
    if ref.status != "ok" or ref.compile is None or not ref.compile.ok:
        return None
    ref_by_id = {c.id: c for c in ref.cases}

    started = time.monotonic()
    stu = run(execution.language, list(execution.files), cases, mode="run", main=execution.main,
              repeat=1, owner=execution.owner)
    execution.budget.charge(time.monotonic() - started)
    if stu.status != "ok" or stu.compile is None or not stu.compile.ok:
        return None
    stu_by_id = {c.id: c for c in stu.cases}

    verdicts = {}
    for case in cases:
        sol, got = ref_by_id.get(case.id), stu_by_id.get(case.id)
        if sol is None or got is None:
            return None
        if (sol.status != "OK" or sol.eof_error or sol.nondeterministic or sol.stdout_truncated
                or sol.exit_code not in (0, None) or not sol.stdout.strip()):
            return None
        reads = [int(r[2]) for r in sol.stdin_reads] if sol.segmented else []
        verdicts[case.stdin] = _judge(sol.stdout, reads, got, plan.ignore_patterns).verdict
    return verdicts


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #
_REASON_TEXT = {
    "execution_off": "the output check is switched off",
    "plan_disabled": "the output check is off for this assignment",
    "no_usable_cases": "the assignment has no usable test case",
    "bad_ignore_pattern": "an ignore pattern is not a valid expression",
    "file_too_large": "a submitted source file is too large to run",
    "duplicate_file_name": "two submitted files share a name",
    "unsafe_file_name": "a submitted file has a name the sandbox does not accept",
    "no_runnable_source": "the submission has no source file the sandbox can run",
    "other_language": "the submission is in a different language than the solution",
    "unavailable": "the sandbox is not available",
}


def _reason_text(report: ExecutionReport) -> str:
    return _REASON_TEXT.get(report.reason) or (report.reason.replace("_", " ") if report.reason else report.status)


def _added_note(error: DetectedError, reason: str) -> ReconciliationNote:
    """The note for an error the gate added. It carries the error, so the instructor can Remove it."""
    return ReconciliationNote(stage=STAGE, action="added", code=error.code, reason=reason, error=error)


def _ok_verdicts(mode: str) -> tuple:
    return (Verdict.MATCH,) if mode == "match" else (Verdict.MATCH, Verdict.FORMAT_ONLY)


def _find_definition(error_definitions, course: Optional[str], suffix: str):
    suffix = "_" + suffix
    best = None
    for d in error_definitions or []:
        error_id = str(getattr(d, "error_id", "")).strip().upper()
        if course_of(error_id) == course and error_id.endswith(suffix):
            if getattr(d, "enabled", True):
                return d
            best = best or d
    return best if best is not None and getattr(best, "enabled", True) else None


def format_course(error_definitions, by_id: dict) -> Optional[str]:
    """The course whose format policy applies: the one course (of those in :data:`FORMAT_POLICY`)
    that owns an enabled output-format definition in the list. None for a custom assignment."""
    courses = set()
    for d in error_definitions or []:
        if not getattr(d, "enabled", True):
            continue
        category = getattr(d, "execution_category", None) or infer_execution_category(
            d.error_id, getattr(d, "name", ""))
        course = course_of(d.error_id)
        if category == CAT_FORMAT and course in FORMAT_POLICY:
            courses.add(course)
    return next(iter(courses)) if len(courses) == 1 else None


def _case_by_id(plan: ExecutionPlan, case_id: str) -> Optional[TestCase]:
    return next((c for c in plan.cases if c.id == case_id), None)


def _transparency_note(plan: ExecutionPlan, results: Sequence[CaseResult]) -> str:
    """Student-facing: the case input and the expected vs actual line (program output only)."""
    first = results[0] if results else None
    if first is None:
        return "Output check: your program's output differs from the expected output in format."
    case = _case_by_id(plan, first.case_id)
    shown = " ".join(ln for ln in (case.stdin if case else "").split())
    pair = first_diff_pair(first.diff_excerpt)
    head = f"Output check: with the input {inert(shown)}" if shown else "Output check: on one test run"
    if pair is None:
        return f"{head}, your program's output differs from the expected output in format."
    return f"{head}, the expected line was {inert(pair[0])} but your program printed {inert(pair[1])}."


def apply_execution_gate(
        result: RubricAssessmentResult,
        execution: Optional[StudentExecution],
        error_definitions: Optional[Sequence] = None,
        *,
        reference_solution: Optional[str] = None,
) -> tuple:
    """Judge the model's errors against the student's execution report. Returns ``(result, info)``.

    Runs after the compile gate and before requirement coverage, so the score is recomputed from the
    corrected list. With ``execution`` None the result comes back untouched (the plan was None).
    Otherwise ``result.execution_report`` is set (also when the check was skipped) and every
    decision is a ``ReconciliationNote(stage="execution")``.
    """
    info = {"action": "none", "status": None, "mode": None, "verified": False, "disagrees": False}
    if execution is None:
        return result, info
    report, plan = execution.report, execution.plan
    notes = list(result.reconciliation or [])
    info["status"] = report.status
    errors = list(result.detected_errors or [])
    by_id = _defs_by_id(error_definitions)
    gate = {"mode": None, "verified": False, "cannot_verify": "", "disagrees": False,
            "removed": [], "added": [], "kept": [], "uncovered_conditions": [],
            "cited_checks": 0, "language": plan.language}

    def finish(update_errors: bool, action: str):
        out = {"reconciliation": notes, "execution_report": {**report.model_dump(), "gate": gate}}
        if update_errors:
            out.update(detected_errors=errors, error_counts_by_severity=None, error_counts_by_id=None)
        info.update(action=action, mode=gate["mode"], verified=gate["verified"], disagrees=gate["disagrees"])
        return result.model_copy(update=out), info

    if not execution.has_evidence:
        gate["cannot_verify"] = report.status
        notes.append(note(STAGE, f"Output check not used ({_reason_text(report)}): graded without it, "
                                 "the score follows the model's judgment."))
        return finish(False, "skipped")

    results = list(report.cases)
    worst = oc.aggregate(Verdict(r.verdict) for r in results)
    mode = "match" if worst is Verdict.MATCH else "format_only" if worst is Verdict.FORMAT_ONLY else "differs"
    gate["mode"] = mode

    usable = plan.usable_cases()
    cannot = ""
    if len(usable) < MIN_VALIDATED_CASES:
        cannot = f"fewer than {MIN_VALIDATED_CASES} validated test cases"
    elif plan.sample_only:
        cannot = "every test case is a sample run copied from the instructions"
    elif any(r.verdict == Verdict.NOT_RUN.value for r in results):
        cannot = "a test case did not run"
    uncovered = [c.id for c in plan.conditions if not plan.covered_by_independent(c.id)]
    gate["uncovered_conditions"] = uncovered
    model_errors = [e for e in errors if may_drop(e) and not is_requirement_error(e.code)]
    cats = {id(e): category_of(e, by_id) for e in model_errors}

    if mode == "differs":
        reported = any(cats[id(e)] in OUTPUT_CATEGORIES for e in model_errors)
        gate["disagrees"] = not reported
        bad = sorted({r.verdict for r in results if r.verdict not in (Verdict.MATCH.value, Verdict.FORMAT_ONLY.value)})
        notes.append(note(STAGE, "The program's output differs from the reference on "
                                 f"{sum(r.verdict in bad for r in results)} of {len(results)} test case(s) ({', '.join(bad)}). "
                                 + ("The model reported no output error: shown as a disagreement, nothing changed."
                                    if not reported else "The model's errors are kept with this evidence.")))
        changed = False
        if not cannot:
            changed = _route_misspelling(plan, results, model_errors, cats, error_definitions, errors, notes, gate)
        gate["verified"] = not cannot
        return finish(changed, "changed" if changed else "kept")

    if cannot:
        gate["cannot_verify"] = cannot
        notes.append(note(STAGE, f"The output check ran but cannot change the grade ({cannot}): shown as evidence only."))
        return finish(False, "evidence")
    gate["verified"] = True

    changed = False
    removable = {CAT_SEQUENCE, CAT_VALUES, CAT_FORMAT} if mode == "match" else {CAT_SEQUENCE, CAT_VALUES}
    ok = _ok_verdicts(mode)
    n = len(results)
    why = (f"the program's output matches the reference on all {n} test cases" if mode == "match" else
           f"the program's output matches the reference on all {n} test cases apart from format")

    # Errors that may leave, split by what stands in the way.
    candidates = []
    if uncovered:
        notes.append(note(STAGE, "No independent test case sits on the exact edge of: "
                                 f"{', '.join(uncovered)}. The output check cannot remove errors; format "
                                 "errors can still be set."))
    for e in model_errors:
        if cats[id(e)] in removable and not uncovered:
            candidates.append(e)
    if mode == "format_only":
        # Format errors the model reported are replaced below, by the course policy.
        pass

    pending = {}  # id(error) -> stdins to re-check
    to_remove = []
    for e in candidates:
        held = hold_reason(e)
        if held:
            notes.append(note_kept(STAGE, e, held))
            gate["kept"].append(e.code)
            continue
        cited = cited_inputs(e)
        if cited:
            pending[id(e)] = cited
        elif cites_value(e):
            notes.append(note_kept(STAGE, e, "Kept: it cites a specific value that cannot be re-run."))
            gate["kept"].append(e.code)
        else:
            to_remove.append(e)

    if pending:
        all_stdins = list(dict.fromkeys(s for v in pending.values() for s in v))
        solution_files = solution_files_from_text(plan, reference_solution)
        checked = (check_cited_inputs(execution, solution_files, all_stdins)
                   if len(all_stdins) <= MAX_CITED_INPUTS else None)
        gate["cited_checks"] = len(all_stdins) if checked is not None else 0
        for e in candidates:
            stdins = pending.get(id(e))
            if stdins is None:
                continue
            if checked is not None and all(checked.get(s) in ok for s in stdins):
                to_remove.append(e)
            else:
                notes.append(note_kept(STAGE, e, "Kept: the input it cites "
                                                 + ("still gives a different output."
                                                    if checked is not None else "could not be re-run.")))
                gate["kept"].append(e.code)

    # Format differences: set the course's format error and drop the model's other format errors.
    added = []
    if mode == "format_only":
        added, replaced = _format_errors(plan, results, model_errors, cats, error_definitions, by_id, errors,
                                         notes, gate, ok, candidates_ok=not uncovered)
        to_remove.extend(e for e in replaced if all(e is not r for r in to_remove))

    for e in to_remove:
        notes.append(note_removed(STAGE, e, f"Not counted: {why}."))
        gate["removed"].append(e.code)
    if to_remove:
        drop = {id(e) for e in to_remove}
        errors = [e for e in errors if id(e) not in drop]
        changed = True
    for e, text in added:
        errors.append(e)
        notes.append(_added_note(e, text))
        gate["added"].append(e.code)
        changed = True
    if not changed:
        notes.append(note(STAGE, f"The output check ran ({why}); no error needed to change."))
    return finish(changed, "changed" if changed else "kept")


def _format_errors(plan, results, model_errors, cats, error_definitions, by_id, errors, notes, gate, ok,
                   candidates_ok: bool):
    """Errors to add for the FORMAT_ONLY details, and the model's format errors they replace."""
    course = format_course(error_definitions, by_id)
    details = sorted({r.detail for r in results if r.verdict == Verdict.FORMAT_ONLY.value and r.detail})
    if course is None:
        notes.append(note(STAGE, "The program differs from the reference only in format, but this assignment's "
                                 "error definitions are not one of the known courses (custom): no format error "
                                 "was set and the model's format errors are left as reported."))
        return [], []
    policy = FORMAT_POLICY[course]
    desired = []  # (detail, definition)
    for detail in details:
        suffix, severity = policy.get(detail, (None, None))
        definition = _find_definition(error_definitions, course, suffix) if suffix else None
        if definition is None or str(definition.severity_category).lower() != severity:
            notes.append(note(STAGE, f"{course} has no enabled definition {suffix or '?'} ({severity or '?'}) for a "
                                     f"{detail.replace('_', ' ')} difference: skipped, not counted."))
            continue
        if all(definition.error_id.upper() != d.error_id.upper() for _, d in desired):
            desired.append((detail, definition))
    if not desired:
        return [], []
    wanted = {d.error_id.upper() for _, d in desired}
    present = {(e.code or "").upper() for e in errors}
    added = []
    for detail, definition in desired:
        if definition.error_id.upper() in present:
            continue
        diff_results = [r for r in results if r.verdict == Verdict.FORMAT_ONLY.value and r.detail == detail]
        error = DetectedError(
            code=definition.error_id, name=definition.name, severity=definition.severity_category.lower(),
            description=definition.description, occurrences=1,
            notes=_transparency_note(plan, diff_results), source=SOURCE_EXECUTION)
        added.append((error, f"The output differs from the reference only in {detail.replace('_', ' ')}: counted as "
                             f"{definition.name} per the {course.replace('_', ' ')} error definitions."))
    replaced = []
    if candidates_ok:
        replaced = [e for e in model_errors if cats[id(e)] == CAT_FORMAT and (e.code or "").upper() not in wanted
                    and hold_reason(e) is None and not cited_inputs(e) and not cites_value(e)]
    return added, replaced


def _route_misspelling(plan, results, model_errors, cats, error_definitions, errors, notes, gate) -> bool:
    """CSC134: when the only difference is one misspelled output word, a model MAJOR_FORMATTING error
    becomes the course's MISSPELLING error (a smaller deduction). Never adds when the model reported
    nothing. Returns True when the error list changed."""
    bad = [r for r in results if r.verdict not in (Verdict.MATCH.value, Verdict.FORMAT_ONLY.value)]
    if not bad or any(r.verdict != Verdict.TEXT_DIFFERS.value or not is_single_word_misspelling(r.diff_excerpt)
                      for r in bad):
        return False
    format_errors = [e for e in model_errors if cats[id(e)] == CAT_FORMAT and str(e.severity).lower() == "major"]
    if not format_errors:
        return False
    # Validate every error first, so nothing is changed unless all of them can be routed.
    routes = []
    for e in format_errors:
        course = course_of(e.code)
        if course not in MISSPELLING_COURSES:
            return False
        definition = _find_definition(error_definitions, course, MISSPELLING_SUFFIX)
        if definition is None or str(definition.severity_category).lower() != "minor":
            return False
        routes.append((e, definition))
    for e, definition in routes:
        miss = DetectedError(code=definition.error_id, name=definition.name, severity="minor",
                             description=definition.description, occurrences=1,
                             notes=_transparency_note(plan, bad[:1]).replace("Output check:", "Output check (spelling):", 1),
                             source=SOURCE_EXECUTION)
        errors[:] = [x for x in errors if x is not e]
        notes.append(note_removed(STAGE, e, "The only difference is one misspelled output word: routed to "
                                              f"{definition.name}, not formatting."))
        gate["removed"].append(e.code)
        if all((x.code or "").upper() != definition.error_id.upper() for x in errors):
            errors.append(miss)
            notes.append(_added_note(miss, "A misspelled word in the program's output."))
            gate["added"].append(miss.code)
    return True


# --------------------------------------------------------------------------- #
# Building a plan (the caller of suggest_test_cases)
# --------------------------------------------------------------------------- #
async def derive_execution_plan(
        instructions: str,
        solution: SolutionSources,
        *,
        manual_cases: Iterable[TestCase] = (),
        ignore_patterns: Sequence[str] = (),
        model_name: Optional[str] = None,
        runner: Optional[Callable] = None,
        owner: Optional[str] = None,
) -> tuple:
    """Sample runs, one cached model call for generated cases, validation on the solution; ``(plan, reason)``.

    ``suggest_test_cases`` has no ``CQC_EXECUTION=off`` switch: it would send the solution to the
    model provider. So this checks ``program_runner.runner_status()`` first and, when the sandbox is
    off or missing, returns ``(None, reason)`` without any model call: ``off`` means no solution
    text leaves the host. A plan that comes back ``enabled=False`` explains itself in
    ``disabled_reason``.
    """
    import asyncio

    from cqc_cpcc import test_case_generation as tcg
    from cqc_cpcc.utilities import program_runner as pr

    status = pr.runner_status()
    if not status.available:
        return None, status.reason or "unavailable"
    samples = tcg.parse_sample_runs(instructions)
    suggestion = await tcg.suggest_test_cases(instructions, solution, model_name=model_name)
    llm_cases, conditions = tcg.suggestion_to_plan_parts(suggestion)
    cases = tcg.merge_cases(list(manual_cases), tcg.cases_from_samples(samples), llm_cases)
    shown = {s.id: s.expected_output for s in samples}
    validation = await asyncio.to_thread(
        tcg.validate_cases_on_solution, solution, cases, conditions=conditions, sample_outputs=shown,
        ignore_patterns=list(ignore_patterns), runner=runner, owner=owner)
    risks = tcg.scan_solution_risks(solution)
    plan = tcg.build_plan(solution, validation, conditions, ignore_patterns=list(ignore_patterns), risks=risks,
                          all_cases=cases)
    return plan, plan.disabled_reason
