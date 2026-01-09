#!/bin/bash

#
#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
#

# Prompt the user for which test to run
echo "Which tests would you like to run?"
select method in "all" "unit" "integration" "e2e"; do
    case $method in
        all)
            # Run all tests
            poetry run --cov=src --cov-report=term-missing --cov-report=xml pytest
            break
            ;;
        unit)
            # Run unit tests
            poetry run pytest --cov=src --cov-report=term-missing --cov-report=xml -m unit
            break
            ;;
        integration)
            # Run integration tests
            poetry run pytest -m integration
            break
            ;;
        e2e)
            # Run end-to-end tests (Playwright + Streamlit)
            # Runs tests marked with "e2e" located in tests/e2e
            poetry run pytest -m e2e tests/e2e
            break
            ;;
        *)
            # Prompt the user to select a valid option
            echo "Invalid option. Please select a valid option."
            ;;
    esac
done