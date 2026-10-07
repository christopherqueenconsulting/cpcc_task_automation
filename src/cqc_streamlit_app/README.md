# Streamlit UI Package

Multi-page Streamlit application providing web interface for CPCC Task Automation features.

## Features

Pages are grouped in the navigation (`Home.py` builds it; page scripts live in `app_pages/`).

### Grading
* **Grade assignment**: rubric grading for exams, projects and reflections, with the
  submission-validity gate (empty, missing or wrong-type work is scored 0 and held for
  your review) and the requirement checklist (unfinished work loses points for what it
  leaves out).
* **Flowgorithm assignments**: feedback and grading for Flowgorithm submissions.
* **Give feedback**: AI feedback on student project submissions (via OpenRouter).

### Students
* **Take attendance**: records attendance from BrightSpace activity in MyColleges and
  the tracking spreadsheet.
* **Find student**: looks a student up across your course rosters.

### Settings
Credentials (OpenRouter API key; OpenAI key only for audio/video transcription),
instructor login, analytics, a model per feature, and **Preferences**.

### Legacy (deprecated)
Hidden unless **Settings → Preferences → Show legacy pages** is on (saved to
`~/.cqc_cpcc/app_settings.json`). Currently **Exams (legacy)**, the pre-rubric exam
grader. Legacy pages will be removed once they are no longer used.

## Documentation

For detailed documentation about this package, see:

**[docs/src-cqc-streamlit-app.md](../../docs/src-cqc-streamlit-app.md)**

For overall project documentation:
- [Project README](../../README.md) - Quick start and overview
- [docs/README.md](../../docs/README.md) - Documentation hub
- [ARCHITECTURE.md](../../docs/ARCHITECTURE.md) - System architecture
- [PRODUCT.md](../../PRODUCT.md) - Product features and use cases

## Running the Application

```bash
# From project root
poetry run streamlit run src/cqc_streamlit_app/Home.py
```

Or use the interactive launcher:
```bash
./run.sh
```