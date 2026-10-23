# Changelog

Versions follow [docs/RELEASING.md](docs/RELEASING.md). Entries below the first tag are
summarised from `git log` and merged pull request titles on `master`; nothing else is claimed.

## Unreleased

- Code version stamped on grading output: result card and JSON export, saved run meta, Settings
  page. Not part of the grading run key.
- `docs/RELEASING.md` and the `release.yml` tag workflow (tags `vX.Y.Z` and `vX.Y.Z-rcN` create
  GitHub releases).

## 0.1.2 and earlier

`pyproject.toml` has said `0.1.2` since 2024-04-19 (commit 7d175b3) and was never bumped. No
tags or GitHub releases exist before this changelog. The history on `master` starts
2024-02-21. Recent merged pull requests, newest first:

- Rubric grading with a reference solution as the authority: #414, eval suite #413.
- Disjoint rubric point bands: #416.
- Grading diagnostics and scoring: harshness report #399, each error type counted once #400,
  reconciliation notes and Reinstate #410, LLM Prompt tab #412, bounded LLM calls #398.
- Compile gate keeps host files out of diagnostics and feedback: #395.
- Prompt and model evaluation CI: judge calibration #367 and #368, A/B gate fixes #401 and
  #405, skip the paid A/B when requests are identical #407.
- Give Feedback v2: #392.
- Grade Assignment: separate BrightSpace fetches for instructions and submissions #381.
