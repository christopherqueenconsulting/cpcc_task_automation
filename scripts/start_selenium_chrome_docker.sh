#!/bin/bash

#
#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/docker-compose.yml"
SERVICE_NAME="selenium-chrome"

if [ ! -f "$COMPOSE_FILE" ]; then
    echo "docker-compose.yml not found at: $COMPOSE_FILE" >&2
    exit 1
fi

echo "Starting Selenium Chrome via docker compose service '$SERVICE_NAME'..."
docker compose -f "$COMPOSE_FILE" up -d "$SERVICE_NAME"

echo "Selenium Chrome service is running."

# Add below line to docker command to enable view-only VNC access (optional)
# -e SE_VNC_VIEW_ONLY=true \