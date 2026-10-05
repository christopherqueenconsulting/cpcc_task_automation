#!/bin/bash

#
# Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
#

# Ends every browser this project may have left running:
#   1. local Chrome processes (LOCAL_CHROME), and
#   2. sessions on the Docker Selenium grid (DOCKER_CHROME). The grid has one slot,
#      so a leftover session makes the next run fail with
#      "Selenium container did not become ready".

GRID_URL="${SELENIUM_GRID_URL:-http://localhost:14444}"

echo "Killing all Selenium drivers started by the current user..."
ps u | grep 'Chrome.app' | grep -v grep | awk '{print $2}' | xargs kill -9 2>/dev/null
echo "All Selenium drivers started by the current user killed."

echo "Ending sessions on the Selenium grid at ${GRID_URL}..."
sessions=$(curl -s --max-time 5 "${GRID_URL}/status" | python3 -c '
import json, sys
try:
    nodes = json.load(sys.stdin)["value"].get("nodes", [])
except Exception:
    sys.exit(0)
for node in nodes:
    for slot in node.get("slots", []):
        if slot.get("session"):
            print(slot["session"]["sessionId"])
')
if [ -z "$sessions" ]; then
  echo "No grid sessions found (or the grid is not running)."
else
  for session_id in $sessions; do
    curl -s --max-time 30 -X DELETE "${GRID_URL}/wd/hub/session/${session_id}" > /dev/null \
      && echo "Ended grid session ${session_id}."
  done
fi
