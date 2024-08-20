#!/bin/bash

#
# Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
#


# Prompt the user for which test to run
echo "Which tests would you like to run?"
select method in "all" "unit" "integration"; do
    case $method in
        all)
            # Run all tests
            poetry run pytest
            break
            ;;
        unit)
            # Run unit tests
            poetry run pytest -m unit
            break
            ;;
        integration)
            # Run integration tests
            poetry run pytest -m integration
            break
            ;;
        *)
            # Prompt the user to select a valid option
            echo "Invalid option. Please select a valid option."
            ;;
    esac
done
