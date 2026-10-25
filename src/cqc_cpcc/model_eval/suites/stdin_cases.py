#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Suite ``test-case-generation``: instructions + solution -> stdin cases with boundary coverage.

Each synthetic case labels the selection and loop conditions the instructions name, with
keyword groups to recognise each one and the input values that sit on its boundary. All four
graders are deterministic; the suite never runs a program (the sandbox is not needed for
prompt evaluation).

- ``condition_recall``: share of the named conditions the model listed.
- ``boundary_hit``: share of the named conditions with a case that contains a boundary value
  on one of its input lines and claims a condition that matches that gold condition. It
  measures that the value was typed, not that the program reached the branch.
- ``map_complete``: every condition the model listed has a case that claims its boundary.
- ``case_limit``: one to six cases.
- ``values_may_differ`` (report-only, no weight): the model marks a case as printing varying values
  exactly when the assignment does (dataset v2 adds a dice roll and a timestamp program).
"""

from __future__ import annotations

from typing import Optional

from cqc_cpcc.model_eval.suites import common
from cqc_cpcc.model_eval.suites.base import CodeGrader, Suite
from cqc_cpcc.model_eval.suites.jsonl import chars_to_tokens, load_jsonl_cases


async def invoke(case):
    from cqc_cpcc.test_case_generation import suggest_test_cases

    return await suggest_test_cases(case.inputs["instructions"], case.inputs["solution"],
                                    case.inputs["language"], use_cache=False)


def to_payload(output) -> dict:
    return {
        "conditions": [{"id": c.id, "text": c.text, "kind": c.kind} for c in output.conditions],
        "cases": [{"id": c.id, "inputs": list(c.inputs), "boundary_of": list(c.boundary_of),
                   "values_may_differ": bool(c.values_may_differ), "varying_markers": list(c.varying_markers)}
                  for c in output.cases],
    }


def _same(a: str, b: str) -> bool:
    a, b = (a or "").strip(), (b or "").strip()
    if a == b:
        return True
    try:
        return float(a) == float(b)
    except ValueError:
        return False


def _condition_recall(case, payload) -> Optional[float]:
    gold = case.labels["conditions"]
    if not gold:
        return None
    hits = sum(1 for g in gold
               if any(common.matches_keyword_groups(c["text"], g["keyword_groups"]) for c in payload["conditions"]))
    return hits / len(gold)


def _boundary_hit(case, payload) -> Optional[float]:
    gold = case.labels["conditions"]
    if not gold:
        return None
    texts = {c["id"]: c["text"] for c in payload["conditions"]}
    hits = 0
    for g in gold:
        for c in payload["cases"]:
            # The case must claim a condition that is this gold condition, not just any condition.
            claims_it = any(common.matches_keyword_groups(texts.get(ref, ""), g["keyword_groups"])
                            for ref in c["boundary_of"])
            if claims_it and any(_same(line, v) for line in c["inputs"] for v in g["boundary_values"]):
                hits += 1
                break
    return hits / len(gold)


def _map_complete(case, payload) -> float:
    ids = [c["id"] for c in payload["conditions"]]
    if not ids:
        return 0.0
    covered = {ref for c in payload["cases"] for ref in c["boundary_of"]}
    return sum(1 for i in ids if i in covered) / len(ids)


def _case_limit(case, payload) -> float:
    return 1.0 if 1 <= len(payload["cases"]) <= 6 else 0.0


def _values_may_differ(case, payload) -> float:
    """Report-only: a case is marked values-may-differ exactly when the assignment prints varying values.

    Cases tagged ``varies`` must have at least one marked case; every other case must have none.
    Not in ``weights`` and not gated in model_policy.json (human-edited, untouched).
    """
    marked = any(c.get("values_may_differ") for c in payload["cases"])
    return 1.0 if marked == ("varies" in case.tags) else 0.0


SUITE = Suite(
    id="test-case-generation", prompt_id="test-case-generation", role="grading",
    dataset="test-case-generation/v2", load_cases=load_jsonl_cases, invoke=invoke, to_payload=to_payload,
    graders=(
        CodeGrader("condition_recall", _condition_recall, "share of the named conditions listed"),
        CodeGrader("boundary_hit", _boundary_hit,
                   "share of the named conditions with a case on a boundary value that claims it"),
        CodeGrader("map_complete", _map_complete, "every listed condition has a case that claims its boundary"),
        CodeGrader("case_limit", _case_limit, "one to six cases"),
        CodeGrader("values_may_differ", _values_may_differ,
                   "cases marked values-may-differ exactly when the assignment prints varying values (report-only)"),
    ),
    weights={"condition_recall": 0.3, "boundary_hit": 0.5, "map_complete": 0.1, "case_limit": 0.1},
    estimate_prompt_tokens=lambda c: chars_to_tokens(c.inputs["instructions"], c.inputs["solution"]),
    description="Stdin test cases with a boundary case for every named condition "
                "(boundary_hit measures that a value was typed, not that the program reached the branch).",
)
