# UX goals: CPCC Task Automation (Phase 3a)

_Review record: operator persona E, 4 rounds (fail, fail, fail, fail-with-one-fix). The round-4 fix (interrupted grading must not lock the page) was applied after the review cap and was not re-reviewed._

For: Christopher. Scope: the local Streamlit app (Streamlit 1.65.0), worktree `cpcc-phase2`, branch
`feat/nav-legacy-toggle`. Nothing in the repo was changed to write this. Every Streamlit command named
below was checked with `poetry run streamlit docs st.<command>` against the installed 1.65.0.

**The one-line goal:** each weekly job should feel like a short, guided task with the next step always
visible, not a 10-section scroll. Problems should show first, and nothing that costs money should start
before you have seen the count and the cost. The one exception is the one-time checklist extraction when
instructions are loaded (D-2); its cost is shown.

---

## Decisions (recorded 2026-10-06)

| # | Topic | Decision |
|---|---|---|
| D-1 | Fonts | Self-host the open look-alikes Libre Franklin (body) and Libre Baskerville (headings) through `[[theme.fontFaces]]`, and remove `get_cpcc_css()`. Christopher decided. |
| D-2 | Requirement checklist | Automatic. When a new instructions source is **loaded** (BrightSpace fetch or file upload), extract the checklist once, and show its approximate cost. Editing the instructions text never re-extracts. Your checklist edits survive edits to the instructions text. A "Re-extract" button remains. Christopher decided. |
| D-3 | Grade assignment shape | One page with three stages: Setup / Submissions & run / Results & review. Christopher decided. |
| D-4 | Remembered choices | Course, rubric, assignment (when the mode uses one), grading mode and attendance courses are saved across restarts in `~/.cqc_cpcc/app_settings.json`. Christopher decided. |
| D-5 | Navigation | A **flat** top nav with no section menus, so every page stays one click away. The 7 items, plus the legacy page when it is enabled, fit at 1280px. The designer chose this default and it can be changed. |

---

## 1. Research summary

1. **Top navigation.** `st.navigation(..., position="top")` exists in 1.65. If sections are given, it shows menus; a flat list shows every page in the bar (https://docs.streamlit.io/develop/api-reference/navigation/st.navigation, demo https://doc-navigation-top.streamlit.app/). **For us:** the flat top bar (D-5) gives the wide grading tables the sidebar's width.
2. **Side drawers.** `st.dialog(width="large", position="right", on_dismiss=...)` opens a dialog from the side (https://doc-modal-dialog-drawer.streamlit.app/). **For us:** one student's full result opens in a drawer instead of a stack of nested expanders.
3. **Native badges.** `st.badge(label, icon=, color=)` and inline `:green-badge[...]` (https://docs.streamlit.io/develop/api-reference/text/st.badge). **For us:** one status vocabulary, with no emoji or HTML.
4. **Lazy containers.** Since 1.55, `st.expander`, `st.tabs` and `st.popover` accept `on_change="rerun"`, and `.open` reports whether the container is open, so closed content can skip its work. In 1.65 only **`st.expander` and `st.tabs`** also take `bind="query-params"`; popovers do not (https://docs.streamlit.io/develop/api-reference/layout/st.expander, https://docs.streamlit.io/develop/quick-reference/release-notes/2026). **For us:** use this for read-only, expensive displays only (see rule 2.2-W).
5. **URL binding and persisted state.** `st.segmented_control`, `st.pills` and `st.selectbox` take `bind="query-params"` and `persist_state="page"|"session"` (local docs). `st.selectbox(accept_new_options=True)` lets you type a new value. **For us:** the stage bar can bind to the URL, and the course picker on Give feedback can accept a course name that is not in the list.
6. **Step timeline.** In 1.65, `st.status(..., type="step")` and `st.expander(type="step")` draw a connected timeline (https://doc-status-step.streamlit.app/). **For us:** grading draws Check files, Grade and Build feedback as a timeline while it runs.
7. **Horizontal layout.** `st.container(horizontal=True, gap=, horizontal_alignment=)` and `st.space()` (https://docs.streamlit.io/develop/api-reference/layout/st.container). **For us:** action bars wrap at 1280px without `st.columns([2,2,3])`.
8. **Theme fonts.** `[[theme.fontFaces]]` plus `server.enableStaticServing = true` self-hosts fonts from `static/` at `url = "app/static/..."`. A server restart is needed after a change (https://docs.streamlit.io/develop/concepts/configuration/theming-customize-fonts). **For us:** this replaces the CSS injection and the third-party font host (D-1).
9. **No native stepper.** The feature request is still open (https://github.com/streamlit/streamlit/issues/10748). The blog builds wizards from session state and buttons (https://blog.streamlit.io/streamlit-wizard-form-with-custom-animated-spinner/). **For us:** build the stage bar from a segmented control that moves forward automatically when Setup is complete. Do not add a third-party stepper.
10. **Progressive disclosure.** Show the few options people use most, keep advanced options one clear click away, and avoid deep nesting (https://www.nngroup.com/articles/progressive-disclosure/). State the requirements before the user starts (https://www.nngroup.com/articles/4-principles-reduce-cognitive-load/). **For us:** grading mode, course and rubric stay visible because some courses change them every batch. Solution file, model, rubric overrides and the error-definition editor go behind "Advanced".
11. **Review exceptions first.** Send only flagged items to a person, say why each was flagged, and make reject as cheap as approve (https://reloadux.com/blog/human-in-the-loop-ai-design-3-ux-patterns/, https://www.blackline.com/blog/human-in-the-loop-at-scale/). **For us:** keep "Needs review" first, with a reason badge on each item and equal-weight buttons.
12. **Bulk actions.** Name the exact count before acting and report how many succeeded and how many failed (https://www.marigold-ui.io/patterns/user-input/bulk-actions, https://www.saasui.design/blog/saas-bulk-actions-ux-patterns). **For us:** "Grade 27 submissions" and "Write 24 grades as drafts" buttons, and a posted/skipped/failed report.
13. **Row selection.** `st.dataframe(on_select="rerun", selection_mode="single-row")` returns the selected row (https://docs.streamlit.io/develop/tutorials/elements/dataframe-row-selections). **For us:** clicking a summary row opens that student's drawer.

---

## 2. Shared design system

### 2.1 Theme tokens (`.streamlit/config.toml`)
```toml
[server]
enableStaticServing = true            # serves src/cqc_streamlit_app/static/ (next to Home.py)

[theme]
base = "light"
primaryColor = "#007396"              # CPCC blue
backgroundColor = "#FFFFFF"
secondaryBackgroundColor = "#F5F2E9"  # light CPCC-gold tint (today full #B6A269 fills every input)
textColor = "#1F2A30"
linkColor = "#007396"
borderColor = "#D8DCDF"
showWidgetBorder = true
baseRadius = "0.5rem"
yellowColor = "#B6A269"               # CPCC gold: accents, "Draft" badge
grayColor = "#636466"                 # CPCC gray: neutral badges
font = "libre-franklin, sans-serif"
headingFont = "libre-baskerville, serif"
codeFont = "monospace"

[[theme.fontFaces]]
family = "libre-franklin"
url = "app/static/fonts/LibreFranklin-VariableFont_wght.woff2"
weight = "100 900"
[[theme.fontFaces]]
family = "libre-baskerville"
url = "app/static/fonts/LibreBaskerville-Regular.woff2"
weight = 400
[[theme.fontFaces]]
family = "libre-baskerville"
url = "app/static/fonts/LibreBaskerville-Bold.woff2"
weight = 700
```
- Delete `get_cpcc_css()` and every `st.markdown(get_cpcc_css(), unsafe_allow_html=True)` call.
- Today's `font = "sans serif"` is not a documented value; the documented one is `"sans-serif"`.
- The font files must exist before the config points at them. `static/` does not exist yet.

### 2.2 Layout rules
- Call `st.set_page_config(layout="wide", page_icon=":material/...:")` once, in `Home.py`. Today 7 page scripts call it again, with emoji icons.
- Use `st.navigation(flat_page_list, position="top")` (D-5).
- Each page starts with `st.title(<nav title>, icon=...)` and one `st.caption`. The title matches the nav label: "Grade assignment", not "Exam Grading".
- Nest disclosure **at most 2 levels** in normal use. Debug panels may go up to 4 levels, and only when `CQC_OPENAI_DEBUG` is set. Put details in a right-side `st.dialog`.
- Use `st.container(horizontal=True)` for action bars and `st.columns` only for fixed grids. No `---` or `st.divider()` between sections.
- **2.2-W Widget state (required).** Streamlit drops a widget's value when the widget is not drawn on a rerun, for example after a stage switch or when Advanced is closed lazily. So:
  - every Setup, Advanced and stage input is mirrored into a non-widget `st.session_state` key, or uses `persist_state` where the widget supports it;
  - widgets are re-seeded from that key when drawn again;
  - the run key, effective rubric, checklist and upload paths are computed from the **persisted** values, never from whether a widget happens to be drawn;
  - lazy rendering (`on_change="rerun"` + `.open`) is for **read-only or expensive displays only**, never for inputs. Advanced inputs are always drawn: either in a normal expander, which runs whether open or closed, or with mirrored state.
- **Polling.** The thread-backed **BrightSpace fetch** and **BrightSpace write-back** jobs move from `time.sleep(1.5)` + `st.rerun()`
  (`utils.py:1731-1732`, `2002-2003`) to an `st.fragment(run_every=...)`, as attendance already does.
  **Grading keeps running in-script** (`await process_rubric_grading_batch`, `grade_assignment.py:2489-2527`):
  - the step timeline is drawn as it runs;
  - the stage bar and Setup inputs are disabled while it runs. The Grade button sets `grading_status_by_key[run_key] = "running"` in its `on_click` callback (with `run_key` passed in `args`). Callbacks run before the script, so the stage bar reads the status before it is drawn.
  - **A run never leaves the page locked.** Streamlit stops a script with `StopException`/`RerunException` (BaseException, not Exception) on Stop, a reconnect or a click during the run, so `except Exception` never sees it. The run's `finally` sets the status to `"interrupted"` whenever it is still `"running"`, and the `"running"` flag also carries a per-run token: a `"running"` status left by an earlier script run is treated as stale. An interrupted run shows "Grading was interrupted — grade again" and re-enables the stage bar and Setup.
  - Grading starts only from the Grade button's `on_click` status change for the current run key, never from a rerun, so an unrelated rerun cannot start a second paid batch.
  - When grading finishes normally, the same `pending_stage` + `st.rerun()` hand-off moves to "Results & review" and clears the disabled state. A Results stage opened from a URL or reload with no run this session says "No grading run this session" and links back to Setup.

  Moving grading to a thread is out of scope; it would be separate work gated by H-7.

### 2.3 Icons and labels
- Use Material Symbols `:material/name:` for nav, buttons, headers and badges. No emoji in labels (today 🎯, 🔄, 📦, 🎓, 🔑, 📝, ✅, ❌, ⏳).
- Use sentence case: "Select course", "Convert to Markdown", "Usage analytics".
- Buttons are a verb plus what they act on: "Grade 27 submissions", "Re-extract requirements".
- No empty labels; use `label_visibility="collapsed"` instead. Images and tables get `alt=`.
- Keep internals (run key, raw prompt, debug info) in an "Advanced" or debug area, never in the main flow.

### 2.4 Status vocabulary
| Status | Badge | Meaning |
|---|---|---|
| Queued | `gray`, `:material/schedule:` | In the batch, not started |
| Grading | `blue`, `:material/progress_activity:` | In progress |
| Graded | `green`, `:material/check_circle:` | Finished, no flags |
| Needs review | `orange`, `:material/flag:` + reason | Held for your decision |
| Accepted | `green`, `:material/task_alt:` | You accepted a flagged score |
| Confirmed 0 | `gray`, `:material/block:` | You confirmed no gradeable work |
| Failed | `red`, `:material/error:` | The grading call errored; it can be retried |
| Draft in BrightSpace | `yellow`, `:material/edit_note:` | Written as a draft (assignment route) |
| Posted to BrightSpace | `violet`, `:material/cloud_done:` | Published (the quiz route posts immediately) |

### 2.5 Component choices
| Job | Element |
|---|---|
| Choose among 2–5 modes (grading mode, search scope) | `st.segmented_control` (not a horizontal radio, not the third-party `tab_bar`) |
| Course, rubric, assignment | separate `st.selectbox`es with `placeholder=` and `index=None`, remembered (D-4) |
| Grading stages | `st.segmented_control(required=True, bind="query-params")`, seeded only through `st.session_state` and never with `default=` (avoids the default plus Session State warning). Moves forward automatically; every stage stays clickable. |
| Optional settings | one "Advanced" expander per stage (inputs follow rule 2.2-W) |
| Edit criteria, error definitions or requirements | `st.data_editor` with `column_config` and `alt=` |
| Results summary | `st.dataframe(on_select="rerun", selection_mode="single-row")`, with a status column and a score `ProgressColumn` |
| One student's detail | `st.dialog(width="large", position="right")` |
| Grading progress | `st.status(type="step")`, drawn in-script |
| Totals | a row of `st.metric(border=True)` |
| Risky confirmation (real write-back, clear results) | `st.dialog` naming the count, the effect and the route |
| Short confirmation | `st.toast` |
| Hints | `st.caption`; `st.info` only for something you must act on |
| Cost caption | `model_registry.estimate_cost(...)`. With auto-route, show "cost varies by routed model". |

---

## 3. Pages

The baselines below come from reading the code. The Phase 3b Playwright script re-measures each one
before any change. **Action** means one click, one file set, or one type-and-enter. **Scroll** means
800px viewport heights travelled at 1280×800.

**Instrumentation for measures (used by H-4, H-6):**
- **Paid calls:** AppTest with `llm_gateway.structured` monkeypatched to a call counter.
- **Ledger reads:** a call counter on `AttendanceLedger` methods.
- **Reruns:** only when `CQC_TEST_MODE` is on, each page draws a session-state run counter as plain `st.caption(...)` inside `st.container(key="cqc-test-run-counter")`. Playwright finds it by the key's CSS class (`.st-key-cqc-test-run-counter`). It must not change while only a fragment ticks.
- **Actions and scrolls:** the Playwright test counts its own actions and `window.scrollY` steps.

### Home
- **Job:** see what is happening in this session and jump to a task.
- **Top tasks:** start grading, start attendance, find a student.
- **Pain points:**
  - The header has an emoji.
  - The page renders the package README, whose relative `../../docs/...` links do not resolve in the app.
  - It shows no state, and it injects CSS.
- **Proposed:** a title, then three bordered cards (Grade assignment, Take attendance, Find student), each with a button.
  The Grade card shows **this session's** last run and review count, if there is one. Results are session-only (`initi_pages.py:59-60, 74-75`), so after a restart it says "No run this session".
  The model update banner goes below the cards. "What this app does" goes in an expander.
- **Measures:** 1 action from Home to any page. The flat top nav keeps this, and so do the cards. No README text and no broken links.
- **Reference:** https://doc-navigation-top.streamlit.app/ and the current page.

### Grade assignment: Setup
- **Job:** tell the grader what is graded and how.
- **Moments:** first batch of term; weekly repeat with a new assignment; CSC 113 reflections, which are always "Rubric only".
- **Top tasks:** choose mode, course, rubric and (when used) the assignment; load the instructions; check the requirement checklist.
- **Pain points (`get_rubric_based_exam_grading`):**
  - Up to 10 linear sections. Once a rubric is chosen, everything renders at once.
  - The "-- Select X --" fake options reset the pickers below them.
  - The mode radio defaults to "Rubric + error definitions", but CSC 113 reflections need "Rubric only" every time (the radio and its default `rubric_and_errors` are in `select_grading_mode`, `grade_assignment.py:365-380`).
  - Requirement extraction needs its own click.
  - The "Advanced: edit performance levels" expander is an empty placeholder.
  - The title says "Exam Grading".
- **Proposed:**
  1. **Grading mode** at the top as a segmented control with all three segments: Rubric + error definitions, Rubric only, Error definitions only. When the **rubric changes**, the mode takes the rubric's default: a rubric with no enabled error-count criterion defaults to "Rubric only". A remembered explicit choice for the same rubric overrides that default (D-4).
  2. **Course** and **rubric** as separate remembered pickers. Rubrics and error-definition assignments are independent and many-to-many: CSC_113 has 9 rubrics and 0 assignments; CSC_151 and CSC_251 have 3 rubrics and 2 assignments; CSC_134 has 2 rubrics and 1 assignment; CSC_152 has 1 rubric and 0 assignments. So there is no combined picker and no automatic rubric choice.
  3. **Assignment / error-definition picker**, shown for "Rubric + error definitions" and "Error definitions only" (`use_error_definitions`, `grade_assignment.py:2167`). It keeps "+ Create new". "Rubric only" keeps the optional assignment-label text input. The class section goes next to it.
  4. **Instructions source** as a segmented control: BrightSpace URL / Upload file / URL or Google Drive (recommend keeping all three). Loading a source triggers checklist extraction once, with a cost caption (D-2). The text preview opens in a dialog.
  5. **Requirement checklist editor** with a "Re-extract" button.
  6. **"Advanced" expander:** solution file, rubric overrides, error-definition editor (save, reset, export), the error-only point settings (max points and deductions, used only in that mode), model. Advanced holds settings only; no mode is chosen there.
  7. **Auto-advance (no "Next" click).** "Complete" means instructions are loaded and the checklist is ready, or the mode needs no checklist.
     - **When it fires:** only when Setup goes from incomplete to complete, and once per loaded instructions source. Track this in a non-widget key, `st.session_state["auto_advanced_for"] = <instructions hash>`. It does not fire again for the same hash, so clicking back to Setup never bounces you forward (H-9's Setup → Run → Results → Setup leg).
     - **Mechanism:**
       - The completion check runs inside the Setup content, after the stage bar is drawn. So it must **not** write the stage widget's key; Streamlit raises "cannot be modified after the widget ... is instantiated".
       - Instead it sets the non-widget key `st.session_state["pending_stage"] = "Submissions & run"` and calls `st.rerun()`.
       - At the top of the next run, before `st.segmented_control` is drawn, the page copies `pending_stage` into the stage widget key and clears it.
       - An alternative is an `on_change` callback on the instructions loader that sets the stage key directly; callbacks run before widgets are drawn.
     - Every stage stays clickable.
- **Measures:**
  - Counting rule, the same as for the batch figure below: Setup runs from page open until instructions are loaded. Instructions are **not** remembered, because they change per assignment.
  - Today: 7 actions (course 2, rubric 2, assignment 2, instructions 1), plus 1 extract click.
  - First-ever use, reported only and not a target: 7 (course 2, rubric 2, assignment 2, instructions 1; checklist automatic, auto-advance).
  - **Target, new assignment with course, rubric and mode remembered: 3** (assignment 2, instructions 1).
  - **Target, same assignment: 1** (instructions 1).
  - With Advanced closed, no data editor is visible.
- **Reference:** the wizard screenshots in https://blog.streamlit.io/streamlit-wizard-form-with-custom-animated-spinner/ and the current page.

### Grade assignment: Submissions & run
- **Job:** load the batch, check it, start grading and watch it finish.
- **Top tasks:** fetch or upload submissions; check who is in the batch; press Grade.
- **Pain points:**
  - The BrightSpace fetch redraws the whole page every 1.5 s during MFA.
  - The ZIP picker ("Review / change selected files") lives in a section higher up the page.
  - The Grade button gives no count and no cost.
  - The run key and cache notices are shown in the main flow.
  - Buttons sit in `st.columns([2,2,3])`.
- **Proposed:**
  - **Source:** a segmented control (BrightSpace / Upload). The BrightSpace job and MFA number refresh inside a fragment.
  - **Batch preview table:** student, files and check result, with the keep/drop picker inline.
  - **Action bar:** "Grade 27 submissions" (primary), a cost caption from `estimate_cost` (or "cost varies" when auto-routing), and "Clear previous results" with a confirm dialog.
  - **While grading:** an in-script `st.status(type="step")` timeline with a line per student; the stage bar is disabled.
  - **When done:** go to Results.
- **Measures:**
  - **ZIP batch, from page open to Grade pressed: today N = 10 in a fresh session** (course 2, rubric 2, assignment 2, instructions 1, extract 1, submissions 1, Grade 1), or 11 with a class section.
  - All targets are measured on the `rubric_and_errors` path. With D-2 automatic extraction and D-3 auto-advance:
    - **First-ever use: 9** (course 2, rubric 2, assignment 2, instructions 1, submissions 1, Grade 1). This is reported only, not a target, because 9 is above 0.7N = 7.
    - **Target, new assignment with course, rubric and mode remembered: 5** (assignment 2, instructions 1, submissions 1, Grade 1). 5 ≤ 7, so it meets 0.7N.
    - **Target, same assignment: 3** (instructions 1, submissions 1, Grade 1). It meets 0.7N.
  - Scrolls from page open to Grade: measure the baseline; target ≤ 1 viewport.
  - The Grade label contains the count, and a cost caption is present.
  - During a BrightSpace fetch, the run counter is unchanged while the fragment ticks.
- **Reference:** https://doc-status-step.streamlit.app/ and the current page.

### Grade assignment: Results & review
- **Job:** settle the exceptions, check the scores, hand back feedback, write grades back.
- **Moments:** right after a run, or later **in the same session** (results are kept for the session only; see Q-1).
- **Top tasks:** clear "Needs review"; download the feedback ZIP; write to BrightSpace (dry run, then real).
- **Pain points:**
  - The order is Needs review, "Expand all", summary, downloads, metric, then one expander per student. Each student's expander nests up to 4 levels in debug mode.
  - `---` dividers.
  - The write-back section sits below every student.
- **Proposed:**
  - **Header metrics:** graded, needs review, failed, average.
  - **Needs review card:** a reason badge per item and equal buttons. Confirm 0 / Grade anyway / Accept / Grade again are kept.
  - **Summary table:** status and score columns. Clicking a row opens the student drawer, with tabs for Feedback, Criteria, Errors and Requirements; debug is a lazy tab shown only with `CQC_OPENAI_DEBUG`. **"Expand all" goes away**, replaced by the drawer.
  - **Hand back card:** "Download feedback (27 .docx)", "Download summary (.xlsx)" and the existing **.csv** download, all kept.
  - **BrightSpace write-back card.** Every existing control is kept:
    - URL;
    - buffer %;
    - include per-criterion feedback;
    - feedback delivery, attach .docx or inline (this decides whether assignment scores are written);
    - the needs-review hold warning;
    - the warning that the quiz route posts immediately;
    - dry run, then real write.

    A **confirm dialog replaces the existing confirm checkbox** (`utils.py:1977-2000`). It names the count and the route, for example "Write 24 grades as **drafts**" or "**Post** 24 quiz grades now". The write-back job polls in a fragment. The report shows counts for posted, skipped and failed.
- **Measures:**
  - From results to one student's full feedback: today a scroll plus 1 click; target 1 action and 0 scrolls.
  - From results to the dry run: target ≤ 1 viewport.
  - Nesting ≤ 2 levels without debug (AppTest count).
  - Each review item takes 1 action.
  - **Grading outputs keep the same extracted text** as the baseline (H-7).
- **Reference:** https://doc-modal-dialog-drawer.streamlit.app/, https://docs.streamlit.io/develop/tutorials/elements/dataframe-row-selections and the current page.

### Take attendance
- **Job:** record attendance from BrightSpace activity into MyColleges and the tracker, and flag students past EVA.
- **Top tasks:** start a run; choose courses and options; read the EVA flags and the finish summary.
- **Pain points:**
  - The live view is already a fragment, which is good.
  - The plan form stacks five checkboxes with long labels.
  - The tracker URL input repeats a value that is also in Settings.
  - Screenshots and the log show when idle.
  - The ledger expander opens SQLite on every rerun even when collapsed.
  - Emoji in status text; title-case subheaders.
- **Proposed:**
  - **Idle:** a summary of the last run, a "Start attendance" button, and the ledger in a lazy expander (read-only display, so lazy loading is allowed).
  - **Plan:** course pills remembered (D-4), and an "Options" group with defaults.
  - **Running:** timeline, then EVA alerts, then lazy tabs for screenshots and log.
- **Measures:**
  - Start to running: 2 actions (today 2; keep).
  - With the ledger collapsed, the `AttendanceLedger` call counter is 0.
  - EVA alerts are visible without scrolling.
- **Reference:** the current page.

### Find student
- **Job:** look up a student's ID, email or course.
- **Top tasks:** search by email, by name, by ID.
- **Pain points:**
  - A third-party `tab_bar` switches between three near-identical inputs.
  - The roster table is an **editable** `data_editor` with `num_rows="dynamic"`.
  - Search results are a read-only dataframe drawn inside the `on_change` callback, so they **vanish on the next rerun**.
  - A separate course filter box.
  - "Active Courses Only" is a checkbox that restarts the roster job.
- **Proposed:**
  - **One search box.** It keeps today's matching rules: exact email, exact ID, and a name match that needs at least two tokens.
  - **Results** stored in session state and drawn in the script body, so they persist, in a read-only `st.dataframe`.
  - **Filters:** course `st.pills`; "Active courses only" toggle; "Refresh roster" button.
  - **Roster:** read-only.
- **Measures:**
  - Search takes 1 action (today 2).
  - Results survive one unrelated rerun.
  - No `extra_streamlit_components` import, and no editable roster.
- **Reference:** the dataframe row-selection tutorial above and the current page.

### Settings
- **Job:** keys, login, signature, model pins, analytics, preferences.
- **Pain points:** one long column split by dividers; three different save buttons; repeated "*Required" captions; title-case headers.
- **Proposed:**
  - Tabs for Credentials, Models, Analytics and Preferences.
  - Credentials, Models and Analytics are each a `st.form` with one "Save" button.
  - The **"Show legacy pages" toggle stays instant-save, outside any form**.
  - Required fields are marked in their labels.
- **Measures:** 1 save per tab; each tab is 1 action away with 0 scrolls at 1280×800.
- **Reference:** the current page.

### Give feedback
- **Job:** AI feedback (no grade) on project submissions.
- **Pain points:**
  - The course is free text.
  - Feedback starts automatically once all inputs are present, with no button, count or cost, and it **re-runs the whole batch on any rerun**.
  - A "Chat GPT Prompt" header shows the raw prompt.
  - The title is "Feedback Assignment".
  - The feedback-types table is always open.
- **Proposed:**
  - **Course:** a remembered selectbox with `accept_new_options=True`.
  - **Feedback types:** moved into Advanced.
  - **Start:** the primary button "Give feedback on n submissions", with a cost caption. Results are stored per run, so they are never recomputed on a rerun.
  - **Results:** a table with a drawer, one ZIP download, and the prompt in the drawer's debug tab.
- **Measures:**
  - Actions from page open to start: today 4 (course, instructions, solution, submissions); target 4 + 1 explicit start (3 + 1 with the course remembered).
  - Paid-call counter: 0 before the click, and unchanged by an unrelated rerun.
- **Reference:** the current page.

### Flowgorithm assignments
- **Job:** feedback and a grade on .fprg submissions.
- **Pain points:**
  - Points is a text input.
  - One submission per upload.
  - The "Show instructions" checkbox renders HTML.
  - It uses the older `define_chatGPTModel`.
  - Once a submission is present, the AI is **called again on every rerun**.
- **Proposed:**
  - `st.number_input` for points, and the rubric editor in Advanced.
  - Multi-file upload with a "Grade n submissions" button.
  - Results stored per run, shown as a table with a drawer.
- **Measures:**
  - Page open to first result: today 2 actions (instructions, submission). Target ≤ 3, including an explicit run.
  - 5 files: 1 upload + 1 click.
  - Paid-call counter unchanged by an unrelated rerun.
- **Reference:** the current page.

---

## 4. Gauntlet loop for Phase 3b

**Phase 3b order:**
1. Design system (theme, fonts, CSS removal, flat top nav)
2. Grade assignment: Setup
3. Grade assignment: Submissions & run
4. Grade assignment: Results & review
5. Take attendance
6. Find student
7. Settings
8. Home
9. Give feedback
10. Flowgorithm

Each piece goes through the following.

**Hard checks.** All must pass before the critic runs.
- **H-1 Renders:** AppTest loads the page in `CQC_TEST_MODE=true` with no exception, and the stage or tab under test renders.
- **H-2 No new unsafe HTML or CSS:** the count of `unsafe_allow_html`, `st.html` and `<style>` in changed files does not rise, and any that remain carry a one-line justification. `get_cpcc_css` calls reach 0 after the design-system piece.
- **H-3 Label hygiene:** no emoji in labels, sentence case, no empty labels, `alt=` on images and tables, no `use_container_width`. Checked by a lint script over the changed files.
- **H-4 Nothing hidden computes:** with lazy displays closed, the instrumentation counters (paid calls, `AttendanceLedger`) stay at 0. No `time.sleep` plus full-`st.rerun` polling remains in the fetch or write-back jobs.
- **H-5 E2E covers the main task:** a Playwright test in `tests/e2e` runs the top task in `CQC_TEST_MODE` with **synthetic data only**.
- **H-6 Success measure met:** the same script asserts the section 3 target against the baseline it recorded before the change, using the named instrumentation.
- **H-7 Grading outputs unchanged:** for the synthetic batch, scores, feedback text, the .xlsx/.csv summary and the .docx feedback keep the **same extracted text** as the pre-change golden output. The run key does not change unless the inputs changed.
- **H-8 No horizontal scroll:** at 1280×800 and 1920×1080, `scrollWidth <= innerWidth` on every stage.
- **H-9 State survives navigation:** switch Setup → Run → Results → Setup, and collapse then expand Advanced. Then assert that these are unchanged:
  - run key;
  - effective rubric;
  - requirement checklist (including edits);
  - grading mode;
  - instructions;
  - upload paths.
  It also stops an AppTest mid-grade and asserts that, on the next run, the status is `"interrupted"` and the stage bar and Setup inputs are enabled.

  H-9 also restarts the app: a fresh AppTest with the same `~/.cqc_cpcc/app_settings.json` (a temporary copy in tests) must reload the remembered course, rubric, assignment and mode.

**Blind A/B critic** (after H-1 to H-9 pass):
1. Playwright screenshots of before and after, at the same states, at 1280 and 1920. A coin flip decides which is labelled "A". The critic is a fresh agent that sees no code and no conversation.
2. The critic gets only the screenshots, that page's section 3 entry, its named reference, and the Decisions table.
3. Output is JSON: `{"preferred":"A"|"B"|"tie","per_goal":[{"goal","A","B","note"}],"biggest_gap":{"goal","what","where"}}`. It must name exactly **one** gap, tied to one stated goal.
4. **Pass** when the new version is preferred and no goal scores worse. Otherwise fix the named gap and repeat. **Maximum 3 rounds**, then park the piece as **needs-human** with the verdicts and screenshots attached.

---

## 5. Open questions for Christopher

**Q-1. Keep grading results across app restarts?** Today results live only in the browser session and are lost when the app restarts. Keeping them would store student names, scores and feedback on this Mac's disk under `~/.cqc_cpcc/`.
- **(Recommended) No: session only.** No student data is written to disk beyond the feedback files you download. Cost to you: 0 minutes.
- Yes, kept locally, deleted after 14 days. Results can be reopened the next day, and the Home card can show waiting review items. Cost to you: about 2 minutes to approve a retention rule.
- Yes, kept until you delete them. Cost to you: you manage the files yourself.
