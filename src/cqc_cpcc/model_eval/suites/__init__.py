#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Prompt evaluation suites, one per registered prompt (see ``base.py``)."""

from __future__ import annotations

import importlib

#: suite id -> module defining ``SUITE``. Ids match ``suite`` in prompt_registry.json.
SUITE_MODULES = {
    "grading": "cqc_cpcc.model_eval.suites.grading",
}


def get_suite(suite_id: str):
    if suite_id not in SUITE_MODULES:
        raise KeyError(f"Unknown suite {suite_id!r}; known: {', '.join(sorted(SUITE_MODULES))}")
    return importlib.import_module(SUITE_MODULES[suite_id]).SUITE


def all_suites() -> list:
    return [get_suite(s) for s in SUITE_MODULES]
