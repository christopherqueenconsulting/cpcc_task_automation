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

KIND_FORMAT = "format"   # explicit format wording: decimals, currency, separators, spacing, sample output
KIND_PRINT = "print"     # prints or shows something, no format wording: never pairs with a formatting code
KIND_INPUT = "input"     # reading or prompting for values
KIND_VALID = "valid"     # rejecting or re-asking for bad input
KIND_FUNC = "func"       # functions and methods
KIND_SELECT = "select"   # decisions, lookups, conditional rules
KIND_LOOP = "loop"       # repetition
KIND_CALC = "calc"       # arithmetic, counting, converting, random values
KIND_DATA = "data"       # variables, arrays, starting values
ALL_KINDS = (KIND_FORMAT, KIND_PRINT, KIND_INPUT, KIND_VALID, KIND_FUNC, KIND_SELECT, KIND_LOOP, KIND_CALC, KIND_DATA)


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


_NEGATION_WORDS = frozenset({"never", "without", "avoid", "dont", "cannot", "cant", "no", "nor"})
#: "not" negates an instruction after one of these ("must not", "do not"); "is not C or F" is a condition.
_NEGATING_BEFORE_NOT = frozenset({"must", "may", "should", "shall", "do", "does", "can", "will", "would",
                                  "could", "need", "are", "mustnt"})
_VALID_WORDS = frozenset({"validate", "validates", "validation", "reject", "rejects", "refuse", "refuses",
                          "invalid", "reprompt", "reprompting", "sanitize"})
_FUNC_WORDS = frozenset({"function", "functions", "method", "methods", "prototype", "prototypes"})
_LOOP_WORDS = frozenset({"loop", "loops", "repeat", "repeats", "repeatedly", "until", "dowhile"})
_FORMAT_CUES = frozenset({"currency", "decimal", "decimals", "dollar", "dollars", "separator", "separators",
                          "spacing", "alignment", "aligned", "align", "format", "formatted", "formatting"})
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


def _is_negated(words: list[str]) -> bool:
    """True when the requirement forbids or excludes something ("must not use loops", "without
    functions", "No global variables", "instead of", "rather than"). Such a requirement is left alone."""
    if set(words) & _NEGATION_WORDS:
        return True
    if _has_phrase(words, "instead", "of") or _has_phrase(words, "rather", "than"):
        return True
    return any(w == "not" and i > 0 and words[i - 1] in _NEGATING_BEFORE_NOT for i, w in enumerate(words))


def requirement_kind(text: str) -> Optional[str]:
    """The kind of a requirement from its wording, or ``None`` when it is not clear.

    Plain keyword and leading-verb rules, in order; no regular expressions. ``None`` means
    "leave this requirement alone".
    """
    words = _words(text)
    if not words:
        return None
    if _is_negated(words):
        return None
    wset = set(words)

    # Validation wording wins: "Print an error message if ..." is about rejecting bad input.
    if wset & _VALID_WORDS or _has_phrase(words, "error", "message") or _has_phrase(words, "keep", "prompting"):
        return KIND_VALID
    # Functions and methods (but "String methods" is a library constraint, not a function requirement).
    if _has_phrase(words, "string", "methods"):
        return None
    if wset & _FUNC_WORDS:
        return KIND_FUNC
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

    # Explicit format wording (decimals, currency, spacing, sample output) is a format requirement
    # only when the requirement is about printing. "Calculate the payment as currency" mixes a
    # calculation with a format: unclear, so left alone.
    if wset & _FORMAT_CUES or _has_phrase(words, "sample", "output"):
        if lead in _CALC_VERBS or lead in _SELECT_VERBS or lead in _DATA_VERBS or lead in _INPUT_VERBS:
            return None
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
        return KIND_PRINT
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
        return KIND_PRINT
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
    ("INCORRECT_DATA_TYPE", frozenset({KIND_DATA})),
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

#: Whole-word hints for a custom definition no code token recognises. A definition that talks
#: about style, comments or naming never pairs, whatever else its text says.
_STYLE_WORDS = frozenset({"comment", "comments", "commenting", "commented", "naming", "name", "names", "indent",
                          "indentation", "indented", "documentation", "document", "style", "spelling",
                          "misspelled", "whitespace", "convention", "conventions"})
_DESCRIPTION_HINTS: tuple[tuple[frozenset, frozenset], ...] = (
    (frozenset({"decimal", "decimals", "currency", "separator", "separators"}), frozenset({KIND_FORMAT})),
    (frozenset({"validate", "validation", "validating"}), frozenset({KIND_VALID})),
    (frozenset({"function", "functions", "method", "methods", "prototype", "prototypes"}), frozenset({KIND_FUNC})),
    (frozenset({"scanner"}), frozenset({KIND_INPUT})),
    (frozenset({"calculation", "calculations"}), frozenset({KIND_CALC, KIND_SELECT})),
)

#: Codes that say "something is wrong with the output or the logic" in general. A kind match alone
#: is too weak for these: the error's own description must share a content word with the
#: requirement before a drop is allowed (otherwise it is only a possible-overlap note).
_CATCH_ALL_TOKENS = ("OUTPUT_IMPACT", "CALCULATION", "SEQUENCE", "SELECTION")


def is_catch_all(code: str) -> bool:
    up = (code or "").upper()
    return any(t in up for t in _CATCH_ALL_TOKENS)


_STOP_WORDS = frozenset({"the", "and", "that", "this", "with", "from", "into", "when", "what", "does", "not",
                         "are", "was", "for", "its", "has", "have", "which", "their", "them", "then", "than",
                         "will", "should", "must", "program", "code", "error", "errors", "output", "value",
                         "values", "number", "each", "every", "any", "all", "but", "can", "also", "user"})


def _content_words(text: str) -> set:
    return {w for w in _words(text) if len(w) >= 4 and w not in _STOP_WORDS}


def shares_content_word(error: DetectedError, requirement_text: str) -> bool:
    """Whether the error's own description or notes mention something the requirement names."""
    own = _content_words(f"{error.description or ''} {error.notes or ''}")
    return bool(own & _content_words(requirement_text))


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
    if definition is not None:
        text = f"{getattr(definition, 'name', '') or ''} {getattr(definition, 'description', '') or ''}"
        dwords = set(_words(text))
        if not dwords & _STYLE_WORDS:
            for needles, kinds in _DESCRIPTION_HINTS:
                if dwords & needles:
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
    unconfirmed: list = field(default_factory=list)  # requirement ids paired by a general code, no shared wording
    dropped: list = field(default_factory=list)      # codes actually removed (drop mode only)

    def as_dict(self) -> dict:
        return {"mode": self.mode, "requirements": self.requirements, "classified": self.classified,
                "matched": [list(m) for m in self.matched], "ambiguous": list(self.ambiguous),
                "unconfirmed": list(self.unconfirmed), "dropped": list(self.dropped)}


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
    # Every entry of a code is kept (the model may list one code twice): a drop removes exactly
    # these entries and leaves one note per entry, so Reinstate can put each back.
    entries_of: dict[str, list[DetectedError]] = {}
    model_errors: dict[str, frozenset] = {}
    for e in errors:
        code = (e.code or "").strip().upper()
        if is_requirement_error(code) or not may_drop(e) or e.source in ("requirement",) or _is_compile_like(e):
            continue
        ks = error_kinds(code, defs.get(code))
        if ks:
            model_errors[code] = ks
            entries_of.setdefault(code, []).append(e)

    def _rep(code_entries: list) -> DetectedError:
        """The entry that stands for a code: its most severe one (scoring counts a code once, as its worst)."""
        return max(code_entries, key=_severity)

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
    drop_ids: set[int] = set()
    for rid in sorted(cand_of_req):
        codes = cand_of_req[rid]
        req_err = req_errors[rid]
        req_label = f'Requirement {rid} ("{texts[rid]}")'
        unambiguous = len(codes) == 1 and len(cand_of_err[codes[0]]) == 1
        if not unambiguous:
            info.ambiguous.append(rid)
            names = ", ".join(sorted(_rep(entries_of[c]).name or c for c in codes))
            notes.append(note(STAGE, f"Possible overlap: {req_label} may describe the same issue as {names}. "
                                     "The match is not certain, so both are counted.",
                              requirement_id=rid, code=req_err.code))
            continue
        code = codes[0]
        model_entries = entries_of[code]
        model_err = _rep(model_entries)
        if is_catch_all(code) and not any(shares_content_word(e, texts[rid]) for e in model_entries):
            # A general code ("output is wrong") says nothing about WHICH output: not enough to drop.
            info.unconfirmed.append(rid)
            notes.append(note(STAGE, f"Possible overlap: {req_label} and {model_err.name or code} are both "
                                     "about the program's behaviour, but the error does not mention the "
                                     "same thing, so both are counted.",
                              requirement_id=rid, code=req_err.code))
            continue
        missing = (req_err.code or "").upper().startswith(MISSING_REQUIREMENT_ID)
        # Lesser of the pair goes; a tie keeps the requirement error (rule A'). A MISSING_REQUIREMENT
        # error (a core requirement not implemented) is never the one dropped.
        if missing:
            gone_entries, kept = model_entries, req_err
        elif _severity(model_err) > _severity(req_err):
            gone_entries, kept = [req_err], model_err
        else:
            gone_entries, kept = model_entries, req_err
        info.matched.append((rid, kept.code, gone_entries[0].code))
        pair = f"{req_label} and {model_err.name or code} describe the same issue"
        once = f"counted once, as {kept.name or kept.code}"
        if mode == MODE_DROP:
            drop_ids.update(id(g) for g in gone_entries)
            info.dropped.append(gone_entries[0].code)
            for g in gone_entries:
                times = f" (listed {g.occurrences} times)" if (g.occurrences or 1) > 1 else ""
                notes.append(note_removed(STAGE, g, f"{pair}: {once}.{times}", requirement_id=rid, kept_code=kept.code))
        else:
            notes.append(note(STAGE, f"Possible overlap (not applied; both are counted now): {pair}. "
                                     f"With drop mode on they would be {once}.", requirement_id=rid,
                              code=gone_entries[0].code, kept_code=kept.code))

    if drop_ids:
        kept_errors = [e for e in errors if id(e) not in drop_ids]
        result = result.model_copy(update={
            "detected_errors": kept_errors,
            "error_counts_by_severity": None,  # recompute from the corrected error list
            "error_counts_by_id": None,
        })
    return result, notes, info
