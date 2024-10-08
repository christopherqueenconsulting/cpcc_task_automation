#!/bin/bash

named_pipes=()  # Declare an array to store pipe names

handle_cleanup(){
  for var in "${named_pipes[@]}"; do
    rm -f "$var"
    echo "Removed: $var"
  done
}

trap handle_cleanup EXIT


# Function to handle Java output and log it
handle_output() {
  local named_pipe="$1"
  local output_file="$2"
  timeout 10 tail -f "$named_pipe" | while IFS= read -r line; do
    log_message "$output_file" "$line"
  done

  #while IFS= read -r line; do
  #  log_message "$output_file" "$line"
  #done <<< "$named_pipe"
}

retrieve_log(){
  local start_timestamp="$1"
  local output_file="$2"
  log show --info --debug --predicate "process == 'syslog'" --start "$start_timestamp" | awk -F "output " '{print $2}' > "$output_file"
}

retrieve_log_stream(){
  local output_file="$1"
  log stream --info --debug --predicate "process == 'syslog'" | awk -F "$output_file " '{print $2}'
}


log_message() {
    local logfile="$1"
    local message="$2"

    # Use syslog utility to log the message to the specified log file
    syslog -s -l info -f "$logfile" "$message" &

    # Sleep to give the system control for a second
    sleep .1;

}

# Function to retrieve logs by tag using Logstash
retrieve_logs_by_tag() {
    local tag="$1"
    local output_file="$2"

    # Send request to Logstash to retrieve logs by tag
    # Adjust the Logstash host and port as needed
    curl -XGET 'http://localhost:9600/_node/stats/logstash/pipelines/main?pretty' \
         --data-urlencode "filter_path=pipelines.main.plugins.filters|select(.name == \"mutate\" and .tags[] == \"$tag\")" \
         > "$output_file"
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
simulate_typing() {
    local input="$1"
    for ((i = 0; i < ${#input}; i++)); do
        print_char "${input:i:1}"
    done
}

# Function to simulate typing input to the Java command with a delay between lines
simulate_typing_2() {
    local input="$1"
    local line
    local sleep_duration_line=0.1  # Sleep duration between lines
    local sleep_duration_newline=0.2  # Sleep duration for newline character

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

# Function to find the class name with main method in a Java file
find_class_with_main() {
    local java_file=$1
    local class_name

    # Extract class name from file
    #class_name=$(awk '/public[[:space:]]+class[[:space:]]+/{print $3}' "$java_file")
    class_name=$(awk '/public[[:space:]]+class[[:space:]]+/{print $3}' "$java_file" | awk -F '{' '{print $1}')

    # Extract main method declaration
    #if grep -q 'public\s\+static\s\+void\s\+main\s*(String\[\]\s*args)' "$java_file"; then
    if grep -q 'public\s\+static\s\+void\s\+main\s*(\s*String\s*\[\]\s*[a-zA-Z_][a-zA-Z0-9_]*\s*)' "$java_file"; then
        trimmed_class_name=$(trim_whitespace "$class_name")
        echo "$trimmed_class_name"
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
            echo "Searching : $main_java_file_name"

            class_name=$(find_class_with_main "$java_file")
            if [ -n "$class_name" ]; then
                main_java_file="$java_file"
                mv "$main_java_file" "$folder/$class_name.java"
                echo "Class Name Found and File Renamed : $class_name"
                break
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
                    #input_text=$(cat "$input_file")

                    #echo "Input Text:"
                    #echo "$input_text"

                    # Run Java program and redirect input from input file, override output file if exists
                    output_file="${folder}/${input_name}_output.txt"
                    error_file="${folder}/${input_name}_error.log"
                    #simulate_typing_2 "$input_text" | java -cp "$folder" "$class_name" > "$output_file" 2> "$error_file"

                    start_timestamp=$(date +"%Y-%m-%d %H:%M:%S")
                    timestamp=$(date +"%Y-%m-%d-%H-%M-%S")

                    #  Create the output file or erase if it exists
                    truncate -s 0 "$output_file"

                    # Create named pipes
                    java_output_pipe="/tmp/${folder_name}_${timestamp}_${input_name}_java_output"
                    typing_output_pipe="/tmp/${folder_name}_${timestamp}_${input_name}_typing_output"

                    # Make sure the named pipes are cleaned up whenever the script exits
                    #pipes=("$java_output_pipe" "$typing_output_pipe")
                    #trap `handle_cleanup "${pipes[@]}"` EXIT

                    mkfifo "$java_output_pipe" || { echo "Failed to create pipe: $java_output_pipe"; exit 1; }
                    named_pipes+=("$java_output_pipe")
                    mkfifo "$typing_output_pipe" || { echo "Failed to create pipe: $typing_output_pipe"; exit 1; }
                    named_pipes+=("$typing_output_pipe")

                    # Run Java command in the background
                    #retrieve_log_stream "$java_output_pipe" | while IFS= read -r line; do printf "%s\n" "$line"; sleep .1; &
                    log_message "$java_output_pipe" "Starting Java -> "
                    #THIS once close -> #simulate_typing_2 $(retrieve_log_stream "$typing_output_pipe") | java -cp "$folder" "$class_name" 2> "$error_file" | while IFS= read -r line; do log_message "$java_output_pipe" "$line" ; done &


                    #| \
                    #log_message "$java_output_pipe" "HEY" &


                    #(timeout 10 tail -f "$typing_output_pipe" | while IFS= read -r line; do printf "%s\n" "$line"; done) | (java -cp "$folder" "$class_name" > "$java_output_pipe" 2> "$error_file") &
                    #tail -f "$typing_output_pipe" | java -cp "$folder" "$class_name" > "$java_output_pipe" 2> "$error_file" &

                    #handle_output "$java_output_pipe" "$output_file" &

                    #retrieve_log_stream "$typing_output_pipe" | while IFS= read -r line; do printf "%s\n" "$line"; sleep .1; done  &


                    # Sleep one second
                    #sleep 1

                    # Run simulate_typing_2 in the background
                    #log_message "$typing_output_pipe" "$input_text"
                    #(simulate_typing_2 "$input_text" | while IFS= read -r line; do log_message "$typing_output_pipe" "$line" ; done) &

                    #simulate_typing_2 "$input_text" "$output_file" | while IFS= read -r line; do
                    #  log_message "$typing_output_pipe" "$line"
                    #  sleep .5
                    #done &
                    #(simulate_typing_2 "$input_text" "$output_file" > "$typing_output_pipe") &


                    #handle_output "$typing_output_pipe" "$output_file" &

                    # Run simulate_typing_2 in the background, sending its output to both the Java command and the named pipe
                    #(java -cp "$folder" "$class_name" 2> "$error_file" | \
                    #  while IFS= read -r line; do
                    #    log_message "$output_file" "$IFS"
                    #  done ) < <(simulate_typing_2 "$input_text" "$output_file" ) &

                    #(java -cp "$folder" "$class_name" 2> "$error_file" | while IFS= read -r line; do log_message "$line" "$output_file" ; done) < <(simulate_typing_2 "$input_text" "$output_file" )
                    simulate_typing_2 "$input_text" | \
                    tee >(java -cp "$folder" "$class_name" 2> "$error_file" | \
                    while IFS= read -r line; do log_message "$java_output_pipe" "$line"; done & ) | \
                    while IFS= read -r line; do sleep .5; wait; log_message "$typing_output_pipe" "$line";  done

                    # Wait for background processes to complete
                    wait
                    #sleep .1

                    # Call log show to get logs between start and end timestamps
                    #log show --info --debug --predicate "process == 'syslog'" --start "$start_timestamp" | awk -F "$output_file " '{print $2}' > "$output_file"
                    retrieve_log "$start_timestamp" "$output_file"
                    #cat "$output_file"

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