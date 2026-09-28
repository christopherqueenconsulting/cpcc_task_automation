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
| **Temp files** (downloaded ZIPs, extracted submissions, screenshots, generated `.docx`) | Student work | Written to a private `<system tmp>/cqc_cpcc/` (`0700`). Anything older than `CQC_TEMP_RETENTION_HOURS` (default 24) is deleted when the app starts. |
| **Withdrawal CSVs** (`WITHDRAWALS_CSV_DIR`) | Names, IDs, e-mails | By design. Outside version control and git-ignored. Protect the folder. |
| **LLM providers** (OpenAI, OpenRouter) | Submission content, instructions, rubric | Needed for grading. The `Submission File Name:` line in prompts is scrubbed. Header comments inside submissions are sent as written, because rubrics commonly grade them. See [Vendor settings](#vendor-settings-you-control). |
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
| `$ai_span` | A silent degradation inside a call | `cqc_degradation`: `schema_validation_failed`, `empty_response`, `response_truncated`, `smart_retry_fallback`, `placeholder_backfill`; field *names* only |
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

- **OpenAI**: review the organisation's data controls, including training opt-out and
  retention, and whether your institution has a data-processing or zero-retention
  agreement.
- **OpenRouter**: in privacy settings, restrict routing to providers that do not train
  on or log prompts. `OPENROUTER_ALLOWED_MODELS` limits which models auto-routing may
  use.
- **PostHog**: pick the region (US or EU) your institution prefers. No education
  records are sent, but the project still records the hashed instructor ID and usage
  patterns.

## FERPA notes

The instructor uses this tool on the institution's behalf. Sending student work to an
LLM provider is a disclosure of education records to a third party, so check whether
your institution's AI-use policy and vendor agreements cover it. The design keeps
every other destination free of student identifiers, so the LLM providers are the
only vendors that need that review.
