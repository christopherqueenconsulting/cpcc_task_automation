# Plan: static results report (Results_Report.html in the hand-back ZIP)

**Date**: 2026-11-11 · **Status**: Implemented (see the PR that adds `results_report.py`)

## Goal

After a grading run, keep one self-contained HTML file next to the feedback documents.
Opened later from disk, with the app closed, it shows each student's score, errors,
output comparison and code, and can send the code to OneCompiler to run it. It never
regrades and never calls a model.

## What exists today (investigated)

| Need | Where it lives now | Reusable as-is? |
|---|---|---|
| Per-student view (Feedback, Criteria, Errors, Requirements, Output, Code) | `display_rubric_assessment_result`, `grade_assignment.py:3645` | No: Streamlit widgets. The data it reads is reusable. |
| Student feedback text | `build_student_feedback` (`student_feedback_builder.py`) | Yes |
| Instructor card with scores, Markdown to HTML | `result_card_export.py` (`result_card_markdown`, `_markdown_to_html`) | Yes |
| Code files with language, truncation | `code_preview.collect_code_files` | Yes |
| OneCompiler runner | `code_preview.build_onecompiler_payload` + `build_runner_html` (iframe + `postMessage` to `https://onecompiler.com`) | Yes: plain HTML/JS, no Streamlit |
| Output check verdicts and diffs | `result.execution_report` (dict, saved in results JSON) + `execution_view.render_output_check` / `_diff_lines` | Data yes; renderer is Streamlit |
| Test case inputs and expected output | the run's `execution_plan` in `grading_inputs_by_key`, or the saved `execution_workspace` (`results_store.load_execution_workspace`) | Yes |
| Hand-back ZIP assembly | `_generate_feedback_docs_and_zip`, `grade_assignment.py:4563` (docs, summary xlsx/csv, BrightSpace submissions ZIP) | Add one file here |
| ZIP cache invalidation on regrade | `_drop_derived_outputs`, `grade_assignment.py:4184` | Already drops the ZIP; the report rides on it |

**Gap:** the saved results JSON (`results_store.save_run`) does not keep student code.
`source_files` are temp paths in `grading_inputs_by_key[run_key]["submissions"]`. After an
app restart they are gone, and the Code tab shows "no longer available". So the report must
be built while the run's inputs are still in the session, which is when the hand-back ZIP
is built today.

## Design

1. **New pure module** `src/cqc_cpcc/results_report.py`:
   `build_results_report_html(meta, results, code_by_student, cases) -> str`.
   No Streamlit import, so it is unit-testable and reusable from a script.
   - `meta`: course, assignment, date, model and effort, app version, points possible.
   - `results`: `[(student_id, RubricAssessmentResult)]`.
   - `code_by_student`: `{student_id: [CodeFile]}` from `collect_code_files`.
   - `cases`: `{case_id: {label, stdin, expected_output}}` from the plan or saved workspace.
2. **One file, no network except OneCompiler.** Inline CSS and vanilla JS. No CDN, so it
   opens offline and leaks nothing on open. Data goes in one
   `<script type="application/json" id="run-data">` block with every `</` escaped (the
   same rule `build_runner_html` uses). JS renders all text with `textContent`, never
   `innerHTML`, so student code and model text cannot inject markup.
   CSP meta tag: `default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline';
   frame-src https://onecompiler.com; img-src data:`.
3. **Layout** (mirrors the app): header with run metadata and totals; a sortable,
   filterable student table (Status, Score, Level, major/minor errors, Output verdict);
   clicking a row opens that student's panel with tabs:
   Feedback · Criteria · Errors (with suppressed and reinstated reconciliation notes) ·
   Requirements · Output (per case: verdict, input, expected, actual, unified diff
   computed in Python with `difflib` at build time) · Code (line numbers, file picker).
   Plus a Solution section with the instructor's solution files and cases, when present.
4. **Run in OneCompiler**: a button per student. Nothing loads until the click (same opt-in
   and same `RUNNER_NOTICE` wording as `code_viewer.py`). The click inserts the iframe and
   posts `build_onecompiler_payload(...)`. Payloads are precomputed in Python, so the JS
   stays small. An "Open in OnlineGDB" link as the fallback, as in the app.
5. **Left out on purpose**: the LLM prompt tab (it repeats the full submission), debug
   records, any edit or regrade control, reinstate buttons.
6. **Wiring** in `_generate_feedback_docs_and_zip`: build the report from session state and
   add it as `Results_Report.html` at the ZIP root with `add_file_to_zip`. Also add a
   "Results report (.html)" download button on the Hand back tab beside Summary (.xlsx)
   (decision 1A, 2026-11-11).
7. **Code goes into the report, not a link to the nested ZIP.** Christopher asked
   (2026-11-11) whether the report can point at the `Student_Submissions/*.zip` already in
   the hand-back ZIP instead of copying the code. It cannot do that reliably:
   - a page opened from disk cannot read sibling files (Chrome and Firefox block `fetch` of
     `file://`), and cannot open a file inside a ZIP at all;
   - double-clicking the HTML inside a ZIP extracts only that file to a temp folder;
   - that nested ZIP exists only when submissions came from BrightSpace; uploaded files are
     never bundled (`_get_brightspace_submissions_zip`).
   The viewer and the OneCompiler button need the code text in the page, so it is embedded.
   The cost is small: source text compresses well inside the hand-back ZIP. A Student
   column note names the matching folder in `Student_Submissions/` for the original files.
8. **Code survives a restart (decision 2B, 2026-11-11), using Christopher's idea of keeping
   the submissions ZIP rather than copying files into the JSON.** When a run is saved,
   `results_store` also keeps a copy of its submissions ZIP as
   `<run_key>.submissions.zip` beside `<run_key>.json` (same owner-only directory, same
   `persistence_enabled()` switch, so still off on Community Cloud; pruned and deleted with
   its run). BrightSpace runs copy the fetched ZIP; uploaded runs write one in the same
   folder-per-student layout. On restore, `extract_student_submissions_from_zip` (it already
   reads that layout) rebuilds `source_files`, so the Code tab, the runner and the report
   work again. The same reader also accepts the hand-back ZIP itself (it reads its
   `Student_Submissions/*.zip`), which gives a later "load an old hand-back ZIP" path for
   free if wanted.
9. **Size guard**: code is already capped by `MAX_PREVIEW_CHARS`, stdout by the execution
   report caps. Log the report size; warn on the Hand back tab above 15 MB.

## Steps (each fits one session)

| # | Step | Check that proves it |
|---|---|---|
| 1 | ✅ (2026-11-11, see Step 1 result) **Spike, the one real unknown**: does OneCompiler's embed accept `postMessage` from a page opened as `file://` (origin `null`)? Write `build_runner_html` output to a temp file, open it in Chrome (Selenium MCP or by hand), see the code appear. If it fails, fall back to a "Copy code + open OneCompiler" button. | Screenshot of the filled editor from a `file://` page |
| 2 | Write failing unit tests for `build_results_report_html` (synthetic names from `pii_guard.py` only): every student present; code containing `</script>` and `<img onerror>` is inert; no `http` URL other than onecompiler.com/onlinegdb.com; diff present for a mismatched case; no LLM prompt text. | `poetry run pytest tests/unit/test_results_report.py` red |
| 3 | Implement `results_report.py` (template + JSON + JS). | Step 2 tests green |
| 4 | Wire into the ZIP and the Hand back download button. AppTest: after grading, the ZIP lists `Results_Report.html`. Regrade one student, the rebuilt ZIP's report shows the new score. | `poetry run pytest -m unit` green |
| 5 | Manual check: open the ZIP's report from disk in Chrome and Safari, click through two students, run one in OneCompiler. | Screenshots |
| 6a | Failing tests for the kept submissions ZIP (tmp `CQC_GRADING_RESULTS_DIR`): saved beside the JSON with mode 0600; uploaded runs get a folder-per-student ZIP; pruning and `delete_run` remove it; persistence off writes nothing; restore rebuilds each student's files. | `poetry run pytest tests/unit/test_results_store.py` red |
| 6b | Implement in `results_store.py` + `restore_saved_results`; Code tab works after an app restart. | 6a green; manual restart check |
| 7 | Docs: `docs/PRIVACY_AND_TELEMETRY.md` (the report holds names, scores and code: keep it with the ZIP, never upload it; student code is now kept on disk with saved runs), `docs/ARCHITECTURE.md`, `docs/BRIGHTSPACE_AUTOMATION_HANDOFF.md` hand-back section. `python scripts/pii_guard.py` clean. | Guard passes; PR CI green |
| 8 | Gauntlet `infra-runbook` (D, E, H) plus G for personal data, then merge. | JSON verdicts on the PR |

## Step 1 result (2026-11-11): PASS

Headless Chrome (Selenium 4.27.1, local `google-chrome`) opened the
`build_runner_html` template from a `file://` page (`window.origin` = `null`), with a
synthetic `Main.java` that prints `SPIKE_MARKER_42`. The OneCompiler embed received the
`postMessage`, showed `Main.java` with the code, and clicking Run printed `SPIKE_MARKER_42`
in its console. The same run with the design's CSP meta tag (`default-src 'none'; …;
frame-src https://onecompiler.com`) also passed. No fallback button is needed. Not yet
checked: Safari and Firefox (step 5).

## Risks

- **OneCompiler from `file://`**: settled in step 1 for Chrome; Safari and Firefox are checked in step 5.
- **Privacy**: the report is as sensitive as the ZIP it sits in (names, scores, code). No new
  data leaves the computer unless the instructor clicks Run, which already sends code to
  OneCompiler from the app today.
- **XSS from student code**: handled by JSON escaping, `textContent` and the CSP; step 2 tests it.

## Decisions (Christopher, 2026-11-11)

1. **1A**: the report goes inside the hand-back ZIP and gets its own download button.
2. **2B**: code survives a restart, by keeping the run's submissions ZIP with the saved run
   (design 8). The report embeds the code because a page opened from disk cannot read the
   nested ZIP (design 7).
