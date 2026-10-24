# Student Data, Logs and Telemetry

This application processes FERPA-covered education records: student names, IDs,
e-mail addresses, submissions, grades and feedback. This page lists every place that
data can go, what protects it there, and how to configure the optional PostHog usage
analytics.

## Where student data goes

| Destination | What reaches it | Protection |
|---|---|---|
| **Log files** `logs/cpcc_*.log`, `logs/openai/*.log` | Operational messages | Every line is scrubbed by `pii_redaction` (below). Directory `0700`. 50 MB × 5 files per day, deleted after `CQC_LOG_RETENTION_DAYS` (default 14). |
| **Console / Streamlit log panel** | Same messages | Same scrubbing filter. The panel no longer forces DEBUG on. |
| **AI debug dumps** (`CQC_AI_DEBUG_SAVE_DIR`, off by default) | Requests and responses | Student identifiers are always scrubbed from files on disk; `CQC_AI_DEBUG_REDACT` (default on) also masks secrets, e-mails, phone numbers and SSNs. Files `0600`. Submission *content* is still present: keep this off unless you are debugging. |
| **Clipboard / downloaded prompt (.md)** from the LLM Prompt tab of a student result | The full grading prompt, including that student's submission and any name in it | Leaves the app's controls once copied or downloaded; the instructor's responsibility. The prompt is never saved with results (a restored run has none). Each message is shown as a code block, never rendered. |
| **Temp files** (downloaded ZIPs, extracted submissions, screenshots, generated `.docx`) | Student work | Written to a private `<system tmp>/cqc_cpcc/` (`0700`). Anything older than `CQC_TEMP_RETENTION_HOURS` (default 24) is deleted when the app starts. |
| **Saved grading results** `~/.cqc_cpcc/grading_results/` (`CQC_GRADING_RESULTS_DIR` overrides) | Student names, scores, feedback, detected errors (including raw compiler diagnostics, which are instructor-only) | Kept so results survive a restart. Files `0600` in a `0700` directory, outside the repository. Only the newest 20 runs are kept. Local only: never uploaded. Results are session-only (not saved) on Streamlit Community Cloud (decision 3, 2026-10-23). On any other shared host, setting `CQC_GRADING_RESULTS_DIR` or `CQC_RESULTS_PERSIST=on` stores every instructor's student data on one disk, and such a host defaults to ON: set `CQC_RESULTS_PERSIST=off` there, and in the Streamlit Cloud secrets as well (belt and braces). The result-card JSON export leaves out the raw diagnostics, the output-check report and the program-output lines of a gate-added error's note; program output that IS saved in the results file is in the "Saved grading results: program output" row below. |
| **Withdrawal CSVs** (`WITHDRAWALS_CSV_DIR`) | Names, IDs, e-mails | By design. Outside version control and git-ignored. Protect the folder. |
| **OpenRouter** and the model provider it routes to | Submission content, instructions, rubric | Needed for grading. Every LLM path (rubric, exam/errors-only, digest, Give Feedback, Flowgorithm) goes through OpenRouter. Each request asks for zero data retention, no data collection and no fallback to other providers; the policy lives in `src/cqc_cpcc/config/model_policy.json`. The `Submission File Name:` line in prompts is scrubbed. Header comments inside submissions are sent as written, because rubrics commonly grade them. A `session_id` passed to the gateway (no caller sets one today) would be sent to OpenRouter as the request's `user` field: it must be the run key, never a student id or name. See [Vendor settings](#vendor-settings-you-control). |
| **OpenRouter**: program output (output check, PR 14) | Output of a student's program run in the sandbox. The verdict table sends each test case's input (the stdin, cut to 48 characters per table cell) and its label from the plan, with the verdict. Each case that did not match also sends its diff of expected against actual output (up to 20 lines or 1,500 characters; the model sees those lines in full) and, for a crash, the first 400 characters of stderr. The 80-character cut applies only to the single expected and actual line quoted to the student in a gate-added error's note, not to what the model sees. The driver caps stdout at 64 KB and stderr at 16 KB. File names and the instructor's solution source are not in it | Only after a verified run, only inside a per-call nonce fence the model is told is untrusted. Same zero-retention policy as the rest of the grading prompt. Never sent to PostHog. |
| **OpenRouter**: the instructor's solution (test-case generation, PR 13) | The reference solution's source, once per assignment (cached), to suggest test inputs | Not sent when `CQC_EXECUTION=off` or Docker is missing (`derive_execution_plan` and the Setup tab's **Suggest test cases** button both check first); the button sends nothing until the instructor presses it, and shows its estimated cost. Never shown to students and never in a feedback document. |
| **Saved grading results**: program output | `execution_report.cases[].diff_excerpt` and the notes of gate-added errors (case input, one expected and one actual line) | In the same local results file as above (`0600`, newest 20 runs). Session-only on Streamlit Community Cloud (`persistence_enabled()` is false there). Program output copied or exported out of the app (the Output tab, **Copy feedback as Markdown**, **Download prompt (.md)**) is a student education record and the instructor's responsibility. Purge steps: `docs/execution-gate.md`, "If you suspect an escape or a leak", step 7. |
| **OpenAI** (direct) | Audio/video submissions only | Whisper transcription. `OPENAI_API_KEY` is optional and needed only for this. |
| **OneCompiler** (onecompiler.com) and **OnlineGDB** (onlinegdb.com) | One student's code | Sent only when the instructor acts on that student's Code tab. OneCompiler: the editor loads and the code is posted to it only after **Run in OneCompiler** is clicked. OnlineGDB: **Open in OnlineGDB** opens a blank editor, and the code reaches it only if the instructor pastes it in. No data-processing agreement is in place with either site, and their FERPA status has not been verified. |
| **PostHog** (optional) | Counts, durations, token usage, model names, scrubbed error types | No student data, not even an alias. See [Usage analytics](#usage-analytics-posthog). |
| **BrightSpace, MyColleges, Attendance Tracker** | Grades, attendance, withdrawal rows | These are the institution's own systems. Grade write-back defaults to a dry run. |

## How log redaction works

`src/cqc_cpcc/utilities/pii_redaction.py` is attached as a `logging.Filter` to every
handler: file, console, OpenAI-debug and Streamlit. It rewrites each message and each
traceback before anything is written.

- **Structural patterns** need no setup. They cover BrightSpace submission folder
  names (`<userid>-<subid> - First Last`), `ou=`/`qi=`/`db=`/`userId` ids,
  `mark,<attempt>,<user>` tokens, e-mail addresses, URL query strings and numeric path
  segments, `viewFile` download paths, and digit runs of six or more.
- **Known students.** Wherever the app reads a roster, a withdrawal record, a
  submission folder, a BrightSpace name table, quiz attempts or assignment learners,
  it registers each student. After that, every spelling of the name becomes the
  student's alias, for example `Last, First`, `First Last`, `LAST FIRST` or
  `First_Last.java`. Registered IDs and e-mails become the alias too.
- **Aliases** look like `student_3f9a2c1d`. Each is an HMAC of the name under a
  machine-local key, `~/.cqc_cpcc/pii_alias.key`, created on first use with mode
  `0600`, or `CQC_PII_ALIAS_KEY` if you set it. The same student gets the same alias
  on every run, so you can follow one student through the logs. An alias cannot be
  reversed by hashing a class roster without that key.
- Code should still log `alias(name)` or counts rather than rely on the filter. The
  filter is defence in depth. Single-word names are deliberately not scrubbed
  globally ("Will", "Grace"), so a student known only by a first name can slip
  through a free-text message.

## Usage analytics (PostHog)

Analytics are optional. They show that runs are happening, how long they take, how
many tokens they use, and where the AI output quietly degrades.

### Turning it on

1. `poetry install -E telemetry` installs `posthog` v7.
2. Provide the project key, either in `.env`
   (`POSTHOG_API_KEY=phc_...`, `POSTHOG_HOST=https://us.i.posthog.com` or
   `https://eu.i.posthog.com`), or on the Streamlit **Settings** page under
   **Usage Analytics (PostHog)**. The Settings page applies immediately and is not
   saved to disk.
3. The Settings page shows the current status, for example
   `disabled: the posthog package is not installed`. The log also says
   `PostHog analytics enabled (host: ...)` on first use.

If PostHog's dashboard stays empty while the status says enabled, check that the host
matches the project's region, and check the organisation's **billing / event limits**.
PostHog drops events silently once a quota is hit.

### Events

| Event | When | Properties |
|---|---|---|
| `$ai_generation` | Each OpenAI or OpenRouter call | model (the routed model for OpenRouter), provider, schema/span name, latency, input/output tokens, attempt, fallback used, error flag and scrubbed error text |
| `$ai_span` | A silent degradation inside a call | `cqc_degradation`: `schema_validation_failed`, `empty_response`, `response_truncated`, `smart_retry_fallback`, `placeholder_backfill`, `model_fallback` (primary model failed, the role's fallback model answered); field *names* only |
| `cqc_run_completed` | End of each feature run: `attendance`, `withdrawals`, `project_feedback`, `brightspace_fetch`, `brightspace_writeback`, `rubric_grading`, `error_only_grading` | status (`succeeded`/`failed`/`interrupted`), duration, and counts such as courses, students, succeeded/failed, matched/saved; plus flags such as dry run, route, feedback mode and model |
| `$exception` | A feature run fails | exception type, scrubbed message, file/line/function frames. No source lines, no local variables. |

### Guarantees (enforced in `posthog_telemetry.py` and its tests)

- No prompt or completion text is ever sent, and there is no setting to send it.
  `$ai_input` / `$ai_output_choices` are blocked even if a caller supplies them.
- Only allowlisted PostHog properties and `cqc_*` properties are sent. Values must be
  scalars, every string is scrubbed and truncated to 500 characters, and the SDK's
  `before_send` hook scrubs again. An event that cannot be cleaned is dropped.
- `distinct_id` is a SHA-256 hash of `INSTRUCTOR_USERID`. Person profiles, GeoIP,
  exception autocapture and code-variable capture are off.
- Telemetry failures never affect a run. Buffered events are flushed at exit.

## Vendor settings you control

These are account settings, not code. Check them against your institution's policy.

- **OpenRouter**: turn on account-level **Zero Data Retention** and disable prompt
  logging and training in privacy settings. The app also sends
  `provider: {zdr: true, data_collection: "deny", allow_fallbacks: false}` on every
  request (from `model_policy.json`), so a request fails rather than reach a provider
  that does not meet that bar. `OPENROUTER_ALLOWED_MODELS` limits which models
  auto-routing may use. Which model each feature uses is set in
  `src/cqc_cpcc/config/model_registry.json`.
- **OpenAI** (Whisper only): review the organisation's data controls, including training
  opt-out and retention.
- **PostHog**: pick the region (US or EU) your institution prefers. No education
  records are sent, but the project still records the hashed instructor ID and usage
  patterns.

## FERPA notes

The instructor uses this tool on the institution's behalf. Sending student work to an
LLM provider is a disclosure of education records to a third party, so check whether
your institution's AI-use policy and vendor agreements cover it. The LLM providers are
not the only vendors that need that review: the code runners (OneCompiler and
OnlineGDB) receive a student's code when the instructor runs it, and neither has an
agreement in place. A prompt copied or downloaded from the LLM Prompt tab is a
disclosure to whatever tool receives it, so paste it only into tools the institution
has approved for student records. Every other outside destination is kept free of
student identifiers.

## Student data in the repository

The repository is public, so the `pii-guard` required check (`scripts/pii_guard.py`,
see `docs/security-workflows.md`) blocks BrightSpace submission-folder names and ids,
hashed known-leaked tokens, and any e-mail address at `cpcc.edu` or `*.cpcc.edu`. Fixtures
and docs use only the synthetic local parts the script allowlists, for example
`student.example@email.cpcc.edu`.
