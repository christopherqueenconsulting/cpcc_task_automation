#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Model evaluation harness.

Grades a synthetic, labelled dataset (``evals/datasets/``) with the production grading
functions on a candidate model and the incumbent, then decides whether the candidate
may replace the incumbent in ``config/model_registry.json``.

Run ``python -m cqc_cpcc.model_eval --help``.
"""
