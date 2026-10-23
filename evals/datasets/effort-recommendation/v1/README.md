# effort-recommendation dataset (v1)

Synthetic data for the `effort-recommendation` prompt suite: the judge that reads an assignment
and recommends the lowest reasoning effort expected to grade it reliably.

**No case derives from a real assignment or a real submission.** Every assignment and every
program is written in `src/cqc_cpcc/model_eval/effort_dataset.py` (three of the programs come
from the synthetic assignments in `dataset_builder.py`), and every author name is one of the
allowlisted synthetic names in `scripts/pii_guard.py`.

Regenerate with `poetry run python -m cqc_cpcc.model_eval build-suite-datasets`. Do not hand-edit
`cases.jsonl` or `cases/`; a unit test fails when they differ from what the builder writes.

## What is here

- `cases.jsonl`: 12 assignments (CSC 151 Java, CSC 134 C++, CSC 251 Java; four of each, from
  short single-decision tasks to multi-requirement tasks with a loop, a decision and several
  methods or functions) and 3 prompt-injection twins of the multi-requirement ones. The twin's
  instructions end with a note asking the recommender for low effort. Each row has the
  instructions, the clean reference solution and the requirement checklist.
- `cases/<id>/`: labeled grading cases of the grading-solution kind, four per assignment: the
  solution's own format, one equivalent rewrite, one formatting-only twin (`slight_format`) and
  one bug twin. They load with `model_eval.dataset.load_cases` and use each course's real error
  definitions and rubric.
- `ground_truth.json` (absent until it is measured): the right effort per assignment.

## Ground truth is measured, not guessed

The reference is the labels, not "whatever `high` does". For one assignment the right effort is the
lowest one that is no worse than `high`, beyond noise, on all three of:

1. level-band agreement: the share of graded calls whose total is inside the labeled score range;
2. missed bug twins: the share of the labeled errors the grader reported;
3. harshness on the equivalent cases: how far the total drops below the lowest correct score.

**Noise baseline.** `high` is run with `--repeats 3` and its disagreement with itself (the mean
absolute difference between its repeats, per measure) is the tolerance, floored at 0.02. The report
prints it next to each effort's numbers.

**Separation precondition.** If no assignment shows any lower effort worse than `high` beyond that
noise, the report says `not informative`, no ground truth is written, and the judge stays
display-only.

```bash
# 1. cost first (no model call)
poetry run python -m cqc_cpcc.model_eval run --dataset evals/datasets/effort-recommendation/v1 \
  --candidate <model>@low --candidate <model>@medium --repeats 3 --dry-run
# 2. the incumbent (the registry grading model at its own effort, high) runs beside the candidates
poetry run python -m cqc_cpcc.model_eval run --dataset evals/datasets/effort-recommendation/v1 \
  --candidate <model>@low --candidate <model>@medium --repeats 3 --out evals/runs/effort-truth
# 3. derive and write the ground truth, the noise baseline and the extras
poetry run python -m cqc_cpcc.model_eval effort-truth --raw evals/runs/effort-truth/raw_outputs.jsonl --write
```

The report also lists: each counted signal's association with the ground truth (a signal that does
not predict is dropped from the prompt and the UI reasons), whether `medium` or `high` has the better
harshness and recall balance (so the registry default can be reviewed), and how many assignments were
decided by a single measure (how stable the rule is).

## Graders

`confident_not_under` is the key grader: among the judge's confident answers, the share that did not
pick a lower effort than the ground truth. Target 1.0. Over-recommending only costs money
(`not_over_recommended`). `safety_floor`, `constrained`, `reasons_ok` and `injection` check the
rules that live in code. Until `ground_truth.json` exists, the graders that need it return nothing.
