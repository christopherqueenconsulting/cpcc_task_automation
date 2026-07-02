# CPCC Task Automation — a GenAI app that automates repetitive instructor admin work

An AI-assisted automation platform for college instructors. It turns hours of weekly
administrative busywork — attendance tracking, writing student feedback, and grading —
into a few minutes of guided, reviewable automation. Built as a solo/portfolio project
and used against my own courses.

[▶ **Live app**](https://cpcc-task-automation.streamlit.app/) — hosted on Streamlit
Community Cloud. Access is **invite-only** (it drives real LMS/SIS accounts); email
**christopher.queen@gmail.com** for an invite.

---

## Problem

Instructors at **CPCC (Central Piedmont Community College)** — especially those teaching
programming courses — lose hours every week to repetitive admin work that lives across
two clunky systems: **BrightSpace/D2L** (the LMS) and **MyColleges** (the SIS). Taking
attendance means cross-referencing each student's activity in BrightSpace and re-entering
it in MyColleges. Feedback and grading mean downloading each submission by hand, reading
it, and typing scores and comments back one student at a time. It's slow, error-prone,
and it's time that should go to teaching.

This app automates those workflows end-to-end with an LLM and browser automation, so the
work takes minutes instead of an evening.

## Approach

A multi-page **Streamlit** application backed by **LangChain** and hosted LLMs (via
**OpenRouter**, with OpenAI as a legacy path). Design goals were speed to a working tool
and a UI simple enough that a non-technical instructor can run it without help:

- **Streamlit** front end — no install for the user, runs in the browser, one page per task.
- **LangChain + OpenRouter** do the language-heavy lifting (feedback generation, rubric-based
  grading, extraction/summarization). OpenRouter means the model is swappable — it currently
  defaults to **GPT-5 / gpt-5-mini / gpt-5-nano** and can route to any supported model
  (including Claude) without code changes.
- **Selenium + BeautifulSoup** drive the real systems the instructor already uses
  (BrightSpace/D2L and MyColleges), including login with Duo/Microsoft **MFA** surfaced
  in the UI, shadow-DOM/iframe traversal for D2L's web components, and file downloads.
- **Document parsing** (`python-docx`, `pypdf`, `pymupdf`, `mammoth`, `textract`) reads
  student submissions in whatever format they arrive.
- Prompt templates tuned per task, with a human **review/edit step** before anything is
  written back — grades are saved as **drafts**, never auto-published.

**Key tradeoffs:** chose Streamlit over a full web stack to ship fast and stay maintainable
as a solo project; used hosted LLMs via OpenRouter rather than self-hosting to avoid infra
overhead and keep the model swappable; kept a human in the loop on every write-back because
the target systems are systems of record.

## Features

- **Attendance tracking** — scrapes BrightSpace activity completion (assignments, quizzes,
  discussions) and records attendance in MyColleges and a tracking spreadsheet.
- **Project feedback** — generates personalized, rubric-aware feedback on student
  submissions for the instructor to review and edit.
- **Exam / rubric grading** — grades submissions against custom rubrics and error
  definitions; supports a configurable score buffer.
- **Student lookup** — finds and summarizes student information across systems.
- **BrightSpace submission fetch** — paste an Assignment or Quiz URL and the app collects
  each student's files into the exact folder layout the grader expects, with a
  preview/edit step before grading.
- **Draft grade write-back** — pushes AI grading results back into BrightSpace as
  **drafts** (score + rubric levels + formatted feedback), verified live and guarded so it
  never publishes.

## Architecture

```
Instructor
    │
    ▼
Streamlit UI (multi-page)
    │
    ├──► App logic (Python) ──► LangChain ──► OpenRouter / OpenAI  (GPT-5 family, swappable)
    │
    └──► Selenium + BeautifulSoup ──► BrightSpace / D2L  +  MyColleges
                │                         (login + MFA, scraping, downloads, draft write-back)
                ▼
        Document parsing (docx / pdf) · zip build · tracking spreadsheet
```

- **Models:** OpenRouter-routed, defaulting to OpenAI **GPT-5 / gpt-5-mini / gpt-5-nano**
  (any OpenRouter model, e.g. Claude, can be selected); Whisper for audio transcription.
- **Data flow:** instructor pastes a BrightSpace/MyColleges URL or uploads submissions →
  app scrapes/parses the inputs → LangChain builds the prompt → model returns feedback or
  grades → instructor reviews/edits in the UI → app writes results back as drafts.

## Results

- **Value:** collapses roughly **5–10 hours of weekly admin work into ~15 minutes** of
  guided, reviewable automation (the project's own real-use target).
- **Scope handled:** processes a full class roster per run — fetch all submissions, grade,
  and draft feedback across every student in one pass.
- **Quality/safety:** every AI output is human-reviewed before submission, and all
  write-backs are saved as **drafts** (never published) — the browser automation for the
  assignment write-back (learner matching, rubric-level selection, score, and
  persisting formatted feedback) has been verified live end-to-end.

Built and used to automate my own grading/attendance workflow — this is a personal tool,
so the numbers above reflect real use rather than a formal benchmark.

## Stack

Python 3.12 · Streamlit · LangChain · OpenRouter · OpenAI (GPT-5 family) · Selenium ·
BeautifulSoup · pandas · ChromaDB · python-docx / pypdf / pymupdf · Poetry · Docker
(Selenium grid) · deployable on Streamlit Community Cloud

## Run it yourself

Requires **Python 3.12+**, **[Poetry](https://python-poetry.org/docs/#installation)**, and
**Chrome** (for the browser-automation features).

```bash
# 1. Clone
git clone https://github.com/gitchrisqueen/cpcc_task_automation.git
cd cpcc_task_automation

# 2. Install dependencies (Poetry, not pip)
poetry install

# 3. Add credentials — .streamlit/secrets.toml (local) or environment variables
#    OPENROUTER_API_KEY = "sk-..."     # recommended (routes the LLM)
#    OPENAI_API_KEY     = "sk-..."     # legacy / optional
#    INSTRUCTOR_USERID  = "..."        # MyColleges / BrightSpace login
#    INSTRUCTOR_PASS    = "..."

# 4. Run the app
poetry run streamlit run src/cqc_streamlit_app/Home.py
#    ...or use the interactive launcher (UI or CLI):
./run.sh
```

Then open `http://localhost:8501`.

**Hosted version:** a live instance runs on Streamlit Community Cloud at
**https://cpcc-task-automation.streamlit.app/**. It's **invite-only** — because it logs
into real BrightSpace/MyColleges accounts, access is granted per user; email
**christopher.queen@gmail.com** to request access.

## What I'd do next

Productionization path if this grew beyond a personal tool:

- **RAG grounding** — index course/policy docs (ChromaDB is already a dependency) so
  feedback and grading cite real course materials instead of relying on general knowledge.
- **Evals harness** — a small custom suite (or ragas) to measure feedback/grading quality
  across a labeled test set — the piece most portfolios skip.
- **Auth + persistence** — multi-user accounts and an audit history of every write-back.
- **Structured output / function calling** — make grading results reliably parseable and
  reduce prompt-format brittleness.
