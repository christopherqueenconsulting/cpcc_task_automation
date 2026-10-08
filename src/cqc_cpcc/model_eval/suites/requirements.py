#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Suite ``requirement-extraction``: instructions -> functional requirement checklist.

Each gold requirement has keyword groups; a predicted item matches a gold item when every
group has a keyword in its text (``common.matches_keyword_groups``), one-to-one, greedily.
Style rules in the instructions must not become items (they count against precision).
"""

from __future__ import annotations

from typing import Optional

from cqc_cpcc.model_eval.suites import common
from cqc_cpcc.model_eval.suites.base import CodeGrader, Suite
from cqc_cpcc.model_eval.suites.jsonl import chars_to_tokens, load_jsonl_cases


async def invoke(case):
    from cqc_cpcc.requirement_coverage import extract_requirements

    return await extract_requirements(case.inputs["instructions"], use_cache=False)


def to_payload(output) -> dict:
    return {"items": [{"id": r.id, "text": r.text, "weight": r.weight} for r in output.requirements]}


def match(case, payload) -> tuple[int, int, int]:
    """(matched gold items, predicted items, gold items), one-to-one, maximum matching."""
    gold = case.labels["gold"]
    edges = [[g for g, item in enumerate(gold) if common.matches_keyword_groups(p["text"], item["keyword_groups"])]
             for p in payload["items"]]
    owner: dict[int, int] = {}

    def assign(i: int, seen: set) -> bool:
        for g in edges[i]:
            if g in seen:
                continue
            seen.add(g)
            if g not in owner or assign(owner[g], seen):
                owner[g] = i
                return True
        return False

    matched = sum(1 for i in range(len(edges)) if assign(i, set()))
    return matched, len(payload["items"]), len(gold)


def _recall(case, payload) -> float:
    matched, _, total = match(case, payload)
    return matched / total if total else 1.0


def _precision(case, payload) -> float:
    matched, predicted, _ = match(case, payload)
    return matched / predicted if predicted else 0.0


def _count(case, payload) -> Optional[float]:
    exact = case.labels.get("exact_count")
    if exact is None:
        return None
    return max(0.0, 1.0 - abs(len(payload["items"]) - exact) / exact)


def _ids(case, payload) -> float:
    ids = [i["id"] for i in payload["items"]]
    return 1.0 if ids == [f"R{n}" for n in range(1, len(ids) + 1)] else 0.0


SUITE = Suite(
    id="requirement-extraction", prompt_id="requirement-extraction", role="grading",
    dataset="requirement-extraction/v1", load_cases=load_jsonl_cases, invoke=invoke, to_payload=to_payload,
    graders=(
        CodeGrader("recall", _recall, "share of the gold functional requirements found"),
        CodeGrader("precision", _precision, "share of items that are gold requirements (style rules are not)"),
        CodeGrader("count", _count, "one item per numbered/bulleted step"),
        CodeGrader("ids", _ids, "ids are R1, R2, ... in order"),
    ),
    weights={"recall": 0.6, "precision": 0.3, "count": 0.1},
    estimate_prompt_tokens=lambda c: chars_to_tokens(c.inputs["instructions"]),
    judges=("faithfulness",),
    description="Requirement checklist extraction from assignment instructions.",
)
