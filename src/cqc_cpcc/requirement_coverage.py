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

Style, naming, formatting and documentation are deliberately NOT requirements: the
error definitions already cover them, and listing them twice would double-penalize.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field

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


class RequirementChecklist(BaseModel):
    requirements: Annotated[
        list[RequirementItem],
        Field(description=f"Between 1 and {MAX_REQUIREMENTS} functional requirements")
    ]


def instructions_hash(instructions: str) -> str:
    return hashlib.sha256((instructions or "").strip().encode("utf-8")).hexdigest()


def checklist_hash(checklist: Optional[RequirementChecklist]) -> Optional[str]:
    """Stable hash of a checklist (for the grading run key); None when there is none."""
    if not checklist or not checklist.requirements:
        return None
    payload = json.dumps([r.model_dump() for r in checklist.requirements], sort_keys=True)
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


def normalize_checklist(checklist: RequirementChecklist) -> RequirementChecklist:
    """Drop blanks, cap the length and make ids unique (case-insensitively)."""
    seen: set[str] = set()
    items: list[RequirementItem] = []
    n = 0
    for r in checklist.requirements or []:
        text = (r.text or "").strip()
        if not text:
            continue
        rid = (r.id or "").strip().upper()
        while not rid or rid in seen:
            n += 1
            rid = f"R{n}"
        seen.add(rid)
        items.append(RequirementItem(id=rid, text=text, weight=r.weight))
        if len(items) >= MAX_REQUIREMENTS:
            break
    return RequirementChecklist(requirements=items)


def requirements_prompt_section(checklist: Optional[RequirementChecklist]) -> list[str]:
    """Prompt lines asking the model to mark each requirement."""
    if not checklist or not checklist.requirements:
        return []
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
    for r in checklist.requirements:
        lines.append(f"- **{r.id}** ({r.weight}): {r.text}")
    lines.append("")
    return lines


def apply_requirement_coverage(
        result: RubricAssessmentResult,
        checklist: Optional[RequirementChecklist],
) -> tuple[RubricAssessmentResult, dict]:
    """Turn requirement statuses into errors the error-count scoring understands.

    Must run BEFORE ``apply_backend_scoring``. Requirements the model did not mark are
    reported in ``info["unmarked"]`` and do not change the score (no guessing).
    """
    info = {"applied": False, "missing": [], "partial": [], "unmarked": []}
    if not checklist or not checklist.requirements:
        return result, info

    by_id = {_id_key(r.requirement_id): r for r in (result.requirement_results or [])}
    errors = [e for e in (result.detected_errors or []) if not is_requirement_error(e.code)]
    for req in checklist.requirements:
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
            description=f"{req.text} ({'not implemented' if verdict.status == 'missing' else 'incomplete'})",
            occurrences=1,
            notes=verdict.evidence,
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

    info["applied"] = True
    result = result.model_copy(update={
        **update,
        "error_counts_by_severity": None,  # recompute from the corrected error list
        "error_counts_by_id": None,
    })
    return result, info
