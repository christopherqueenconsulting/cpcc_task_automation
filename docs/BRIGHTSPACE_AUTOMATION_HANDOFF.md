# BrightSpace Automation — Session Handoff

Copy the **Kickoff Prompt** below into a new session. The rest of the doc is
reference context for that session (and for you).

---

## ✅ Kickoff Prompt (copy–paste this)

> Use a live browser (Playwright MCP and/or a Selenium MCP — see
> `docs/BRIGHTSPACE_AUTOMATION_HANDOFF.md` for which and why) to **verify and fix
> the BrightSpace submission + instructions automation** in this repo. Read that
> handoff doc first.
>
> **Priority 1 — assignment instructions (known broken).** On a real assignment,
> click **"Edit Assignment"** (a `<button class="d2l-button" data-location="/d2l/le/activities/edit/...">`),
> then inspect the **live DOM crossing shadow roots AND the TinyMCE iframe** to find
> the element that holds the instructions text. The editor nests:
> `d2l-activity-text-editor[arialabel="Instructions"]` →
> `d2l-activity-html-new-editor` → `d2l-htmleditor` → `<iframe>` whose
> `contenteditable` `<body>` holds the text. Validate the **exact in-page JS** in
> `_READ_EDITOR_INSTRUCTIONS_JS`, then fix `_READ_EDITOR_INSTRUCTIONS_JS`,
> `_open_assignment_editor`, and `_find_edit_assignment_location` in
> `src/cqc_cpcc/utilities/brightspace_fetch.py` so `fetch_assignment_instructions`
> returns the text.
>
> **Priority 2 — verify the rest live:** quiz instructions (first question),
> quiz file-upload submissions, assignment "Download All" + last-attempt pruning,
> and the MFA number appearing on the Streamlit page during a real login.
>
> **Priority 3 — end-to-end web app:** fetch → preview/edit → "Use the generated
> file" → both instructions and submissions populate → grading runs.
>
> I will log in and approve MFA when prompted. Keep the existing unit tests green
> (`poetry run pytest tests/unit/test_brightspace_fetch.py tests/integration/test_brightspace_submissions.py`),
> and where practical add assertions derived from the real DOM. Use the existing
> tools: `scripts/brightspace_selector_probe.py` (live REPL that attaches to the
> project's real Selenium session) and `scripts/brightspace_fetch_walkthrough.py`.

**Have ready:** a real Assignment URL and a real Quiz URL (ideally with a student
who has multiple attempts and one assignment needing multiple files), and be
available to approve MFA.

---

## Which MCP / browser to use (and why)

The product reads all shadow-DOM / iframe content through Selenium
`driver.execute_script(<JS>)`. That JS runs **in the page context**, so it behaves
identically in any Chromium browser. That fact drives the recommendation:

| Option | Best for | Caveats |
|---|---|---|
| **Playwright MCP** (already connected) | Fast iteration on the extraction **JS**: paste `_READ_EDITOR_INSTRUCTIONS_JS` into `browser_evaluate` — it runs the same as Selenium's `execute_script`. | Its own browser → you log in again there; it won't share the code's session or run Selenium click flows. |
| **Selenium MCP on the project's Docker grid** | Driving the **same Chrome build + profile** the code uses, conversationally. | Setup below. Two WebDriver clients can't share one *live* session or the same `--user-data-dir` simultaneously — run one at a time; login persists via the mounted profile. |
| **`scripts/brightspace_selector_probe.py`** (already in repo) | Highest fidelity, **zero new setup** — it attaches to the project's real Selenium session and runs the product's actual JS (`editor`, `find`, `dump`, `editloc`, `deepcss`). | A REPL you run, not Claude-driven. |

**Recommended path:** iterate the extraction JS quickly in **Playwright MCP**
(`browser_evaluate`), then confirm the final result in the **real Selenium path**
with `brightspace_selector_probe.py`. Only set up a Selenium MCP if you want Claude
to operate the same Selenium browser end-to-end.

### How to set up a Selenium MCP against this project's Docker grid

1. Start the project's Selenium container (or let the app start it):
   ```bash
   docker compose -p cpcc_task_automation -f docker-compose.yml up -d selenium-chrome
   ```
   Grid endpoint: `http://localhost:14444/wd/hub` · VNC (watch it): `http://localhost:17900` (password `secret`).

2. Add a Selenium MCP server. Example using the community `mcp-selenium` (Node):
   ```json
   // .mcp.json (project root) or your Claude Code MCP config
   {
     "mcpServers": {
       "selenium": { "command": "npx", "args": ["-y", "@angiejones/mcp-selenium"] }
     }
   }
   ```

3. In session, have it **connect to the remote grid** rather than a local browser:
   start the browser with the remote URL `http://localhost:14444/wd/hub` and Chrome
   arg `--user-data-dir=/home/seluser/chrome-profile` (the volume-mounted profile,
   so login/MFA persists). If the MCP can't target a remote grid / custom
   `user-data-dir`, fall back to the probe REPL — it already does exactly this.

> Key point: because the code reads via `execute_script`, the **JS is the portable
> artifact**. Verify the JS anywhere Chromium runs; the final fix always lands in
> the Selenium code paths in `brightspace_fetch.py` and is confirmed via the probe
> REPL or the walkthrough.

---

## What was built this session

### 1. `.env` defaults for browser/docker selection — ✅ unit-tested
`BROWSER_TYPE` (`DOCKER_CHROME|LOCAL_CHROME|BROWSERLESS`) and `DOCKER_TYPE`
(`LOCAL|REMOTE`) skip the interactive console prompts (and prevent the
background-thread fetch from hanging on `input()`).
Files: `env_constants.py`, `selenium_util.py` (`_enum_from_env`, `which_browser`,
`which_docker`), `.env.example`.

### 2. One BrightSpace fetch → submissions ZIP **and** instructions — ✅ unit-tested / ❌ not live
`BrightSpaceFetchResult.instructions`; `build_submissions_zip_from_brightspace_url(..., fetch_instructions=True)`;
new top-level `add_brightspace_source_element` auto-fills Step 4 (Instructions) and
Step 8 (Submissions) on the Grade Assignment page (the nested `allow_brightspace`
uploader was removed).
Files: `brightspace_submissions.py`, `cqc_streamlit_app/utils.py`,
`pages/4_Grade_Assignment.py`, `brightspace_fetch.py`.

### 3. MFA number re-published to the web-app page every poll — ✅ unit-tested / ❌ not live
`_wait_for_mfa_approval` re-captures number + screenshot each poll (fixes
"number was still animating / never appeared"); `_render_mfa_prompt` shows a large
number + live screenshot.
Files: `cqc_cpcc/utilities/utils.py`, `cqc_streamlit_app/utils.py`.

### 4. Assignment instructions via "Edit Assignment" editor — ✅ FIXED & verified live (2026-06-29)
Reads the button's `data-location`, navigates to `/d2l/le/activities/edit/...`, then
reads the nested shadow-DOM TinyMCE editor.
**Root cause (not the JS):** `_READ_EDITOR_INSTRUCTIONS_JS` was always correct —
verified live, it returns the full 9,303-char instructions from the editor's
TinyMCE iframe `<body>` (`d2l-activity-text-editor[Instructions]` →
`d2l-activity-html-new-editor` → `d2l-htmleditor` → iframe `body.isContentEditable`,
class `mce-content-body d2l-html-block-rendered`). The bug was the *order* in
`fetch_assignment_instructions`: it ran view-mode `_collect_instructions_text`
**first**, but the submissions/marking page (`folder_submissions_users.d2l`) renders
*student submission* text inside `<d2l-html-block>` elements too — so it returned
~21k chars of the student's words and short-circuited before ever opening the editor.
**Fix:** the Edit Assignment editor is now the **primary/authoritative source**;
view-mode scraping is a fallback used only when no editor is present.
Functions: `_find_edit_assignment_location`, `_open_assignment_editor`,
`_read_editor_instructions`, `_READ_EDITOR_INSTRUCTIONS_JS` in `brightspace_fetch.py`.

### 5. Interactive selector-probe tool — ✅ created, smoke-tested / ❌ not used live
`scripts/brightspace_selector_probe.py`: live REPL (`editor`, `find`, `dump`,
`editloc`, `deepcss`, `xpath`, `css`, `js`, `save`, `accept`) crossing shadow roots
+ iframes; offline `--paste-file` mode that descends declarative shadow DOM.

### Tests
180+ passing across `test_brightspace_fetch.py`, `test_brightspace_submissions.py`,
`test_mfa_bridge.py`, `test_selenium_util.py`, `test_utils.py`. **All mock
Selenium — nothing exercises a real browser or the real BrightSpace DOM.**

---

## ❌ Unverified by the user (verify in the new session)

1. ~~**Assignment instructions extraction** — highest priority, known broken.~~
   ✅ **FIXED & verified live 2026-06-29** (editor-first ordering; see §4 above).
2. **Quiz instructions** = the (single) question prompt — ✅ **REWRITTEN & verified live
   2026-07-09.** These are exam quizzes: one written-response question whose prompt IS the
   instructions. `fetch_quiz_instructions` now opens the first learner **attempt** and reads
   the prompt from the Consistent Evaluation page — the prompt is a `<d2l-html-block>` tagged
   `d2l-questions-written-response-question-text` whose rich text lives in its **shadow root**
   (`d2l-html-block-rendered` div), so a light-DOM `.text` read returns empty. New
   `_READ_QUIZ_QUESTION_JS` scopes to that block and deep-scans its shadow DOM (returned the
   full 5,265-char Exam 2 prompt live). The old `.question-text` edit-page XPath is a
   secondary fallback. **The generic `_collect_instructions_text` fallback was REMOVED from
   the quiz route** — it grabbed course-home widgets / student text and mislabeled them as
   instructions; when the prompt can't be read we now return `None` (paste manually).
3. **Quiz submissions** — ✅ **REWRITTEN & verified live 2026-06-29.** `fetch_quiz_file_uploads`
   now drives the Consistent Evaluation UI (NavInfo onclick attempts; per-learner grouping;
   file URL from `d2l-list-item[key]`; written-response capture). See "Quiz route — live DOM"
   below.
4. **MFA prompt** rendering on the web-app page — ✅ **VERIFIED live 2026-06-29**
   (number rendered on the Grade Assignment page: "🔐 Two-factor approval needed → 40";
   approved → login completed).
5. **End-to-end web-app flow** — ✅ **VERIFIED live 2026-06-29** via Playwright. Fetch →
   MFA on page → approve → (login-race fix) re-navigate → Download All + last-attempt
   prune ("Pruned 1 older attempt for Student A") → preview "route: assignment"
   → "Use the generated file" → instructions textarea auto-filled with the exact
   9,303-char Project 1 instructions + submissions populated. Required fixing the
   login-completion race (see §6 below / the new `_await_brightspace_after_login` gate).
6. **Assignment "Download All"** popup + pruning — ✅ **selectors re-verified live
   2026-06-29** (select-all, download button via XPath + shadow-JS, table = 80 rows);
   full download not re-executed (popup-heavy); pruning is pure-Python unit-tested.

---

## Quiz route — live DOM findings (verified 2026-06-29, CSC151 "Programming Exam 1")

**Route detection:** the quiz *edit* URL (`/d2l/le/activities/edit/...?cft=quiz&...qi=<qi>`)
matches `detect_route` → `ROUTE_QUIZ` (via `qi=` + `quizzing`). NOTE the instructions
page and the submissions page are **different URLs** derived from `ou`/`qi`:
- Instructions/questions: the edit page (`activities/edit/...cft=quiz`).
- Submissions/grades: `/d2l/lms/quizzing/admin/mark/quiz_mark_users.d2l?ou=<ou>&qi=<qi>`.

**Quiz instructions (✅ working):** the quiz editor has only `header`/`footer`/`description`
rich-text fields (no "Instructions"), and the description was empty — so the first
**question** prompt is the instructions, exactly as specified. On the edit page each
question is `<div class="question-item">` → `<div class="question-text">` (clean prompt,
2,096 chars live) + `<div class="question-content">` (type, e.g. "Written Response").
`QUIZ_QUESTION_XPATH` updated to match `question-text`.

**Quiz submissions — ✅ RE-VERIFIED & TWO BUGS FIXED LIVE 2026-07-09 (`fetch_quiz_file_uploads`).**
End-to-end run on a real graded quiz (qi=3000002 ou=200004, CSC151 "Programming Exam 2",
8 learners) produced a correct ZIP of 6 `.java` files (2 learners genuinely submitted
nothing). Two real bugs — the source of the "stuck building the zip" report — were fixed:
- **The hang: `_open_and_login` retry storm.** It called `click_element_wait_retry`
  on `SUBMISSIONS_TAB_XPATH` unconditionally, but that tab exists only on the assignment
  (dropbox) page — the quiz grid (`quiz_mark_users.d2l`) has none. With `WAIT_DEFAULT_TIMEOUT=30`
  + `MAX_WAIT_RETRY=3`, the nested `get_/click_element_wait_retry` retries burned **~4 min**
  before giving up, all under one "Opening Submissions view..." message → looked frozen.
  Fix: probe with a short `WebDriverWait(5)` and only click when the tab is present
  (else log + continue). Benefits the quiz-writeback route too.
- **Empty captures: lazy-load race.** The Consistent Evaluation UI renders the question
  shell first and fills the answer body (typed text / uploaded-file `<d2l-list-item
  key="viewFile...">`) ~1-2 s later. `_capture_quiz_attempt` read immediately → missed
  files. Fix: `_wait_for_quiz_attempt_content` (`_QUIZ_ATTEMPT_READY_JS`) polls until a
  question + an answer signal (file item, real response text, or the explicit "- No text
  entered -" empty marker) is present before reading.
- Also added **per-student progress** (`Collecting attempt i/N: <name>...`) so the loop
  never looks stuck. NOTE: quiz *instructions* (`fetch_quiz_instructions`) returned empty
  on this quiz — still to tune; unrelated to submissions.

The original 2026-06-29 write-up (still accurate for the mechanism) — confirmed against the
live read-only quiz (qi=3000001 ou=200003):
- **Grid URL** derived from the pasted quiz URL by `derive_quiz_grading_url` →
  `/d2l/lms/quizzing/admin/mark/quiz_mark_users.d2l?ou=<ou>&qi=<qi>` (also scans a nested
  `returnUrl` for qi/ou). Reaching the grid opened all 16 learner attempts.
- **Grid is grouped by learner** (not one flat row each): a NAME row (single `<td>` =
  learner name), then 1+ ATTEMPT rows (mark link + Completed date + score + status), then an
  "overall grade" summary row. The attempt row has NO name — `_GATHER_QUIZ_ATTEMPTS_JS`
  deep-scans for each `mark,<attemptId>,<userId>` onclick and walks BACKWARD to the nearest
  name row (all 16 names resolved correctly live). `markoverall,0,<userId>` is excluded by
  the `mark,<id>,<id>` regex. `_keep_last_attempt_per_user` prunes to each learner's latest.
- **Opening an attempt:** Selenium light-DOM XPath `//a[contains(@onclick,'mark,<ai>,<ui>')]`
  finds the link (confirmed found+visible); `.click()` fires `Nav.Go(...,false,false)`
  same-window → lands on `/d2l/le/activities/iterator/<id>?...cft=quiz-attempts-users`.
  `_open_quiz_attempt` re-loads the grid each iteration so the link element is never stale.
- **Capturing the answer (`_READ_QUIZ_ATTEMPT_JS`, deep-scans shadow roots + iframes):**
  - **File upload (verified):** renders as `<d2l-list-item key="<download-url>">` whose inner
    `<a>` has NO href. The real URL is the item's `key` attr, e.g.
    `/d2l/common/viewFile.d2lfile/Database/<id>/<filename>?ou=<ou>`. Fetching it with the
    session cookies returned the raw file (a 3 KB `.java`). Filename = inner anchor text.
  - **Written-response answer:** `.d2l-questions-written-response-question-response`; an empty
    answer instead has a `.d2l-questions-written-response-no-response` marker ("- No text
    entered -") which is SKIPPED. Captured text is saved with the first accepted extension
    (`_written_response_ext`) so the grader keeps it.
  - Plain `<a href>` `fileId`/`viewFile`/`download` links kept as a fallback.
  - Question text: `.d2l-html-block-rendered`.

## Grade write-back — Feature #4 (assignment=draft VERIFIED 2026-07-01; quiz=POST VERIFIED 2026-07-09/10)

Implemented in **`src/cqc_cpcc/utilities/brightspace_writeback.py`** +
`add_brightspace_writeback_element` (UI) on the Grade Assignment page (after the
feedback-docs/ZIP step). Pure core (buffer math, feedback HTML, result→item mapping,
name matching) is fully unit-tested; the Selenium write is isolated and `dry_run`-guarded
(default True: navigate + LOCATE targets, fill/save nothing).

**Score buffer:** configurable via the web app (number input, default 10%); add
`buffer_pct%` of max points to each score, **capped at max** — NOT hard-coded
(`apply_score_buffer`).

**Quiz Consistent Evaluation page (`/d2l/le/activities/iterator/<id>`) — VERIFIED LIVE
(read-only; dry-run locate matched both targets):**
- **Overall score input:** `<input aria-label="Attempt grade out of 200">` (wrapped by
  `<d2l-input-number>`/`<d2l-input-text aria-label="Attempt grade">`). Per-question:
  `<input aria-label="Question score out of 200">`. The previously-inferred
  `.d2l-consistent-eval-quiz-question-score` class is NOT present — score is aria-label
  based (`SCORE_INPUT_SELECTORS` leads with `input[aria-label^='Attempt grade']`).
- **Overall feedback editor:** `<d2l-htmleditor label="Overall Feedback">` (per-question:
  `<d2l-htmleditor label="Feedback" class="d2l-consistent-eval-quiz-question-feedback">`).
  `FEEDBACK_EDITOR_SELECTORS` leads with `d2l-htmleditor[label='Overall Feedback']`.
- ⚠️ **Quiz has NO draft — it POSTS immediately.** An already-published attempt shows
  primary **"Update"** + **"Retract"** (an unpublished one shows **"Publish"**); there is
  NO "Save Draft" here. The ASSIGNMENT (dropbox) eval page has the cleaner "Save Draft" vs
  "Publish" pair. So on the quiz route the score+feedback are published on write — the web
  app relabels the button **"Write Grades and Feedback to Brightspace"** and shows a warning
  notice when `_is_quiz_writeback_url(url)` is true; the report says "posted".

**QUIZ WRITE — FIXED & VERIFIED LIVE 2026-07-09/10.** Real POST on qi=3000002 ou=200004
("Programming Exam 2"): posted 42/200 + overall feedback to a learner, re-read fresh from the
server = both persisted, then reset to 0/empty. Key mechanics (all in `brightspace_writeback.py`):
- **Score = REAL keystrokes** (`_fill_score`): the `d2l-input-number` Lit component ignores the
  native value setter — must `send_keys` (scrollIntoView → focus → `Keys.END` →
  `Keys.BACKSPACE*12` to CLEAR, since Ctrl+A+Delete appended → "4242" → type → `Keys.TAB`).
- **Commit** clicks Update/Publish, then D2L pops **"final score ≠ sum of question points…
  continue anyway?"** → `_confirm_dialog` clicks **Yes**; it EXCLUDES `_DESTRUCTIVE_DIALOG_TEXTS`
  ("discard/reset auto-evaluation/resubmitted/in progress") and must NEVER confirm that one.
- **Overall feedback is on the "Completion Summary" attempt view** (`_switch_quiz_view` flips
  `<select aria-label="User Attempts">`), then `_write_feedback_via_editor` into
  `d2l-htmleditor[label='Overall Feedback']`, then Save.
- **Lazy-load race fixed** by `_wait_for_write_targets` (polls until the score input renders) —
  this was the dry-run "fields not found" failure.

**DOCKER PROFILE PERSISTENCE — FIXED 2026-07-09.** `get_docker_driver` never set
`--profile-directory` (ephemeral `Default` → re-MFA every run). Now (non-headless) adds
`--user-data-dir=/home/seluser/chrome-profile` + `--profile-directory=<INSTRUCTOR_USERID>`;
KMSI now clicks **"Yes"** (`_accept_stay_signed_in`, persistent cookie). Verified: consecutive
runs skip MFA.

**FEEDBACK DELIVERY: attach .docx (default) vs inline — added 2026-07-10.**
`push_grades_to_brightspace(..., feedback_mode="attach"|"inline")` + a web-app radio. Attach
uploads each student's clean `Feedback.docx` (`GradeWriteItem.feedback_doc_path`, sourced from
`st.session_state["feedback_doc_paths_by_key"][run_key]`): on the QUIZ route per-attempt to the
eval page's attachment widget alongside the posted score; on the ASSIGNMENT route as ONE bulk
"Add Feedback Files" ZIP import (see below). Inline injects `feedback_html` per student. Also
`build_feedback_html` now renders an **"Errors Observed"** section from
`result.detected_errors` (matches the .docx).

**FILE ATTACH FLOW — VERIFIED LIVE 2026-07-10 (`_attach_feedback_file`, quiz Completion
Summary).** Legacy nested-iframe picker; the working mechanics:
1. JS-click "Attach" opener → 2. **synthetic** pointer-click "File Upload" (Lit menu item —
   bare `.click()` ignored) → opens `<iframe title="Add a File">` (TWO siblings render; the
   **LAST is the active/top** dialog) → 3. in the active frame, click
   `a.d2l-datalist-item-actioncontrol[title='My Computer']` (offscreen `<a>` the framework
   binds; drive via JS, Selenium `is_displayed()` lies) → Upload pane →
   4. **REAL Selenium `.click()`** on `div.d2l-fileinput-addbuttons button` — a trusted
   user-gesture is REQUIRED for the legacy "MFI" uploader to create its `<input type=file>`
   (a JS/synthetic click is blocked by Chrome's file-picker user-activation rule) →
   5. the input appears in ~1s; `send_keys(abspath)` (LocalFileDetector uploads to the Docker
   node) → 6. JS-click "Add" → the file lands under feedback "Attachments", committed on Save.
   The widget (`d2l-consistent-evaluation-attachments-editor`) is SHARED by the assignment eval
   page — **assignment route uses the same UI; verify with a real assignment URL**.

**ASSIGNMENT ATTACH = BULK "ADD FEEDBACK FILES" ZIP IMPORT — VERIFIED LIVE 2026-07-10
(ou=200002 db=600002; imported Ben Sample's Feedback.docx as a draft, then removed it
cleanly).** The assignment route does NOT reuse the per-attempt `_attach_feedback_file`.
Instead, ATTACH mode delivers ALL clean feedback `.docx` files in ONE bulk upload via the
dropbox submissions page's header button **"Add Feedback Files"** — BrightSpace distributes
each file to the matching submitter as DRAFT feedback, matched PURELY by the **leading
submission-ID** in its enclosing folder name (the same ID-bearing name the download produced =
`GradeWriteItem.student_key`). Only SUBMITTERS are matched (non-submitters have no download ID
and are skipped). This route writes NO scores — scores/rubric use inline mode. Code:
`build_feedback_docs_zip` (packs `{student_key}/Feedback.docx`), `import_feedback_zip` (JS-click
"Add Feedback Files" → LAST `iframe[title='Add Feedback Files']` = active dialog → **REAL**
`.click()` on `div.d2l-fileinput-addbuttons button` → `send_keys(zip)` → JS-click "Add", keep
"Overwrite Duplicate Files" checked), `_import_assignment_feedback_docs` (wires it into
`_push_assignment_grades` when `feedback_mode=="attach"`). Cleanup/remove control:
`d2l-button-icon`/`button` aria-label `"Remove Attachment: <filename>"` inside
`d2l-consistent-evaluation-attachments-editor`.

**Still UNVERIFIED (needs a safe write target):** the ASSIGNMENT route's real Save-as-draft
FILL was verified 2026-07-01 (CSC134 Project 2); its per-student evaluate-link discovery
(`_gather_assignment_learners` / `_open_assignment_evaluation`) remains best-effort (used only
by INLINE mode now — attach mode bypasses it). The per-attempt `_attach_feedback_file` flow is
verified on the QUIZ route only.

---

## Selectors most likely to need live tuning
- `EDIT_ASSIGNMENT_TOGGLE_XPATH` / `_EDIT_ASSIGNMENT_LOCATION_JS`
  (confirmed: `<button class="d2l-button" data-location="/d2l/le/activities/edit/...">Edit Assignment</button>`)
- `_READ_EDITOR_INSTRUCTIONS_JS` — **the failing editor-body read**
- `QUIZ_QUESTION_XPATH`, `QUIZ_UPLOAD_ATTACHMENT_XPATH`, `QUIZ_ROW_XPATH`
- `_INSTRUCTIONS_TEXT_JS` (view-mode `d2l-html-block`)

All live in `src/cqc_cpcc/utilities/brightspace_fetch.py`.

---

## Quick commands
```bash
# Live selector REPL (attaches to the project's real Selenium session)
HEADLESS_BROWSER=false BROWSER_TYPE=DOCKER_CHROME DOCKER_TYPE=LOCAL \
  poetry run python scripts/brightspace_selector_probe.py \
  --url "https://brightspace.cpcc.edu/d2l/lms/dropbox/admin/folders_manage.d2l?ou=200001"

# Offline paste-source search (descends declarative shadow DOM; no iframe bodies)
poetry run python scripts/brightspace_selector_probe.py --paste-file page.html   # or: --paste-file -

# Full fetch walkthrough (prints captured instructions + ZIP tree)
HEADLESS_BROWSER=false poetry run python scripts/brightspace_fetch_walkthrough.py --url "<assignment-or-quiz-url>"

# Tests
poetry run pytest tests/unit/test_brightspace_fetch.py tests/integration/test_brightspace_submissions.py -q
```
