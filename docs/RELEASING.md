# Releasing

How this project versions its code, when a change gets a prerelease (`-rc`) first, how to cut
a release, and how to roll back. Nothing is published to a package index. A release is a git
tag plus a GitHub release with generated notes.

## What is versioned

| Thing | Where the version lives | Effect of a bump |
|---|---|---|
| Code | `pyproject.toml` (`[tool.poetry] version`), read by `cqc_cpcc/version.py` | Stamped on grading output (below). Never part of the run key. |
| Prompts | `prompt_registry.json` (`docs/PROMPTS.md`) | Part of the run key: cached results regrade. |
| Rubrics | `rubric_version` in the rubric config | Part of the run key. |
| Models | `src/cqc_cpcc/config/model_registry.json` (`docs/MODEL_EVALUATION.md`) | Part of the run key. |

The code version answers "which code graded this student?". It is stamped, as optional fields
so older saved runs still load, on:

- each result (`RubricAssessmentResult.code_version`), so it appears on the instructor result
  card ("Graded with") and in its JSON export;
- the run meta saved with each run (`code_version`, next to the prompt tag and model);
- the Settings page footer.

It is not in the grading run key (`grading_run_key.py`). The key caches results; a code
version in it would regrade every cached result on every release. Prompt, rubric and model
versions are in the key because they change what the model is asked or who answers. A code
change that alters scores is covered by the rc rules below, not by the key. The field is
`SkipJsonSchema`, so it is not in the model's output schema and prompt fingerprints do not
change.

## SemVer for this project

Format: `X.Y.Z`, or `X.Y.Z-rcN` for a prerelease. The tag is `v` + the version.

- **Patch (`0.1.2` to `0.1.3`)**: a fix that cannot change a student's score or what is written
  to BrightSpace. UI-only fixes, copy, docs, logging, test-only changes, dependency bumps
  that touch no grading code.
- **Minor (`0.1.x` to `0.2.0`)**: anything that can change a score, a grading decision or an
  external write for the same input: a prompt or rubric version bump, a scoring or
  reconciliation change (compile gate, requirement coverage, scoring engine), a model
  registry change or promotion, BrightSpace fetch or write-back changes, a new feature.
- **Major (`1.0.0` onward)**: a change that older saved runs or exports cannot be read by, or
  that removes a grading mode or setting. While the version is `0.x`, treat such a change as
  a minor bump and say so in the release notes. Moving to `1.0.0` is Christopher's call.

When a change mixes levels, the highest wins.

## Which changes need an `-rc` first

An rc is a tagged build that is checked against live systems before the final tag. A change
needs one when a unit test cannot show it is safe:

- BrightSpace write-back or fetch (`brightspace_writeback.py`, `brightspace_fetch.py`);
- a model promotion or any `model_registry.json` / `model_policy.json` change;
- a rubric or scoring change (rubric config, `scoring/`, reconciliation, compile gate);
- anything that touches the execution sandbox (`utilities/compiler_gate.py`, the code runner).

Everything else (UI-only, docs, patch-level fixes) goes straight to a final release.

## The rc checklist

Copy this into the PR or the release notes of the rc and tick each line with its evidence.

- [ ] `eval_live` suite, when the change touches prompts, models or scoring. It spends real
      OpenRouter money: ask Christopher first, then
      `CQC_EVAL_LIVE=1 poetry run pytest -m eval_live`. Do not run it unasked.
- [ ] BrightSpace walkthrough, when fetch or write-back changed:
      `HEADLESS_BROWSER=false poetry run python scripts/brightspace_fetch_walkthrough.py --url "<assignment or quiz URL>"`.
      Christopher approves MFA.
- [ ] Dry-run write-back, when write-back changed: run the Grade Assignment write-back with
      `dry_run=True` (the default) and confirm it located every target and wrote nothing.
- [ ] Grade a synthetic case from `evals/datasets` through the Grade Assignment page and
      check the score, the result card's "Graded with" line and the saved run's `meta`. No real
      student work in any of this.
- [ ] `poetry run pytest -m unit --ignore=tests/e2e` and `python scripts/pii_guard.py` are green.

An rc that fails is fixed on master and re-tagged `-rc2`. Never move a tag that has a release.

## Cut a release

Every tag, final or rc, must be on `master`: `release.yml` rejects any other.

1. Open a PR that sets `version` in `pyproject.toml` (`0.2.0-rc1` for an rc, `0.2.0` for the
   final), moves the `CHANGELOG.md` "Unreleased" entries under a new `## 0.2.0 - YYYY-MM-DD`
   heading (leave an empty `## Unreleased` above it), and nothing else. CI must be green.
   Merge it and note the merge commit SHA.
2. Tag that exact SHA, check the version it carries, then push the tag:

   ```bash
   git fetch origin
   git show <merge-sha>:pyproject.toml | grep '^version'     # must read the tag's version
   git tag -a v0.2.0-rc1 <merge-sha> -m "v0.2.0-rc1"
   git push origin v0.2.0-rc1
   ```

3. `release.yml` runs on the pushed tag. It fails unless the tag is `vX.Y.Z` or `vX.Y.Z-rcN`,
   equals `v` + the `pyproject.toml` version, and points at a commit on `master`. It then
   creates the GitHub release with generated notes; `-rcN` is flagged as a prerelease. The
   run's failure email goes to whoever pushed the tag.
4. Check the release page, add anything the generated notes miss (a prompt, rubric or model
   change, a rollback warning), and for an rc run the checklist above.
5. For the final release repeat steps 1 to 3 with the final version.

Poetry accepts `0.2.0-rc1` and reports `0.2.0rc1` in installed metadata (PEP 440).
`get_version()` converts that back to the tag's form; a unit test covers it. I did not run
`poetry check` on an rc version with the CI-pinned Poetry 1.7.1: try it in the first rc PR.

Anyone with write access can push a `v*` tag, and the workflow runs from the tagged commit.
The checks inside `release.yml` guard against mistakes; they are not a boundary. The boundary
is a repository tag ruleset on `v*` that limits who can create tags (a repository setting, not
part of this change).

A superseded or bad release is replaced by a later one, not edited.

### The release workflow failed

- Transient error (the tag is right): re-run the failed run from the Actions tab.
- Wrong tag (version mismatch, wrong commit, not on master): if no GitHub release exists for it
  (`gh release view vX.Y.Z` says not found), delete the tag with
  `git push origin :refs/tags/vX.Y.Z` and `git tag -d vX.Y.Z`, then tag the right SHA. Only
  Christopher may delete a tag that has a release; in that case cut the next patch or rc number.

## Deployment

The app at https://cpcc-task-automation.streamlit.app/ is on Streamlit Community Cloud. The
repository has a push webhook to `https://share.streamlit.io/hook`, so a push to the app's
branch redeploys it. The branch is set in the Streamlit Cloud dashboard, which is not
visible from the repository: confirm it there. If it is `master`, every merge reaches the
instructor and an rc is only a label.

The recommended setup, pending Christopher's decision (see the PR that added this file), is to
deploy from a `release` branch that only moves when a release is cut:

```bash
git push origin vX.Y.Z:refs/heads/release      # deploy a release (use --force to roll back)
```

Until then, an rc tag is a label on a commit already on `master`, which is probably live. Run
the rc checklist locally from the tag before the final version PR merges:

```bash
git fetch --tags && git checkout v0.2.0-rc1 && ./run.sh
```

## Roll back

1. Find the last good tag: `git tag --sort=-v:refname` or the GitHub releases page.
2. Roll the code back. Today (Streamlit deploys from `master`, unconfirmed): open a revert PR
   for the bad change (do not rewrite `master`). In the same PR bump the patch version and add
   a CHANGELOG entry, so new grades are not stamped with the bad version; merge it and cut a
   release as above. If a `release` branch exists (pending decision): redeploy the last good tag
   with `git push --force origin <tag>:refs/heads/release`. Only Christopher force-pushes
   `release`.
3. Name the bad tag in its GitHub release notes:
   `gh release edit <bad-tag> --notes "Rolled back in <new-tag>: <reason>"`.
4. Know what stays behind. Saved runs on the instructor's computer (`~/.cqc_cpcc/grading_results`,
   or `CQC_GRADING_RESULTS_DIR`) keep the scores and the `code_version` stamp of the code that
   graded them. The run key does not include the code version, so after a code rollback the
   same inputs hit the same key and the app shows the old result instead of regrading. To
   regrade with the rolled-back code, use "Re-grade this student" on the result, or remove the
   run's `<run_key>.json` file from that folder. A prompt, rubric or model that was also rolled
   back changes the key, so those regrade by themselves. A cloud deploy has no persistent
   `~/.cqc_cpcc`; this applies to the instructor's own machine.
5. A model rollback does not need a code rollback: run the Model Rollback workflow
   (`model-rollback.yml`), or set `CQC_MODEL_GRADING=<previous model>` and restart.
6. Older code ignores the new optional fields in saved runs, so a rollback does not break
   loading them.
