#!/bin/bash

named_pipes=()  # Declare an array to store pipe names

handle_cleanup(){
  # shellcheck disable=SC2317
  for var in "${named_pipes[@]}"; do
    rm -f "$var"
    #echo "Removed: $var"
  done
  echo "Cleaned up All tmp Files"
}

trap handle_cleanup EXIT

log_message() {
    local logfile="$1"
    local message="$2"
    echo "$message" >> "$logfile"
}

# Function to print a character with a typing effect
print_char() {
    local char="$1"
    local sleep_duration=0.1  # Default sleep duration

    # Check if the character is a newline or carriage return
    if [ "$char" == $'\n' ] || [ "$char" == $'\r' ]; then
        sleep_duration=.5  # Longer sleep duration for newline or carriage return
    fi

    # Print the character without a newline
    printf "%s" "$char"
    # Wait for a short time (default or longer duration) to simulate typing
    sleep "$sleep_duration"
}

# Function to simulate typing input to the Java command one character at a time
simulate_typing_char() {
    local input="$1"
    for ((i = 0; i < ${#input}; i++)); do
        print_char "${input:i:1}"
    done
}

simulate_typing_line() {
    local input="$1"

     printf " "
     #wait
     sleep .5

    while IFS= read -r line; do

        # Print the entire line without a newline
        printf "%s\n" "$line"
        #wait

        sleep .1

     done <<< "$input"
}

# Function to simulate typing input to the Java command with a delay between lines
simulate_typing_2() {
    local input="$1"
    local sleep_duration_line=0.1  # Sleep duration between lines
    local sleep_duration_newline=0.2  # Sleep duration for newline character

    # Prefix the input with a newline character
    #input=$'\n'"$input"

    # Iterate over each line in the input
    while IFS= read -r line; do

        # Print the entire line without a newline
        printf "%s" "$line" &

        sleep "$sleep_duration_line"

        # Print a newline character
        printf "\n" &

        # Wait for all process to stop
        wait

        # Wait for a short time to simulate pausing between lines
        sleep "$sleep_duration_newline"

        # Wait for all process to stop
        #wait

    done <<< "$input"
}

simulate_typing_3() {
    local input="$1"
    local output_pipe="$2"
    local sleep_duration_line=0.1  # Sleep duration between lines
    local sleep_duration_newline=0.2  # Sleep duration for newline character

    # Iterate over each line in the input
    while IFS= read -r line; do

        # Print the entire line with a newline
        printf "%s\n" "$line" &

        sleep "$sleep_duration_line"

        # Wait for all process to stop
        wait

        # Print the line to the output pipe
        printf "%s\n" "$line" > "$output_pipe" &

        # Wait for all process to stop
        wait

        # Wait for a short time to simulate pausing between lines
        sleep "$sleep_duration_newline"


  done <<< "$input"


}


# Function to trim leading and trailing whitespace from a variable
trim_whitespace() {
    local var="$1"
    # Trim leading whitespace
    var="${var#"${var%%[![:space:]]*}"}"
    # Trim trailing whitespace
    var="${var%"${var##*[![:space:]]}"}"
    # Output trimmed variable
    echo "$var"
}

# Function to remove empty lines from the beginning and end of a file
remove_empty_lines() {
    local file="$1"

     # Remove empty lines from the beginning
    sed -i '' '/^[[:space:]]*$/d' "$file"

    # Remove empty lines from the end
    sed -i '' -e :a -e '/^\n*$/{$d;N;};/\n$/ba' "$file"
}


# Function to find the class name with main method in a Java file
find_class_with_main() {
    found_class_name=""
    local java_file=$1
    local class_name
    local main_check

    # Extract class name from file
    class_name=$(grep -m 1 -E '^\s*(public|protected|private|abstract|final)?\s*class\s+[A-Za-z_][A-Za-z0-9_]*' "$java_file" | awk '{for (i=1; i<=NF; i++) if ($i == "class") { gsub(/\{/, "", $(i+1)); print $(i+1); }}')

    #echo "Class Name: $class_name"

    # Check for main method declaration
    main_check=$(grep -Ezo 'public[[:space:]]+static[[:space:]]+void[[:space:]]+main[[:space:]]*\([[:space:]]*String[[:space:]]*\[[[:space:]]*\][[:space:]]*[a-zA-Z_][a-zA-Z0-9_]*[[:space:]]*\)[[:space:]]*\{' "$java_file")

    #echo "Main check results: $main_check"

    # Update the found class name without whitespaces if main method is found
    if [ -n "$main_check" ]; then
        found_class_name=$(trim_whitespace "$class_name")
    fi
}

# Prompt the user for the main directory
read -rp "Enter the directory path containing sub-folders with code: " main_dir

# Prompt the user for the directory path containing sample input files
read -rp "Enter the directory path containing sample input files: " input_dir

# Ensure the main directory exists
if [ ! -d "$main_dir" ]; then
    echo "Main directory not found!"
    exit 1
fi

# Ensure the input directory exists
if [ ! -d "$input_dir" ]; then
    echo "Input directory not found!"
    exit 1
fi

# Iterate over each folder in the main directory
for folder in "$main_dir"/*; do
    if [ -d "$folder" ]; then
        # Get the folder name
        folder_name=$(basename "$folder")
        echo "Processing: $folder_name"

        # Find the Java file containing the main method
        main_java_file=""
        while IFS= read -r -d '' java_file; do
            main_java_file_name=$(basename "$java_file" .java)
            echo "Searching : ${main_java_file_name}.java"

            # Search for the class contianing the main method
            find_class_with_main "$java_file"

            if [ -n "$found_class_name" ]; then
               class_name="$found_class_name"
                echo "Main Class Found: $class_name"
                proper_file_name="$class_name.java"
                # If main_java_file_name does not have proper file name
                if [ "${main_java_file_name}.java" != "$proper_file_name" ]; then
                    echo "File Name Is Not Proper"
                    mv "$java_file" "$folder/$proper_file_name"
                    echo "${main_java_file_name}.java File Renamed : $proper_file_name"
                fi

                main_java_file="$proper_file_name"
                break # Break the while loop

            fi
        done < <(find "$folder" -type f -name '*.java' -print0)

        if [ -z "$main_java_file" ]; then
            echo "No main method found in any file inside $folder_name"
        else
            # Compile the Java files in the folder
            javac "$folder"/*.java

            # Run the Java program and save output for each input file
            for input_file in "$input_dir"/input_sample*.txt; do
                if [ -f "$input_file" ]; then
                    # Extract input file name without extension
                    input_name=$(basename "$input_file" .txt)
                    echo "Using file for input: $input_name"
                    input_text=$(<"$input_file")

                    # Run Java program and redirect input from input file, override output file if exists
                    output_file="${folder}/${input_name}_output.log"
                    error_file="${folder}/${input_name}_error.log"

                    timestamp=$(date +"%Y-%m-%d-%H-%M-%S")

                    #  Create the output file or erase if it exists
                    truncate -s 0 "$output_file"

                    # Create named pipes
                    java_output_pipe="/tmp/${folder_name}_${timestamp}_${input_name}_java_output"
                    typing_output_pipe="/tmp/${folder_name}_${timestamp}_${input_name}_typing_output"

                    mkfifo "$java_output_pipe" || { echo "Failed to create pipe: $java_output_pipe"; exit 1; }
                    named_pipes+=("$java_output_pipe")
                    mkfifo "$typing_output_pipe" || { echo "Failed to create pipe: $typing_output_pipe"; exit 1; }
                    named_pipes+=("$typing_output_pipe")

                    # Run Java command in the background
                    # Start the Java command and have it read from the named pipe
                    (java -cp "$folder" "$class_name" > "$java_output_pipe" 2> "$error_file" < "$typing_output_pipe") &

                    cat "$java_output_pipe" >> "$output_file" &

                    # Start the sub-process and write its output to the named pipe
                    (simulate_typing_line "$input_text" | tee -a "$output_file" > "$typing_output_pipe") &

                    # Wait for background processes to complete
                    wait

                    # Remove empty lines from the beginning and end of the outputfile
                    remove_empty_lines "$output_file"

                    # Check if error.log is empty
                    if [ ! -s "$error_file" ]; then
                        # If error.log is empty, delete it
                        rm "$error_file"
                    fi
                fi
            done
        fi
    fi
done

echo "Script Complete"
exit 0