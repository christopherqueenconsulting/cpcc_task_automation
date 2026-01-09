#!/bin/bash

#
#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
#

# Check if a test type was provided as an argument
if [ "$#" -eq 1 ]; then
    method="$1"
else
    # Prompt the user for which test to run
    echo "Which tests would you like to run?"
    select method in "all" "unit" "integration" "e2e"; do
        break
    done
fi

# Run the selected test type
case $method in
    all)
        # Run all tests
        poetry run pytest --cov=src --cov-report=term-missing --cov-report=xml
        ;;
    unit)
        # Run unit tests
        poetry run pytest --cov=src --cov-report=term-missing --cov-report=xml -m unit --ignore=tests/e2e
        ;;
    integration)
        # Run integration tests
        poetry run pytest -m integration
        ;;
    e2e)
        # Run end-to-end tests (Playwright + Streamlit)
        # Runs tests marked with "e2e" located in tests/e2e
        poetry run pytest -m e2e tests/e2e
        ;;
    *)
        # Invalid option
        echo "Invalid option: $method"
        echo "Usage: $0 [all|unit|integration|e2e]"
        exit 1
        ;;
esac