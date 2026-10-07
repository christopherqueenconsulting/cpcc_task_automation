# Model evaluation dataset

Synthetic, labelled grading cases used by `python -m cqc_cpcc.model_eval` to decide
whether a new model may become the default in `src/cqc_cpcc/config/model_registry.json`.

**No real student work belongs here.** Every case is generated from clean template
programs by `src/cqc_cpcc/model_eval/dataset_builder.py`, author names are the synthetic
placeholders allowed by `scripts/pii_guard.py`, and the PII guard runs over this folder
in CI. Do not hand-edit cases: change the builder and run
`python -m cqc_cpcc.model_eval build-dataset`. A unit test fails if the committed cases
differ from what the builder produces.

## What v2 covers (current)

Rubric grading with error definitions, the main production path:

| Assignment | Rubric | Error ids |
|---|---|---|
| CSC 151 Exam 1 (Java, `OrderTotal.java`) | `csc151_java_exam_rubric` | `CSC_151_EXAM_1_*` |
| CSC 134 Project (C++, `payroll.cpp`) | `csc134_cpp_exam_rubric` | `CSC_134_PROJECT_1_*` |

90 cases:
- **Error detection (as in v1):**
  - clean programs and one seeded error per mutation;
  - multi-error combinations;
  - decoys (correct but differently written code that must stay clean);
  - programs that do not compile (checked against real `javac`/`g++`);
  - unicode;
  - 28 prompt-injection twins whose grade must not move.
- **Validity (new in v2):** empty, whitespace-only and skeleton programs, and no source file in the course language (a Java file for C++, a C++ file or Markdown prose for Java). The submission-validity gate must score them 0 without a model call.
- **Incomplete programs (new in v2):** functionality removed, alone or on top of an error. Each one has a `not_above` partner, the more complete program, and must never score above it. Ties are allowed.

Every case gets the assignment's fixed **requirement checklist** (`requirements` in `case.json`), so the eval also measures how the model marks requirement coverage.

Not yet covered: level-band rubrics (CSC 113 reflections), Give Feedback, Flowgorithm and the preprocessing digest for very large submissions.

v1 (78 cases, no validity, checklist or ordering labels) is frozen in `evals/datasets/v1/` for the October 2026 calibration report.

## Labels

Each `case.json` lists:

- `expected.error_ids`: a careful grader must report these.
- `acceptable_error_ids`: fair extra reports, not counted as false positives.
- `expected.validity`: the statuses the validity gate may return.
- `expected.requirements`: the statuses a correct grader may give each checklist item. Unlisted items must be `met`.
- `expected.error_satisfied_by`: an omission error that the matching requirement, marked missing or partial, may stand in for. The prompt asks for one of the two, not both.
- `expected.not_above`: the case this one must never outscore.

The hard gates `min_validity_accuracy`, `max_ordering_violations` and `min_requirement_agreement` in `model_policy.json` use these labels. Expected
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
