#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Requirement / model-error overlap: one penalty per issue, decided outside the model.

Requirement coverage turns a requirement the model marked partial or missing into an error.
The model also lists its own errors, and the two often describe the same defect (a
``Print the total with two decimal places`` requirement marked partial next to an
``OUTPUT_FORMATTING`` error). Scoring both charges the student twice (ruling 2026-10-22:
one penalty per issue).

This stage pairs them deterministically. The grading prompt, the answer schema and the model's
output are untouched: asking the model to link its own errors made it mark requirements
partial far more often (PR 8, A/B 40033879399), so the pairing is done here from the text.

* Each requirement gets a ``kind`` from its wording (:func:`requirement_kind`); each model
  error code gets a set of kinds (:func:`error_kinds`). Either may be unknown: an unknown side
  is never paired (today's behaviour).
* A pair is acted on only when it is UNAMBIGUOUS: the requirement has exactly one kind-matching
  model error and that error has exactly one kind-matching requirement (one-to-one).
* Of a matched pair the lesser is dropped; at equal severity the REQUIREMENT error is kept and
  the model error dropped (Christopher, tie rule A', reversible). A ``MISSING_REQUIREMENT_*``
  error is never dropped (shallow-submission detection), nor is an error backed by the
  compiler or the execution check, nor "Does Not Compile".
* Anything ambiguous or unmatched that still looks like an overlap gets an instructor-only
  note and keeps both penalties.

Mode (``CQC_REQUIREMENT_OVERLAP``): ``notes`` (default: write notes, drop nothing, scores equal
to a run without this stage), ``drop`` (apply the drops, each one a Reinstate-able note) or
``off``. Only ``drop`` changes a score, so only ``drop`` enters the grading run key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from cqc_cpcc.reconciliation import may_drop, note, note_removed
from cqc_cpcc.requirement_coverage import (MISSING_REQUIREMENT_ID, PARTIAL_REQUIREMENT_ID,
                                           RequirementChecklist, is_requirement_error)
from cqc_cpcc.rubric_models import DetectedError, ReconciliationNote, RubricAssessmentResult

ENV_VAR = "CQC_REQUIREMENT_OVERLAP"
MODE_NOTES = "notes"
MODE_DROP = "drop"
MODE_OFF = "off"
MODES = (MODE_NOTES, MODE_DROP, MODE_OFF)
DEFAULT_MODE = MODE_NOTES

STAGE = "overlap"

KIND_FORMAT = "format"   # what the program prints and how (decimals, currency, layout)
KIND_INPUT = "input"     # reading or prompting for values
KIND_VALID = "valid"     # rejecting or re-asking for bad input
KIND_FUNC = "func"       # functions and methods
KIND_SELECT = "select"   # decisions, lookups, conditional rules
KIND_LOOP = "loop"       # repetition
KIND_CALC = "calc"       # arithmetic, counting, converting, random values
KIND_DATA = "data"       # variables, arrays, starting values
ALL_KINDS = (KIND_FORMAT, KIND_INPUT, KIND_VALID, KIND_FUNC, KIND_SELECT, KIND_LOOP, KIND_CALC, KIND_DATA)


def overlap_mode() -> str:
    """The configured mode; anything unrecognised falls back to ``notes`` (never drops)."""
    raw = (os.environ.get(ENV_VAR) or "").strip().lower()
    return raw if raw in MODES else DEFAULT_MODE


def run_key_overlap(mode: Optional[str] = None) -> Optional[str]:
    """The value for the grading run key: ``"drop"`` or ``None``.

    Only ``drop`` changes a score, so notes/off leave every existing key (and cached result)
    exactly as it was.
    """
    return MODE_DROP if (mode or overlap_mode()) == MODE_DROP else None


# ---------------------------------------------------------------------------
# Requirement kind, from the requirement's own wording
# ---------------------------------------------------------------------------

def _words(text: str) -> list[str]:
    """Lowercase words; hyphens joined (``re-prompt`` -> ``reprompt``), other punctuation splits."""
    out: list[str] = []
    cur: list[str] = []
    for ch in (text or "").lower():
        if ch.isalnum():
            cur.append(ch)
        elif ch in "-'’":
            continue
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


def _has_phrase(words: list[str], *phrase: str) -> bool:
    n = len(phrase)
    return any(tuple(words[i:i + n]) == phrase for i in range(len(words) - n + 1))


_NEGATION_STARTS = frozenset({"do", "dont", "never", "avoid"})
_VALID_WORDS = frozenset({"validate", "validates", "validation", "reject", "rejects", "refuse", "refuses",
                          "invalid", "reprompt", "reprompting", "sanitize"})
_FUNC_WORDS = frozenset({"function", "functions", "method", "methods", "prototype", "prototypes"})
_LOOP_WORDS = frozenset({"loop", "loops", "repeat", "repeats", "repeatedly", "until", "dowhile"})
_FORMAT_CUES = frozenset({"currency", "decimal", "decimals", "dollar", "dollars", "separator", "separators"})
_CONDITION_WORDS = frozenset({"if", "when", "only", "unless", "otherwise", "whether", "switch"})
_SKIP_LEAD = frozenset({"when", "if", "for", "once", "after", "before", "whenever", "while"})

_FORMAT_VERBS = frozenset({"print", "prints", "display", "displays", "show", "shows", "output", "outputs",
                           "tell", "tells", "announce", "announces", "list", "lists", "report", "reports",
                           "write", "writes"})
_INPUT_VERBS = frozenset({"read", "reads", "prompt", "prompts", "ask", "asks", "accept", "accepts",
                          "enter", "enters", "input", "get", "gets", "let", "lets", "obtain"})
_CALC_VERBS = frozenset({"compute", "computes", "calculate", "calculates", "add", "adds", "subtract",
                         "subtracts", "multiply", "multiplies", "divide", "divides", "count", "counts",
                         "convert", "converts", "sum", "total", "average", "find", "finds", "withdraw",
                         "withdraws", "deposit", "deposits", "pay", "pays", "generate", "generates",
                         "pick", "picks", "choose", "chooses", "increment", "decrement", "accumulate"})
_SELECT_VERBS = frozenset({"apply", "applies", "determine", "determines", "decide", "decides", "compare",
                           "compares", "look", "lookup", "select", "selects", "classify", "assign",
                           "assigns", "allow", "allows"})
_DATA_VERBS = frozenset({"store", "stores", "initialize", "initializes", "start", "starts", "declare",
                         "declares", "create", "creates", "set", "sets", "hold", "holds", "keep", "keeps"})


def requirement_kind(text: str) -> Optional[str]:
    """The kind of a requirement from its wording, or ``None`` when it is not clear.

    Plain keyword and leading-verb rules, in order; no regular expressions. ``None`` means
    "leave this requirement alone".
    """
    words = _words(text)
    if not words:
        return None
    if words[0] in _NEGATION_STARTS or (words[0] == "not"):
        return None
    wset = set(words)

    # Validation wording wins: "Print an error message if ..." is about rejecting bad input.
    if wset & _VALID_WORDS or _has_phrase(words, "error", "message") or _has_phrase(words, "keep", "prompting"):
        return KIND_VALID
    # Functions and methods (but "String methods" is a library constraint, not a function requirement).
    if wset & _FUNC_WORDS and not _has_phrase(words, "string", "methods"):
        return KIND_FUNC
    if _has_phrase(words, "string", "methods"):
        return None
    if wset & _LOOP_WORDS:
        return KIND_LOOP

    # Leading verb: skip a "When/If/For ..." clause head.
    lead = words[0]
    rest = words
    if lead in _SKIP_LEAD:
        raw = (text or "")
        comma = raw.find(",")
        if comma >= 0:
            rest = _words(raw[comma + 1:]) or words
            lead = rest[0]
    wset_rest = set(rest)

    # Decimals/currency wording is about how the output looks, even in "Calculate and print ...".
    if wset & _FORMAT_CUES or _has_phrase(words, "decimal", "place") or _has_phrase(words, "decimal", "places"):
        return KIND_FORMAT

    if lead == "use":
        tail = set(rest[1:4])
        if "scanner" in tail:
            return KIND_INPUT
        if "switch" in tail:
            return KIND_SELECT
        if "mathpow" in tail or ("math" in tail and "pow" in tail):
            return KIND_CALC
        return _keyword_kind(rest)
    if lead in _FORMAT_VERBS:
        return KIND_FORMAT
    if lead in _INPUT_VERBS:
        # "Allow ... only when ..." is a rule, handled below; "let/ask/read/prompt" are input.
        return KIND_INPUT
    if lead in _DATA_VERBS:
        return KIND_DATA
    if lead in _CALC_VERBS:
        if lead in ("generate", "pick", "choose", "chooses", "picks", "generates"):
            return KIND_CALC
        return KIND_SELECT if wset_rest & _CONDITION_WORDS else KIND_CALC
    if lead in _SELECT_VERBS:
        if lead in ("allow", "allows") and not (wset_rest & {"only", "if", "when", "unless"}):
            return KIND_INPUT
        if lead == "select" and "random" in wset_rest:
            return KIND_CALC
        return KIND_SELECT
    return _keyword_kind(rest)


def _keyword_kind(words: list[str]) -> Optional[str]:
    """Fallback when the leading verb is unknown: the first telling keyword anywhere."""
    wset = set(words)
    if wset & {"print", "prints", "display", "displays", "output", "outputs", "show", "shows", "prompt"} and not (
            wset & {"scanner"}):
        return KIND_FORMAT
    if wset & {"scanner", "input", "reads", "read", "keyboard"}:
        return KIND_INPUT
    if wset & {"switch", "else", "ifelse"}:
        return KIND_SELECT
    if wset & {"calculate", "compute", "sum", "average", "multiply", "divide", "random", "formula"}:
        return KIND_CALC
    if wset & {"array", "arrays", "variable", "variables", "constant", "constants"}:
        return KIND_DATA
    return None


# ---------------------------------------------------------------------------
# Error kinds, from the model error's code (and definition)
# ---------------------------------------------------------------------------

#: Code token -> requirement kinds it can overlap. The first token found in the (upper-cased)
#: code wins, so list the specific ones first. The tokens are shared by the CSC134, CSC151 and
#: CSC251 vocabularies; COURSE_ERROR_KINDS refines them per course.
_ERROR_KIND_TOKENS: tuple[tuple[str, frozenset], ...] = (
    ("OUTPUT_FORMATTING", frozenset({KIND_FORMAT})),
    ("DECIMAL", frozenset({KIND_FORMAT})),
    ("FORMATTING", frozenset({KIND_FORMAT})),
    ("OUTPUT_IMPACT", frozenset({KIND_CALC, KIND_SELECT})),
    ("CALCULATION", frozenset({KIND_CALC, KIND_SELECT})),
    ("SEQUENCE", frozenset({KIND_SELECT, KIND_CALC, KIND_LOOP})),
    ("SELECTION", frozenset({KIND_SELECT, KIND_CALC, KIND_LOOP})),
    ("INPUT_VALIDATION", frozenset({KIND_VALID})),
    ("FUNCTION_PROTOTYPE", frozenset({KIND_FUNC})),
    ("METHOD_ERRORS", frozenset({KIND_FUNC})),
    ("SCANNER", frozenset({KIND_INPUT})),
    ("INCORRECT_DATA_TYPE", frozenset({KIND_INPUT, KIND_DATA})),
)

#: Codes that are never an overlap with a requirement: style, structure and compile errors.
_NEVER_TOKENS = ("DOES_NOT_COMPILE", "COMPILE", "SYNTAX", "NAMING", "DOCUMENTATION", "CONSTANT", "STYLE",
                 "MISSPELL", "INEFFICIENT", "CURLY", "FILENAME", "FILE_CLASS", "UNUSED", "STALE", "SECURITY",
                 "CLASS_DESIGN", "ARRAYLIST", "AGGREGATION")

#: Per-course overrides, keyed by the course prefix of the error code. A course lists only what
#: differs from the shared tokens; ``None`` means "this code never overlaps a requirement".
COURSE_ERROR_KINDS: dict[str, dict[str, Optional[frozenset]]] = {
    "CSC_251": {
        # CSC251 formats money with commas and a scale: the decimal-scale code is an output format.
        "DECIMAL_SCALE": frozenset({KIND_FORMAT}),
        "MINOR_FORMATTING": frozenset({KIND_FORMAT}),
    },
    "CSC_134": {
        # CSC134 prototypes are declared and called: a function requirement.
        "FUNCTION_PROTOTYPE_ERROR": frozenset({KIND_FUNC}),
    },
}

_DESCRIPTION_HINTS: tuple[tuple[tuple[str, ...], frozenset], ...] = (
    (("decimal", "output format"), frozenset({KIND_FORMAT})),
    (("validate", "validation"), frozenset({KIND_VALID})),
    (("function", "method", "prototype"), frozenset({KIND_FUNC})),
    (("scanner",), frozenset({KIND_INPUT})),
    (("calculation",), frozenset({KIND_CALC, KIND_SELECT})),
)


def _course_prefix(code: str) -> str:
    parts = code.split("_")
    return "_".join(parts[:2]) if len(parts) >= 2 else ""


def error_kinds(code: str, definition=None) -> frozenset:
    """The requirement kinds a model error code can overlap; empty = never paired.

    Order: per-course override, then the shared code tokens, then (for a custom definition no
    token recognises) keywords in its description. Style, structure and compile codes are
    never paired whatever their description says.
    """
    up = (code or "").strip().upper()
    if not up or is_requirement_error(up):
        return frozenset()
    over = COURSE_ERROR_KINDS.get(_course_prefix(up), {})
    for token, kinds in over.items():
        if token in up:
            return frozenset(kinds or ())
    for token in _NEVER_TOKENS:
        if token in up:
            return frozenset()
    for token, kinds in _ERROR_KIND_TOKENS:
        if token in up:
            return kinds
    desc = (getattr(definition, "description", None) or "").lower() if definition is not None else ""
    if desc:
        for needles, kinds in _DESCRIPTION_HINTS:
            if any(n in desc for n in needles):
                return kinds
    return frozenset()


# ---------------------------------------------------------------------------
# The stage
# ---------------------------------------------------------------------------

_RANK = {"major": 2, "minor": 1}


@dataclass
class OverlapInfo:
    mode: str = DEFAULT_MODE
    classified: int = 0           # requirements with a kind
    requirements: int = 0         # non-met requirements seen
    matched: list = field(default_factory=list)      # (requirement id, kept code, dropped code)
    ambiguous: list = field(default_factory=list)    # requirement ids with 0 < candidates != 1 one-to-one
    dropped: list = field(default_factory=list)      # codes actually removed (drop mode only)

    def as_dict(self) -> dict:
        return {"mode": self.mode, "requirements": self.requirements, "classified": self.classified,
                "matched": [list(m) for m in self.matched], "ambiguous": list(self.ambiguous),
                "dropped": list(self.dropped)}


def _requirement_id(code: str) -> Optional[str]:
    for base in (MISSING_REQUIREMENT_ID, PARTIAL_REQUIREMENT_ID):
        if code.startswith(base + "_"):
            return code[len(base) + 1:].strip().upper()
    return None


def _severity(error: DetectedError) -> int:
    return _RANK.get((error.severity or "").strip().lower(), 0)


def _is_compile_like(error: DetectedError) -> bool:
    from cqc_cpcc.utilities.compiler_gate import is_compile_error
    return bool(is_compile_error(error.code, error.name))


def reconcile_overlaps(
        result: RubricAssessmentResult,
        checklist: Optional[RequirementChecklist],
        error_definitions: Optional[list] = None,
        mode: Optional[str] = None,
) -> tuple[RubricAssessmentResult, list[ReconciliationNote], OverlapInfo]:
    """Pair requirement errors with the model errors that describe the same defect.

    Must run AFTER ``apply_requirement_coverage`` and BEFORE ``apply_backend_scoring``. Pure and
    deterministic. Returns ``(result, notes, info)``: in ``drop`` mode ``result`` has the lesser
    error of each unambiguous pair removed (each one a suppressed note, so Reinstate works); in
    ``notes`` mode ``result`` is returned unchanged and ``notes`` explain what drop mode would do.
    """
    mode = mode if mode in MODES else overlap_mode()
    info = OverlapInfo(mode=mode)
    if mode == MODE_OFF or not checklist or not checklist.requirements:
        return result, [], info

    errors = list(result.detected_errors or [])
    defs = {(getattr(d, "error_id", "") or "").upper(): d for d in (error_definitions or [])}
    texts = {(r.id or "").strip().upper(): r.text for r in checklist.requirements}

    req_errors: dict[str, DetectedError] = {}
    for e in errors:
        rid = _requirement_id((e.code or "").strip().upper())
        if rid is not None and rid in texts:
            req_errors[rid] = e
    kinds_of_req = {rid: requirement_kind(texts[rid]) for rid in req_errors}
    info.requirements = len(req_errors)
    info.classified = sum(1 for k in kinds_of_req.values() if k)

    # Model errors that may pair: the model's own, not backed by hard evidence, not compile.
    model_errors: dict[str, frozenset] = {}
    for e in errors:
        code = (e.code or "").strip().upper()
        if is_requirement_error(code) or not may_drop(e) or e.source in ("requirement",) or _is_compile_like(e):
            continue
        ks = error_kinds(code, defs.get(code))
        if ks:
            model_errors[code] = ks

    by_code = {(e.code or "").strip().upper(): e for e in errors}
    # candidates: requirement id -> model codes, and model code -> requirement ids
    cand_of_req: dict[str, list[str]] = {}
    cand_of_err: dict[str, list[str]] = {}
    for rid, kind in kinds_of_req.items():
        if not kind:
            continue
        for code, ks in model_errors.items():
            if kind in ks:
                cand_of_req.setdefault(rid, []).append(code)
                cand_of_err.setdefault(code, []).append(rid)

    notes: list[ReconciliationNote] = []
    drop_codes: set[str] = set()
    for rid in sorted(cand_of_req):
        codes = cand_of_req[rid]
        req_err = req_errors[rid]
        unambiguous = len(codes) == 1 and len(cand_of_err[codes[0]]) == 1
        if not unambiguous:
            info.ambiguous.append(rid)
            names = ", ".join(sorted(by_code[c].name or c for c in codes))
            notes.append(note(STAGE, f"Possible overlap: requirement {rid} may describe the same issue as "
                                     f"{names}. The match is not certain, so both are counted.",
                              requirement_id=rid, code=req_err.code))
            continue
        model_err = by_code[codes[0]]
        missing = (req_err.code or "").upper().startswith(MISSING_REQUIREMENT_ID)
        # Lesser of the pair goes; a tie keeps the requirement error (rule A'). A missing
        # requirement is never the one dropped.
        if _severity(model_err) > _severity(req_err) and not missing:
            gone, kept = req_err, model_err
        else:
            gone, kept = model_err, req_err
        info.matched.append((rid, kept.code, gone.code))
        why = (f"Requirement {rid} and {model_err.name or model_err.code} describe the same issue: "
               f"counted once, as {kept.name or kept.code}.")
        if mode == MODE_DROP:
            drop_codes.add((gone.code or "").strip().upper())
            info.dropped.append(gone.code)
            notes.append(note_removed(STAGE, gone, why, requirement_id=rid, kept_code=kept.code))
        else:
            notes.append(note(STAGE, "Possible overlap (not applied): " + why, requirement_id=rid,
                              code=gone.code, kept_code=kept.code))

    if drop_codes:
        kept_errors = [e for e in errors if (e.code or "").strip().upper() not in drop_codes]
        result = result.model_copy(update={
            "detected_errors": kept_errors,
            "error_counts_by_severity": None,  # recompute from the corrected error list
            "error_counts_by_id": None,
        })
    return result, notes, info
