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
options=("Web_App" "Command_Line")

# Simple prompt generator (no comma list)
generate_prompt() {
    local default_option=$1
    echo "Select run method (default: $default_option)"
}

# Build prompt and call dialog with explicit menu height
prompt=$(generate_prompt "${options[0]}")
menu_height=${#options[@]}
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
            poetry run python -m cqc_cpcc.main
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
    dialog --clear --title "Run App" --menu "$prompt" 10 60 "$menu_height" \
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