#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Requirement coverage: incomplete work must cost points, not just buggy work.

Rubric scoring only subtracts points for detected errors, so a student who wrote half
the program (few lines, few errors) could outscore a student who wrote all of it with
some mistakes. Coverage fixes that:

1. Once per assignment, the instructions are turned into a short checklist of the
   FUNCTIONAL requirements (what the program must do). The instructor can edit it.
2. During grading the model marks each requirement met / partial / missing.
3. The backend (deterministic) turns those statuses into errors that the existing
   error-count scoring already understands:

       missing core requirement                    -> MISSING_REQUIREMENT_<id> (major)
       partial requirement, or missing secondary   -> PARTIAL_REQUIREMENT_<id> (minor)

A requirement the instructor's solution does not do (``graded`` False, set from
``requirement_solution_check``) is listed as not required and never costs a student anything;
the decision is recorded as a "solution" reconciliation note.

Style, naming, formatting and documentation are deliberately NOT requirements: the
error definitions already cover them, and listing them twice would double-penalize.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

from cqc_cpcc.rubric_models import DetectedError, RubricAssessmentResult
from cqc_cpcc.utilities.logger import logger

MISSING_REQUIREMENT_ID = "MISSING_REQUIREMENT"
PARTIAL_REQUIREMENT_ID = "PARTIAL_REQUIREMENT"
REQUIREMENT_ERROR_IDS = frozenset({MISSING_REQUIREMENT_ID, PARTIAL_REQUIREMENT_ID})


def requirement_error_code(base: str, requirement_id: str) -> str:
    """Per-requirement error code, e.g. ``MISSING_REQUIREMENT_R2``.

    Scoring counts each error CODE once (``normalize_detected_errors_for_scoring``), so
    every requirement needs its own code or three missing requirements would cost the
    same as one.
    """
    return f"{base}_{requirement_id}"


def is_requirement_error(code: Optional[str]) -> bool:
    return any((code or "").startswith(base) for base in REQUIREMENT_ERROR_IDS)

MAX_REQUIREMENTS = 12


class RequirementItem(BaseModel):
    id: Annotated[str, Field(description="Short stable id: R1, R2, ...")]
    text: Annotated[str, Field(description="One observable thing the program must do")]
    weight: Annotated[
        Literal["core", "secondary"],
        Field(description="core = central to the assignment; secondary = a smaller required detail")
    ]
    # Backend-only (never in the model's schema, so the extraction and grading prompts are
    # unchanged). No docstring on this class: a docstring would become the schema description.
    # ``graded`` False = the instructor's solution does not do this, so it is not required.
    graded: SkipJsonSchema[bool] = True
    # What the solution check found for this item: yes, no, unclear; None = never checked.
    solution_status: SkipJsonSchema[Optional[Literal["yes", "no", "unclear"]]] = None
    solution_evidence: SkipJsonSchema[Optional[str]] = None
    # The instructor removed this requirement from the list to grade. A check never changes it.
    hand_ungraded: SkipJsonSchema[bool] = False


class RequirementChecklist(BaseModel):
    requirements: Annotated[
        list[RequirementItem],
        Field(description=f"Between 1 and {MAX_REQUIREMENTS} functional requirements")
    ]


def instructions_hash(instructions: str) -> str:
    return hashlib.sha256((instructions or "").strip().encode("utf-8")).hexdigest()


def _hash_fields(item: "RequirementItem") -> dict:
    data = {"id": item.id, "text": item.text, "weight": item.weight}
    if not item.graded:
        data["graded"] = False
    return data


def checklist_hash(checklist: Optional[RequirementChecklist]) -> Optional[str]:
    """Stable hash of a checklist (for the grading run key); None when there is none."""
    if not checklist or not checklist.requirements:
        return None
    # The solution check's verdicts and evidence do not change how a student is graded, so they
    # stay out of the hash; ``graded`` enters only when False. A checklist nobody changed hashes
    # exactly as it did before these fields existed (cached results stay valid).
    payload = json.dumps([_hash_fields(r) for r in checklist.requirements], sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_requirement_extraction_prompt(instructions: str) -> str:
    return "\n".join([
        "# Requirement Checklist Extraction",
        "Turn the assignment instructions below into a checklist of the FUNCTIONAL "
        "requirements a grader can check in the student's program.",
        "",
        "Rules:",
        f"- 1 to {MAX_REQUIREMENTS} requirements, ids R1, R2, ... in the order the instructions give them.",
        "- When the instructions number or bullet their tasks, make exactly one requirement per "
        "functional item, in the same order: do not split one item into several or merge items. "
        "Skip items that are only about style, naming, constants, formatting or comments.",
        "- Each requirement is one observable behavior or required program element "
        "(an input it reads, a calculation, a decision, a loop, a function/method/class "
        "it must define, an output it must produce).",
        "- Only include what the instructions explicitly require. Do not invent requirements.",
        "- EXCLUDE style, naming, named constants vs literal values, formatting, indentation, "
        "braces, comments/documentation and file naming: those are graded separately by "
        "error definitions.",
        "- weight 'core' for what the assignment is about; 'secondary' for smaller required details.",
        "- Write each requirement as a short sentence a student would understand.",
        "",
        "## Assignment Instructions",
        instructions or "",
    ])


_CHECKLIST_CACHE: dict[str, RequirementChecklist] = {}


async def extract_requirements(instructions: str, model_name: Optional[str] = None,
                               use_cache: bool = True) -> RequirementChecklist:
    """Extract (and cache by instructions hash) the requirement checklist.

    ``use_cache=False`` always calls the model (the prompt evaluation repeats calls).
    """
    from cqc_cpcc.utilities.AI import llm_gateway

    key = f"{instructions_hash(instructions)}:{model_name or ''}"
    if use_cache and key in _CHECKLIST_CACHE:
        return _CHECKLIST_CACHE[key]
    checklist = await llm_gateway.structured(
        role="grading",
        prompt=build_requirement_extraction_prompt(instructions),
        schema_model=RequirementChecklist,
        override=model_name,
        prompt_id="requirement-extraction",
    )
    checklist = normalize_checklist(checklist)
    _CHECKLIST_CACHE[key] = checklist
    return checklist


def _id_key(requirement_id: Optional[str]) -> str:
    """Canonical form for matching ids: the model may answer 'r1' or 'R1 ' for R1."""
    return (requirement_id or "").strip().upper()


_ID_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
MAX_ID_CHARS = 12


def safe_requirement_id(raw: Optional[str]) -> str:
    """The id in upper case when it is 1 to 12 letters, digits, ``_`` or ``-``; else ``""``.
    A loop over the characters, not a regular expression. Ids end up in labels and captions, so
    anything else (Markdown, a URL, spaces) is replaced by a generated id."""
    rid = (raw or "").strip().upper()
    if not rid or len(rid) > MAX_ID_CHARS or any(ch not in _ID_CHARS for ch in rid):
        return ""
    return rid


def assign_ids(raw_ids: list, reserved=()) -> list:
    """``[(id, own)]`` for rows in order. A row keeps its own id (``own`` True) when it is safe and
    no earlier row has it; otherwise it gets the next free ``R{n}`` (``own`` False), skipping the
    ids of every row (so a real id later in the list is never taken), the ``reserved`` ids and the
    ones already handed out. A generated id never belongs to a row that already had it."""
    safe = [safe_requirement_id(raw) for raw in raw_ids]
    taken = {s for s in safe if s} | {(r or "").strip().upper() for r in reserved}
    seen: set[str] = set()
    out = []
    for sid in safe:
        if sid and sid not in seen:
            seen.add(sid)
            out.append((sid, True))
            continue
        n = 1
        while f"R{n}" in taken or f"R{n}" in seen:
            n += 1
        new = f"R{n}"
        seen.add(new)
        out.append((new, False))
    return out


def normalize_checklist(checklist: RequirementChecklist) -> RequirementChecklist:
    """Drop blanks, cap the length and make ids unique (case-insensitively)."""
    rows = [r for r in (checklist.requirements or []) if (r.text or "").strip()]
    ids = assign_ids([r.id for r in rows])
    items: list[RequirementItem] = []
    for r, (rid, _own) in zip(rows, ids):
        text = (r.text or "").strip()
        items.append(RequirementItem(id=rid, text=text, weight=r.weight, graded=r.graded,
                                     solution_status=r.solution_status,
                                     solution_evidence=r.solution_evidence,
                                     hand_ungraded=r.hand_ungraded))
        if len(items) >= MAX_REQUIREMENTS:
            break
    return RequirementChecklist(requirements=items)


def requirements_prompt_section(checklist: Optional[RequirementChecklist]) -> list[str]:
    """Prompt lines asking the model to mark each requirement.

    Items the instructor's solution does not do (``graded`` False) are listed apart as not
    required and are not marked. With none of those the text is exactly what it always was.
    """
    if not checklist or not checklist.requirements:
        return []
    graded = [r for r in checklist.requirements if r.graded]
    not_required = [r for r in checklist.requirements if not r.graded]
    if not_required and not graded:
        lines = ["## Requirement Checklist",
                 "No requirement needs a mark for this assignment: leave `requirement_results` empty.",
                 ""]
    else:
        lines = [
            "## Requirement Checklist",
            "Mark EVERY requirement below in `requirement_results` with its exact id:",
            "- met: fully implemented and working as required",
            "- partial: attempted, but incomplete or not working as required",
            "- missing: not implemented at all",
            "Unimplemented functionality is a MISSING requirement, not an absence of errors: "
            "a short or unfinished program must have its unimplemented requirements marked missing. "
            "Do not ALSO report a detected error for functionality that is entirely missing; "
            "the missing requirement already accounts for it. Likewise, do not mark a "
            "requirement partial only because of an error you already reported for it.",
            "",
        ]
        for r in graded:
            lines.append(f"- **{r.id}** ({r.weight}): {r.text}")
        lines.append("")
    if not_required:
        lines.append("## Not required (the solution does not do these)")
        lines.append("The instructor's solution does not do the following, so they are not required "
                     "and a submission is not graded on them. Do not mark them in "
                     "`requirement_results`, and do not report a detected error because a "
                     "submission lacks them:")
        for r in not_required:
            lines.append(f"- **{r.id}**: {r.text}")
        lines.append("")
    return lines


SUMMARY_MAX_WORDS = 18
_LEAD_ID = re.compile(r"^\s*(?:\(?\[?R\d+\]?\)?\s*[:.)\-]\s*|\(?R\d+\)?\s+)", re.IGNORECASE)
_LEAD_MODAL = re.compile(
    r"^(?:the\s+)?(?:program|code|application|app|solution|student(?:'s program)?|submission)\s+"
    r"(?:must|should|shall|needs to|has to|will)\s+", re.IGNORECASE)
_LEAD_MUST = re.compile(r"^(?:must|should|shall|needs to|has to)\s+", re.IGNORECASE)
# Only ";" and sentence ends split a requirement. Commas and colons stay: cutting at ", and" or
# ":" produced fragments that read as the whole requirement.
_CLAUSE_SPLIT = re.compile(r"\s*;\s*|(?<=[.!?])\s+")
_PAREN = re.compile(r"\s*[\(\[]([^\)\]]*)[\)\]]")
_KEEP_PAREN = re.compile(r"\d|inclusive|exclusive|rounded", re.IGNORECASE)
_CONDITION = re.compile(r"\b(?:if|when|unless|otherwise|else|then|also)\b", re.IGNORECASE)
_PROHIBITION = re.compile(r"^(?:do not|don't|never|avoid)\b", re.IGNORECASE)
# A summary never ends on one of these (articles, prepositions, comparators, joiners, "is/are/be").
_DANGLING = frozenset({
    "a", "an", "the", "and", "or", "but", "nor", "of", "to", "for", "with", "in", "on", "at", "by", "from",
    "as", "into", "onto", "over", "under", "per", "via", "that", "which", "than", "between", "is", "are",
    "be", "was", "were", "if", "when", "then", "so", "using", "less", "greater", "more", "fewer", "larger",
    "smaller", "equal", "not", "each", "every", "any", "all", "its", "their", "his", "her", "your", "this",
    "these", "those", "both", "either", "neither", "also", "both"})
_CUT_BEFORE = frozenset({"that", "to", "is", "are", "between", "than"})
_LABEL_VERBS = frozenset({
    "print", "display", "show", "read", "ask", "prompt", "use", "call", "compute", "calculate", "write",
    "validate", "store", "create", "define", "include", "return", "accept", "handle", "loop", "check",
    "do", "don't", "never", "avoid", "must", "should"})
ELLIPSIS = "…"


def _tidy_parentheses(text: str) -> str:
    """Drop parentheticals, except a short one (4 words or fewer) that carries a number or an
    inclusive/exclusive/rounded note: it can change what the requirement means."""
    def keep(m) -> str:
        inner = " ".join(m.group(1).split())
        if inner and len(inner.split()) <= 4 and _KEEP_PAREN.search(inner):
            return f" ({inner})"
        return ""
    return _PAREN.sub(keep, text)


def _drop_dangling(words: list) -> list:
    while words and words[-1].lower().strip(",.;:") in _DANGLING:
        words.pop()
    while words and words[-1].endswith(","):
        words[-1] = words[-1].rstrip(",")
    return words


def _shorten(text: str, limit: int) -> tuple:
    """``(text, was_cut)`` of at most ``limit`` words: whole when it fits, else cut at the last
    comma within the limit, else before the last that/to/is/are/between/than, else at the limit."""
    words = text.split()
    if len(words) <= limit:
        return text, False
    head = words[:limit]
    cut = next((i + 1 for i in range(len(head) - 1, 2, -1) if head[i].endswith(",")), None)
    if cut is None:
        cut = next((i for i in range(len(head) - 1, 2, -1) if head[i].lower() in _CUT_BEFORE), limit)
    return " ".join(_drop_dangling(head[:cut])), True


def student_summary(text: str) -> str:
    """A plain-language phrase for a requirement, for text the student reads.

    Deterministic stand-in until the model writes a ``summary`` (plan PR 4). It never states
    less than it claims: it splits only on ";" and sentence ends; a text of
    :data:`SUMMARY_MAX_WORDS` words or fewer is kept whole; a clause that holds a condition or a
    sequence word (if, when, unless, otherwise, else, then, also) is kept; whatever else is cut
    ends in an ellipsis; and it never ends on an article, preposition, comparator or "is/are/be".
    """
    one = " ".join((text or "").split())
    one = _tidy_parentheses(_LEAD_ID.sub("", one)).strip()
    # A short noun label before ":" ("Output format: ...") is not part of the requirement.
    label, sep, rest = one.partition(":")
    if sep and rest.strip() and len(label.split()) <= 3 and label.split()[0].lower() not in _LABEL_VERBS:
        one = rest.strip()
    clauses = [c.strip() for c in _CLAUSE_SPLIT.split(one) if c.strip()]
    if not clauses:
        return "a part of the assignment"
    last = 0
    for i, clause in enumerate(clauses[1:], start=1):
        if _CONDITION.search(clause):
            last = i
            break
    kept = "; ".join(c.rstrip(" .!?") for c in clauses[: last + 1])
    dropped = last + 1 < len(clauses)
    kept = _LEAD_MUST.sub("", _LEAD_MODAL.sub("", kept)).strip()
    if re.match(r"^not\b", kept, re.IGNORECASE):
        kept = "do " + kept  # "The program must not X" -> "do not X"
    out, cut = _shorten(kept, SUMMARY_MAX_WORDS)
    out = " ".join(_drop_dangling(out.split())).rstrip(" ,.;:!")
    if not out:
        return "a part of the assignment"
    out = _lower_first(out, one)
    return out + ELLIPSIS if (dropped or cut) else out


def _lower_first(out: str, original: str) -> str:
    """Mid-sentence use: lower the first letter, unless the word is an acronym (SQL), is followed by
    a colon, or is capitalised elsewhere in the text (Scanner, Java)."""
    first = out.split()[0]
    bare = first.strip(",:;()")
    if not (bare[:1].isupper() and bare[1:2].islower()) or first.endswith(":"):
        return out
    if bare in original.split()[1:] or bare in [w.strip(",:;().") for w in original.split()[1:]]:
        return out
    return out[0].lower() + out[1:]


def requirement_error_description(summary: str, status: str) -> str:
    """The sentence the student reads for a requirement error (no checklist words, no id).

    A rule ("do not ...") is broken, not missing. A part that is only partly done gets a next step.
    """
    end = "" if summary.endswith(ELLIPSIS) else "."
    if _PROHIBITION.match(summary):
        return f"This rule was not followed: {summary}{end}"
    if status == "missing":
        return f"This part is missing: {summary}{end}"
    return (f"This part is not finished yet: {summary}{end} "
            "Re-read the instructions for this part and check that your program does all of it.")


def apply_requirement_coverage(
        result: RubricAssessmentResult,
        checklist: Optional[RequirementChecklist],
) -> tuple[RubricAssessmentResult, dict]:
    """Turn requirement statuses into errors the error-count scoring understands.

    Must run BEFORE ``apply_backend_scoring``. Requirements the model did not mark are
    reported in ``info["unmarked"]`` and do not change the score (no guessing).
    """
    info = {"applied": False, "missing": [], "partial": [], "unmarked": []}
    ungraded: list[str] = []
    if not checklist or not checklist.requirements:
        return result, info

    by_id = {_id_key(r.requirement_id): r for r in (result.requirement_results or [])}
    errors = [e for e in (result.detected_errors or []) if not is_requirement_error(e.code)]
    for req in checklist.requirements:
        if not req.graded:
            # The solution does not do it: never a deduction, whatever the model answered.
            ungraded.append(req.id)
            continue
        verdict = by_id.get(_id_key(req.id))
        if verdict is None:
            info["unmarked"].append(req.id)
            continue
        if verdict.status == "met":
            continue
        major = verdict.status == "missing" and req.weight == "core"
        code = requirement_error_code(MISSING_REQUIREMENT_ID if major else PARTIAL_REQUIREMENT_ID, req.id)
        label = "missing" if verdict.status == "missing" else "partial"
        info[label].append(req.id)
        errors.append(DetectedError(
            code=code,
            name="Missing Requirement" if verdict.status == "missing" else "Incomplete Requirement",
            severity="major" if major else "minor",
            description=requirement_error_description(student_summary(req.text), verdict.status),
            occurrences=1,
            # ``verdict.evidence`` is instructor-only (it may quote the solution or the checklist):
            # never copied into text the student reads.
            notes=None,
        ))
    update = {"detected_errors": errors}
    if info["unmarked"]:
        # Unmarked requirements would silently bring back the original bug (incomplete
        # work not penalized), so the instructor must look at this student.
        logger.warning("Requirement coverage: model did not mark %s", ", ".join(info["unmarked"]))
        texts = {r.id: r.text for r in checklist.requirements}
        update.update(needs_review=True, validity_status="requirements_unmarked",
                      validity_reason="The grader did not assess: "
                                      + "; ".join(f"{rid} ({texts[rid]})" for rid in info["unmarked"])
                                      + ". Accepting keeps the score as if they were met.")

    if ungraded:
        # Only present when something is ungraded, so the info of an ordinary run is unchanged.
        info["ungraded"] = ungraded
    info["applied"] = True
    result = result.model_copy(update={
        **update,
        "error_counts_by_severity": None,  # recompute from the corrected error list
        "error_counts_by_id": None,
    })
    return result, info
