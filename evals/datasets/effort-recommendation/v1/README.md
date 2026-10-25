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
# <model> is the registry grading model (src/cqc_cpcc/config/model_registry.json): openai/gpt-6-luna.
# Its registry effort is high, and `run` starts that incumbent (<model>@high) by itself, so only
# the two lower efforts are named as candidates.

# 1. cost first (no model call)
poetry run python -m cqc_cpcc.model_eval run --dataset evals/datasets/effort-recommendation/v1 \
  --candidate openai/gpt-6-luna@low --candidate openai/gpt-6-luna@medium --repeats 3 --dry-run
# 2. the paid run: worst case. Step 1 prints the current cost (the author's local dry run printed $0.9713 on 2026-10-23;
#    that is not a recorded artifact). Step 2 spends it.
poetry run python -m cqc_cpcc.model_eval run --dataset evals/datasets/effort-recommendation/v1 \
  --candidate openai/gpt-6-luna@low --candidate openai/gpt-6-luna@medium --repeats 3 --out evals/runs/effort-truth
# 3. derive the ground truth (no model call); --write puts ground_truth.json in the dataset directory
poetry run python -m cqc_cpcc.model_eval effort-truth --raw evals/runs/effort-truth/raw_outputs.jsonl --write
```

Then, by hand:

1. Commit `evals/datasets/effort-recommendation/v1/ground_truth.json` (the report says `not informative` and
   writes no truth when fewer than 3 assignments separate; then stop here).
2. Add an `effort-recommendation` entry under `prompt_eval.suites` in `src/cqc_cpcc/config/model_policy.json`
   with `"calibrated": true`, and the prompt version under `prompt_eval.calibrated_versions`. This file is
   Christopher's: it is edited by hand only, and the bot guard rejects agent edits. Set the floor only
   when at least 10 non-injection answers were confident (count them in the suite run's
   `raw_outputs.jsonl`, field `payload.confidence`, source `judge`) and `confident_not_under` is 1.0.

If nobody runs this, the recommendation stays display-only (Apply button, never selected for you) and the
suite's graders that need ground truth return nothing. The ground truth belongs to one model, this dataset
version and its content, and the prompt version it was calibrated on: change any of them and measure again
(a stale report is ignored with a warning).

**Limits.** These are short single-class labs. They cannot show that a real multi-file project needs
`high`, so a low ground truth here says nothing about such work. A recommendation is advice for one
assignment; the instructor still chooses one effort for the whole run.

The report also lists: each counted signal's association with the ground truth (a signal that does
not predict is dropped from the prompt and the UI reasons), whether `medium` or `high` has the better
harshness and recall balance (so the registry default can be reviewed), and how many assignments were
decided by a single measure (how stable the rule is).

## Graders

`confident_not_under` is the key grader: among the judge's confident answers, the share that did not
pick a lower effort than the ground truth. Target 1.0. Over-recommending only costs money
(`not_over_recommended`). `safety_floor`, `constrained`, `reasons_ok` and `injection` check the
rules that live in code. Until `ground_truth.json` exists, the graders that need it return nothing.
