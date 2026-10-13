#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Suite ``digest``: the preprocessing digest of large submissions.

The prompt is the same whatever the size (size only decides when it runs), so the cases
are small multi-file programs with seeded defects and a list of facts the digest must
keep. A digest that merely copies a large program fails the compression grader; on
programs under ``COMPRESSION_MIN_CHARS`` the digest's fixed structure is longer than the
code, so compression is not applicable there.
"""

from __future__ import annotations

import json
from typing import Optional

from cqc_cpcc.model_eval.suites import common
from cqc_cpcc.model_eval.suites.base import CodeGrader, Suite
from cqc_cpcc.model_eval.suites.jsonl import chars_to_tokens, load_jsonl_cases


async def invoke(case):
    from cqc_cpcc.utilities.AI.openai_client import generate_preprocessing_digest

    return await generate_preprocessing_digest(**case.inputs)


def to_payload(output) -> dict:
    return json.loads(output.model_dump_json())


def _issues_text(payload) -> list[str]:
    return [f"{i.get('issue', '')} {i.get('location', '')}" for f in payload.get("files", [])
            for i in f.get("detected_issues", [])]


def _coverage(case, payload) -> Optional[float]:
    defects = case.labels["defects"]
    if not defects:
        return None
    issues = _issues_text(payload)
    found = sum(1 for d in defects if any(common.matches_keyword_groups(t, d["keyword_groups"]) for t in issues))
    return found / len(defects)


def _facts(case, payload) -> float:
    text = json.dumps(payload).lower()
    facts = case.labels["facts"]
    return sum(1 for f in facts if f.lower() in text) / len(facts) if facts else 1.0


def _files(case, payload) -> float:
    names = {f.get("filename", "").split("/")[-1] for f in payload.get("files", [])}
    expected = set(case.labels["files"])
    return len(names & expected) / len(expected)


def _completeness(case, payload) -> float:
    missing = (payload.get("completeness_check") or {}).get("missing_components") or []
    name = case.labels["missing_component"]
    if name:
        return 1.0 if any(name.lower() in m.lower() for m in missing) else 0.0
    return 1.0 if not missing else 0.5  # nothing is missing: extra "missing" items are noise


#: Below this the digest's per-file structure alone outweighs the code (seen live: ~2k-char
#: programs give ~5k-char digests), so shortness says nothing about the prompt.
COMPRESSION_MIN_CHARS = 8000


def _compression(case, payload) -> Optional[float]:
    if case.labels["code_chars"] < COMPRESSION_MIN_CHARS:
        return None
    return 1.0 if len(json.dumps(payload)) <= 0.9 * case.labels["code_chars"] else 0.0


SUITE = Suite(
    id="digest", prompt_id="preprocessing-digest", role="digest", dataset="digest/v1",
    load_cases=load_jsonl_cases, invoke=invoke, to_payload=to_payload,
    graders=(
        CodeGrader("issue_coverage", _coverage, "seeded defects appear among the digest's detected issues"),
        CodeGrader("fact_retention", _facts, "class, method and constant names survive the digest"),
        CodeGrader("files", _files, "every file has a digest entry"),
        CodeGrader("completeness", _completeness, "a removed required method is reported missing"),
        CodeGrader("compression", _compression, "the digest is shorter than the code"),
    ),
    weights={"issue_coverage": 0.6, "fact_retention": 0.3, "completeness": 0.1},
    estimate_prompt_tokens=lambda c: chars_to_tokens(*(str(v) for v in c.inputs.values())),
    judges=("faithfulness",),
    description="Preprocessing digest of multi-file submissions with seeded defects.",
)
