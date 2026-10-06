# Model evaluation dataset

Synthetic, labelled grading cases used by `python -m cqc_cpcc.model_eval` to decide
whether a new model may become the default in `src/cqc_cpcc/config/model_registry.json`.

**No real student work belongs here.** Every case is generated from clean template
programs by `src/cqc_cpcc/model_eval/dataset_builder.py`, author names are the synthetic
placeholders allowed by `scripts/pii_guard.py`, and the PII guard runs over this folder
in CI. Do not hand-edit cases: change the builder and run
`python -m cqc_cpcc.model_eval build-dataset`. A unit test fails if the committed cases
differ from what the builder produces.

## What v1 covers

Rubric grading with error definitions, the main production path:

| Assignment | Rubric | Error ids |
|---|---|---|
| CSC 151 Exam 1 (Java, `OrderTotal.java`) | `csc151_java_exam_rubric` | `CSC_151_EXAM_1_*` |
| CSC 134 Project (C++, `payroll.cpp`) | `csc134_cpp_exam_rubric` | `CSC_134_PROJECT_1_*` |

78 cases: clean programs, one seeded error per mutation, multi-error combinations,
decoys (correct but differently written code that must stay clean), programs that do
not compile (checked against real `javac`/`g++`), unicode, empty and whitespace-only
submissions, and 28 prompt-injection twins whose grade must not move.

Not yet covered: level-band rubrics (CSC 113 reflections), Give Feedback, Flowgorithm and
the preprocessing digest for very large submissions.

## Labels

Each `case.json` lists `expected.error_ids` (a careful grader must report these) and
`acceptable_error_ids` (fair extra reports, not counted as false positives). Expected
scores are not stored: they are computed by the production backend scoring from these
labels, so labels and scores cannot drift apart.

Labels are drafts until reviewed: `labels_reviewed_by` is `null` until a human confirms
them. To record a review, run `python -m cqc_cpcc.model_eval build-dataset --reviewed-by <name>`.

## Running

```bash
# Cost estimate only
poetry run python -m cqc_cpcc.model_eval run --candidate openai/gpt-6.1-sol --dry-run
# Smoke run (6 cases, 1 repeat, ~$0.01 on Luna)
poetry run python -m cqc_cpcc.model_eval run --candidate openai/gpt-6-luna@low --limit 6 --repeats 1
```

Thresholds, margins and the budget live in `src/cqc_cpcc/config/model_policy.json` (`eval`).
Run output goes to `evals/runs/` (git-ignored); CI publishes scorecards under
`evals/reports/` only when a promotion PR is opened.
