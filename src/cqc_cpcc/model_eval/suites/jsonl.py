#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Load a suite dataset written by ``suite_datasets`` (``<dataset>/cases.jsonl``)."""

from __future__ import annotations

import json
from pathlib import Path

from cqc_cpcc.model_eval.suites.base import SuiteCase


def load_jsonl_cases(root: Path) -> list[SuiteCase]:
    path = root / "cases.jsonl"
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        cases.append(SuiteCase(case_id=row["case_id"], stratum=row["stratum"], inputs=row["inputs"],
                               labels=row["labels"], tags=tuple(row.get("tags") or ()),
                               twin_of=row.get("twin_of"), labels_reviewed_by=row.get("labels_reviewed_by")))
    return cases


def chars_to_tokens(*texts: str) -> int:
    return sum(len(t or "") for t in texts) // 4 + 600  # + the prompt's fixed text
