#!/usr/bin/env bash
#
# Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
#

# Source .env if present
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

if [ -z "$OPENAI_API_KEY" ]; then
  echo "OPENAI_API_KEY not set - skipping live rubric integration test (tests will be marked skipped)."
  echo "Set OPENAI_API_KEY in your environment to run the live test. Example: export OPENAI_API_KEY=sk-..."
  # Run pytest but allow skipped tests to pass
  poetry run pytest -k rubric_grading_live_integration -q || true
  exit 0
fi

# Run pytest for the live integration test
poetry run pytest -k rubric_grading_live_integration -q
