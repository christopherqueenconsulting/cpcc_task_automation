#!/bin/bash

# Check if the .env file exists
if [ -f .env ]; then
    # Automatically export all variables
    set -a
    # Source the .env file
    source .env
    # Disable automatic export
    set +a
fi

# Define the options
options=("streamlit" "poetry")

# Function to generate the prompt
generate_prompt() {
    local default_option=$1
    shift
    local options=("$@")
    local options_list=$(IFS=, ; echo "${options[*]}")
    echo "Would you like to run the app via ${options_list}? (default: $default_option)"
}

# Generate the prompt using the first option as default
prompt=$(generate_prompt "${options[0]}" "${options[@]}")
warning="Warning: dialog is not installed. Please install it using 'brew install dialog' and try again."

# Function to display the menu and get user choice
display_menu() {
    echo "$prompt"
    select method in "${options[@]}"; do
        if [ -n "$method" ]; then
            echo "Selected Method: $method"
            break
        else
            echo "Invalid option. Please select a valid option."
        fi
    done
}

# Function to run the app based on the selected method
run_app() {
    local method=$1
    case $method in
        "${options[0]}")
            echo "Running via ${options[0]}"
            poetry run streamlit run ./src/cqc_streamlit_app/Home.py
            ;;
        "${options[1]}")
            echo "Running via ${options[1]}"
            poetry run python ./src/cqc_cpcc/main.py
            ;;
        *)
            echo "Invalid option. Please select a valid option."
            return 1
            ;;
    esac
    return 0
}

# Check if dialog is installed
if command -v dialog &> /dev/null; then
    # Use dialog to present the options
    dialog --menu "$prompt" 0 0 0 \
    1 "${options[0]}" \
    2 "${options[1]}" 2>tempfile

    # Read the user's choice
    choice=$(<tempfile)
    rm -f tempfile

    # Set default option if no choice is made
    if [ -z "$choice" ]; then
        choice="1"
    fi

    # Map choice to method
    case $choice in
        1) method="${options[0]}" ;;
        2) method="${options[1]}" ;;
        *) method="" ;;
    esac
else
    # Notice the user to install Dialog
    tput setaf 3; echo "$warning"; tput sgr0

    # Use select to present the options
    display_menu
fi

# Run the app with the selected method
run_app $method