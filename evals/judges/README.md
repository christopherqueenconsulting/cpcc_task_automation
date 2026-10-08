# Model graders (LLM judges)

Prompts for the judges that score what code cannot: `feedback-quality.md`,
`faithfulness.md` and `pairwise.md`. Each is a registered prompt (`judge-<id>` in
`src/cqc_cpcc/config/prompt_registry.json`), so editing one needs a version bump.
Code: `src/cqc_cpcc/model_eval/judges.py`.

**A judge only counts once it is calibrated.** Until then its scores are shown in
scorecards as *report-only* and never gate anything (hard gates use code graders only).

## Calibrating (one-time, about 1 hour)

1. Pin a non-OpenAI judge in `model_policy.json` → `prompt_eval.judge.model` (or let the
   first run pick one; the scorecard records which).
2. Label the gold sets by hand (40 synthetic outputs each, `calibration/<judge>.jsonl`):
   ```bash
   poetry run python -m cqc_cpcc.model_eval judge-label --judge feedback-quality --labeled-by "<your name>"
   poetry run python -m cqc_cpcc.model_eval judge-label --judge faithfulness --labeled-by "<your name>"
   ```
   Labels go to `calibration/<judge>.labels.json`. The `constructed_score` in the gold
   file is only the intended level; your label is what counts.
3. Measure agreement (needs `OPENROUTER_API_KEY`, about $0.20 per judge):
   ```bash
   poetry run python -m cqc_cpcc.model_eval judge-calibrate --judge feedback-quality
   ```
   This writes `calibration/<judge>.report.json`. A judge counts when that report's judge model
   and prompt fingerprint match the current ones, the lower 95% bound of the weighted kappa is
   at least `prompt_eval.judge.min_kappa_lower` (0.4), and within-one agreement is at least
   `min_within_one` (0.8). `pairwise` has no gold set and stays report-only.
4. Commit the labels and the report.

Changing the judge model or a judge prompt makes the report stale; recalibrate.
