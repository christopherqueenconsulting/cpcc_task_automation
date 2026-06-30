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
2. **Quiz instructions** = first question — ✅ **selector fixed & verified live 2026-06-29**
   (`.question-text` on the quiz edit page; see "Quiz route — live DOM" below).
3. **Quiz submissions** — ✅ **REWRITTEN & verified live 2026-06-29.** `fetch_quiz_file_uploads`
   now drives the Consistent Evaluation UI (NavInfo onclick attempts; per-learner grouping;
   file URL from `d2l-list-item[key]`; written-response capture). See "Quiz route — live DOM"
   below.
4. **MFA prompt** rendering on the web-app page — ✅ **VERIFIED live 2026-06-29**
   (number rendered on the Grade Assignment page: "🔐 Two-factor approval needed → 40";
   approved → login completed).
5. **End-to-end web-app flow** — ✅ **VERIFIED live 2026-06-29** via Playwright. Fetch →
   MFA on page → approve → (login-race fix) re-navigate → Download All + last-attempt
   prune ("Pruned 1 older attempt for Carol Batidzirai") → preview "route: assignment"
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

**Quiz submissions — ✅ REWRITTEN & VERIFIED LIVE 2026-06-29 (`fetch_quiz_file_uploads`).**
The new `fetch_quiz_file_uploads` drives the Consistent Evaluation UI end-to-end; every
selector/mechanism below was confirmed against the live read-only quiz (qi=1015474 ou=304048):
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

## Draft grade write-back — live DOM map (for Feature #4)

The same Consistent Evaluation page (`/d2l/le/activities/iterator/<id>`) is where grades
+ feedback are entered, so write-back drives it per student:
- **Per-question score input:** `.d2l-consistent-eval-quiz-question-score`.
- **Per-question feedback editor:** `<d2l-htmleditor class="d2l-consistent-eval-quiz-question-feedback">`
  (same nested shadow-DOM/TinyMCE write target style as the assignment editor).
- **Overall feedback + overall score:** present on the same page (overall-grade nav =
  `markoverall,0,<userId>`).
- **Draft vs Publish:** publishing is a separate action (rows showed
  `Status: Published:<date>`). **Save without Publish = draft** — the feature must Save
  as draft only and never click Publish, so the instructor reviews then publishes later.
- Apply the **+10% error-buffer** to the computed score before writing; honor the
  rubric if the assignment/quiz uses one.

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
  --url "https://brightspace.cpcc.edu/d2l/lms/dropbox/admin/folders_manage.d2l?ou=338334"

# Offline paste-source search (descends declarative shadow DOM; no iframe bodies)
poetry run python scripts/brightspace_selector_probe.py --paste-file page.html   # or: --paste-file -

# Full fetch walkthrough (prints captured instructions + ZIP tree)
HEADLESS_BROWSER=false poetry run python scripts/brightspace_fetch_walkthrough.py --url "<assignment-or-quiz-url>"

# Tests
poetry run pytest tests/unit/test_brightspace_fetch.py tests/integration/test_brightspace_submissions.py -q
```
