# Running the Rubric Grading Integration Test (Live OpenAI)

This script and test exercise the rubric grading pipeline against the real OpenAI API.

Prerequisites:
- Python (Poetry-managed environment as used by the project)
- A valid `OPENAI_API_KEY` environment variable (do not commit your key)

How to run (macOS / zsh):

1. Install dependencies (if not already):

```bash
poetry install --with test,dev
```

2. Export your OpenAI API key for the session (example):

```bash
export OPENAI_API_KEY="sk-..."
```

3. Run the integration test script (will skip if no key present):

```bash
./scripts/run_integration_rubric_test.sh
```

What to expect:
- If `OPENAI_API_KEY` is not set, the script will run pytest but the live test will be skipped.
- If the key is set, the test will make a single call to OpenAI via `get_structured_completion()` and assert
  that the returned `RubricAssessmentResult` is valid and that totals match the sum of criteria.

Notes:
- No secrets are stored in the repository. Keep your OpenAI key secure.
- The test uses a small synthetic submission and should be fast, but API latency may vary.
