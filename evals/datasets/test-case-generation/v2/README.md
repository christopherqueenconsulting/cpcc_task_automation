# test-case-generation dataset (v2)

v1 plus 4 rows (`dice__*`, `greeting__*`: a dice roll and a timestamp program, numbered and prose wording)
whose printed values legitimately differ between runs. The first 12 rows are v1 unchanged. All synthetic
(author Ada Example); regenerate with `poetry run python -m cqc_cpcc.model_eval build-suite-datasets`.
The README of v1 describes the labels and the four graders; the report-only grader `values_may_differ`
is described in `evals/README.md`.
