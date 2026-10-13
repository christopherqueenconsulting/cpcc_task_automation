#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Prompt evaluation suites (see ``base.py``); a prompt may have several suites."""

from __future__ import annotations

import importlib

#: suite id -> module defining ``SUITE``. Ids match ``suites`` in prompt_registry.json.
SUITE_MODULES = {
    "grading": "cqc_cpcc.model_eval.suites.grading",
    "grading-levelband": "cqc_cpcc.model_eval.suites.levelband",
    "requirement-extraction": "cqc_cpcc.model_eval.suites.requirements",
    "exam-grading": "cqc_cpcc.model_eval.suites.exam",
    "digest": "cqc_cpcc.model_eval.suites.digest",
    "project-feedback": "cqc_cpcc.model_eval.suites.feedback",
    "flowgorithm-grade": "cqc_cpcc.model_eval.suites.flowgorithm",
}


def get_suite(suite_id: str):
    if suite_id not in SUITE_MODULES:
        raise KeyError(f"Unknown suite {suite_id!r}; known: {', '.join(sorted(SUITE_MODULES))}")
    return importlib.import_module(SUITE_MODULES[suite_id]).SUITE


def all_suites() -> list:
    return [get_suite(s) for s in SUITE_MODULES]
