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

1. Open a PR that sets `version` in `pyproject.toml` (`0.2.0rc1` for an rc, the PEP 440 form Poetry always accepts; `0.2.0` for the
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

The tag is `v0.2.0-rc1`; `pyproject.toml` holds `0.2.0rc1`. `check_release_tag.py` and
`get_version()` both treat the two as the same version (unit-tested), so the stamp on grading
output reads `0.2.0-rc1`, like the tag. Poetry itself was not run against an rc version while
writing this, which is why the PEP 440 form is the one to use.

Cutting a release: Christopher, or an agent he has told to, pushes the tag and watches the
Release run to green. An agent that pushes a tag reports the run URL to Christopher, because the
failure email goes only to the pusher.

Anyone with write access can push a `v*` tag, and the workflow runs from the tagged commit.
The checks inside `release.yml` guard against mistakes; they are not a boundary. The boundary
is a repository tag ruleset on `v*` that limits who can create tags (a repository setting, not
part of this change).

A superseded or bad release is replaced by a later one, not edited.

### The release workflow failed

- Transient error (the tag is right): re-run the failed run from the Actions tab.
- The re-run says the release already exists: run `gh release view vX.Y.Z`; edit it with
  `gh release edit`, and delete it only with Christopher's approval.
- Wrong tag (version mismatch, wrong commit, not on master): if no GitHub release exists for it
  (`gh release view vX.Y.Z` says not found), delete the tag with
  `git push origin :refs/tags/vX.Y.Z` and `git tag -d vX.Y.Z`, then tag the right SHA. Only
  Christopher may delete a tag that has a release; in that case cut the next patch or rc number.

## Deployment

The app is meant to build from the **`release` branch**, not from `master`. Christopher reported
in chat on 2026-10-23 that the Streamlit deployment was updated; confirm the branch in the
Streamlit dashboard before relying on it. The previous app URL was
https://cpcc-task-automation.streamlit.app/; the URL of the app that builds from `release` is not
recorded in this repository, so ask Christopher for it. The repository's push webhook to
`https://share.streamlit.io/hook` redeploys the app when the branch it builds moves. Once the app
builds from `release`, a merge to `master` does **not** reach the instructor; a release does, when
`release` is moved to it.

To put a tagged release in front of the instructor, after the Release run for the tag is green,
run the **Deploy release** workflow (`.github/workflows/deploy-release.yml`):

```bash
gh workflow run deploy-release.yml -f tag=vX.Y.Z -f dry_run=true   # optional: show the diff only
gh workflow run deploy-release.yml -f tag=vX.Y.Z
```

It runs only from `master`. It checks the tag (format, on `master`, matches `pyproject.toml`,
newest Release run for the tag green and built from the tag's commit), then
force-pushes `release` to the tag plus one commit, `deploy: requirements.txt for vX.Y.Z`. That
commit adds `requirements.txt` generated from the tag's `poetry.lock` by `export_requirements.sh`.
`requirements.txt` exists **only** on `release`: Community Cloud installs it (with uv) faster than
`pyproject.toml`, and `master` keeps `poetry.lock` as its single dependency file so Dependabot never
bumps a second copy out of step (PRs #524, #543, #555). Never commit `requirements.txt` to `master`;
CI fails if it is tracked. The push is a normal git push, so the repository webhook to
`share.streamlit.io` should fire and the app redeploy; this is not yet confirmed for a push made
by the workflow token, so check the app after the first deploy (use an rc tag). The run summary
records the previous and the new `release` commit.

If the Deploy run fails, `release` is unchanged (the push is the last step):

- *Tag check fails* (format, not on `master`, Release run not green or built from another
  commit): fix the cause, usually by re-running the Release workflow for the tag. There is no
  override input; when the check itself is wrong, use the manual fallback below.
- *Push rejected* (a ruleset or branch protection on `release`): allow the `github-actions` app to
  push to `release`, or use the manual fallback.
- *"the Release run built X, but vX.Y.Z is Y"*, or no Release run is found (run records
  expire): re-running Release does not help; use the manual fallback, or cut the next rc.
- *Export fails* (PyPI or uv down) or *"generated requirements.txt does not match poetry.lock"*:
  re-run the workflow later; the second one means the export produced a line the lock does not
  pin, so do not deploy by hand until it is understood.

To find the tag a `release` commit came from: `git log -1 --format=%s <sha>` prints
`deploy: requirements.txt for vX.Y.Z`. If the app does not rebuild after a green run, use
"Reboot app" in the Streamlit Community Cloud dashboard.

An rc is live from that run, so move `release` to an rc only when Christopher has said to; the rc
checklist above is then run against the live build. `master` carries whatever has merged and is not
what the instructor grades with.

To try a tag on a computer: `git fetch --tags && git checkout v0.2.0-rc1 && ./run.sh`.

## Roll back

1. Find the last good tag: `git tag --sort=-v:refname` or the GitHub releases page.
2. Roll the code back. Redeploy the last good tag at once with
   `gh workflow run deploy-release.yml -f tag=<tag>` (needs `gh auth status` with write access).
   Do not push the bare tag to `release`: a tag from after this change has no `requirements.txt`,
   and Community Cloud would fall back to `pyproject.toml`. Manual fallback, when Actions is down
   or the tag check is wrong (needs `uv`; it uses `master`'s export script, because a tag from
   before this change carries an older one):

   ```bash
   git fetch origin --tags && git checkout --detach origin/master
   cp export_requirements.sh /tmp/export_requirements.sh
   git checkout --detach '<tag>^{commit}'
   bash /tmp/export_requirements.sh requirements.txt
   git add -f requirements.txt
   git commit --allow-empty -m "deploy: requirements.txt for <tag>"
   git push --force origin HEAD:refs/heads/release
   ```

   Then fix forward on `master`: open a revert PR for the bad change (do not rewrite `master`),
   bump the version and add a CHANGELOG entry in the same PR so new grades are not stamped with
   the bad version, merge it and cut a release as above.
3. Mark the bad release and name the new one, keeping its generated notes:
   `gh release edit <bad-tag> --prerelease --notes "Rolled back in <new-tag>: <reason>

$(gh release view <bad-tag> --json body -q .body)"`.
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
