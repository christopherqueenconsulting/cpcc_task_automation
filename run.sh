#!/bin/sh


# Prompt the user to run via steamlit or poetry
echo "Would you like to run the app via streamlit or poetry?"
# Give the user default options and use select case to process
select method in "streamlit" "poetry"; do
    case $method in
        streamlit)
            # Run the app via streamlit
            poetry run streamlit run ./src/cqc_streamlit_app/Home.py
            break
            ;;
        poetry)
            # Run the app via poetry
            poetry run python ./src/cqc_cpcc/main.py
            break
            ;;
        *)
            # Prompt the user to select a valid option
            echo "Invalid option. Please select a valid option."
            ;;
    esac
done


