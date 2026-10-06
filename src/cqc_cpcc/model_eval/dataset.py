#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Load and validate eval cases from ``evals/datasets/<version>/cases``."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

from cqc_cpcc.model_eval.dataset_builder import AUTHORS as AUTHORS_IN_DATASET

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATASET = REPO_ROOT / "evals" / "datasets" / "v1"


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    language: str
    role: str
    course_id: str
    assignment_id: str
    rubric_id: str
    instructions: str
    files: dict  # filename -> source text
    compiles: bool
    error_ids: frozenset
    acceptable_error_ids: frozenset
    tags: tuple
    twin_of: Optional[str]
    labels_reviewed_by: Optional[str]
    max_occurrences: dict = field(default_factory=dict)  # error id -> places it appears
    expected_score_range: tuple = field(default=(0.0, 0.0))  # (low, high) points
    max_points: float = 0.0

    @property
    def is_empty(self) -> bool:
        return "empty" in self.tags


def load_cases(root: Path = DEFAULT_DATASET) -> list[EvalCase]:
    """Load every case, validated against the error registry and rubric config."""
    cases = []
    for case_json in sorted((root / "cases").glob("*/case.json")):
        meta = json.loads(case_json.read_text(encoding="utf-8"))
        files = {
            name: (case_json.parent / name).read_text(encoding="utf-8") for name in meta["files"]
        }
        cases.append(_build(meta, files))
    validate(cases)
    return cases


def _build(meta: dict, files: dict) -> EvalCase:
    expected = meta["expected"]
    case = EvalCase(
        case_id=meta["case_id"],
        language=meta["language"],
        role=meta["role"],
        course_id=meta["course_id"],
        assignment_id=meta["assignment_id"],
        rubric_id=meta["rubric_id"],
        instructions=meta["instructions"],
        files=files,
        compiles=expected["compiles"],
        error_ids=frozenset(expected["error_ids"]),
        acceptable_error_ids=frozenset(expected.get("acceptable_error_ids", [])),
        tags=tuple(meta.get("tags", [])),
        twin_of=meta.get("twin_of"),
        labels_reviewed_by=meta.get("labels_reviewed_by"),
        max_occurrences=dict(expected.get("max_occurrences") or {}),
    )
    low, high, max_points = expected_score_range(case)
    object.__setattr__(case, "expected_score_range", (low, high))
    object.__setattr__(case, "max_points", max_points)
    return case


@lru_cache(maxsize=None)
def error_definitions(course_id: str, assignment_id: str) -> tuple:
    from cqc_cpcc.error_definitions_config import get_error_definitions

    return tuple(get_error_definitions(course_id, assignment_id))


@lru_cache(maxsize=None)
def rubric(rubric_id: str):
    from cqc_cpcc.rubric_config import get_rubric_by_id

    return get_rubric_by_id(rubric_id)


EMPTY_MAX_SCORE_FRACTION = 0.2


def score_for_errors(case: EvalCase, error_ids, occurrences: Optional[dict] = None) -> float:
    """Points the production backend scoring gives a submission with exactly these errors."""
    from cqc_cpcc.rubric_grading import apply_backend_scoring
    from cqc_cpcc.rubric_models import CriterionResult, DetectedError, RubricAssessmentResult

    defs = {d.error_id: d for d in error_definitions(case.course_id, case.assignment_id)}
    rb = rubric(case.rubric_id)
    detected = [
        DetectedError(code=e, name=defs[e].name, severity=defs[e].severity_category,
                      description=defs[e].description, occurrences=(occurrences or {}).get(e, 1))
        for e in sorted(error_ids)
    ]
    criteria = [
        CriterionResult(criterion_id=c.criterion_id, criterion_name=c.name,
                        points_possible=c.max_points, points_earned=0, feedback="-")
        for c in rb.criteria if c.enabled
    ]
    result = RubricAssessmentResult(
        rubric_id=rb.rubric_id, rubric_version=rb.rubric_version,
        total_points_possible=rb.total_points_possible, total_points_earned=0,
        criteria_results=criteria, overall_feedback="-", detected_errors=detected,
    )
    return float(apply_backend_scoring(rb, result).total_points_earned)


def expected_score_range(case: EvalCase) -> tuple[float, float, float]:
    """(lowest, highest, max) points a correct grade can earn.

    Highest: each required error reported once. Lowest: each required error at its
    ``max_occurrences`` plus every acceptable error. Empty submissions have no error
    label; any score up to 20% of max points is accepted.
    """
    max_points = float(rubric(case.rubric_id).total_points_possible)
    if case.is_empty:
        return 0.0, max_points * EMPTY_MAX_SCORE_FRACTION, max_points
    high = score_for_errors(case, case.error_ids)
    low = score_for_errors(case, case.error_ids | case.acceptable_error_ids, case.max_occurrences)
    return low, high, max_points


def validate(cases: list[EvalCase]) -> None:
    """Raise ValueError when a case references unknown ids, rubrics or twins."""
    ids = {c.case_id for c in cases}
    problems = []
    if len(ids) != len(cases):
        problems.append("duplicate case ids")
    for c in cases:
        known = {d.error_id for d in error_definitions(c.course_id, c.assignment_id)}
        if not known:
            problems.append(f"{c.case_id}: no error definitions for {c.course_id}/{c.assignment_id}")
        unknown = (c.error_ids | c.acceptable_error_ids) - known
        if unknown:
            problems.append(f"{c.case_id}: unknown error ids {sorted(unknown)}")
        if c.error_ids & c.acceptable_error_ids:
            problems.append(f"{c.case_id}: id both required and acceptable")
        if c.twin_of and c.twin_of not in ids:
            problems.append(f"{c.case_id}: twin_of {c.twin_of} does not exist")
        if c.role != "grading":
            problems.append(f"{c.case_id}: unsupported role {c.role}")
    if problems:
        raise ValueError("Invalid eval dataset:\n" + "\n".join(problems))
