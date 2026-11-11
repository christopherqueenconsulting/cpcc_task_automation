#!/bin/bash -e
#
# Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
#

#############################################
# Write the Streamlit Community Cloud manifest from poetry.lock.
#
#   ./export_requirements.sh [OUTPUT]    # default OUTPUT: requirements.txt
#
# requirements.txt is NOT tracked on master: poetry.lock is the only dependency
# file there, so Dependabot updates one file. The deploy-release workflow runs
# this script and commits the result onto the `release` branch only, because
# Community Cloud installs requirements.txt (with uv) faster than pyproject.toml.
# CI runs it on every PR so a broken export fails before a deploy.
#
# Needs uv. Poetry and the export plugin are pinned so the output does not
# depend on the local Poetry install.
#############################################

out="${1:-requirements.txt}"

uv tool run --from poetry==2.5.1 --with poetry-plugin-export==1.10.1 \
  poetry export --without-hashes --format=requirements.txt > "$out"
# Community Cloud installs this manifest with uv; include the src-layout app itself.
printf '%s\n' '-e .' >> "$out"

# A dependency line looks like `name==1.2.3 ; markers`. Fail on an empty export.
grep -q '==' "$out" || { echo "export_requirements.sh: no pinned packages in $out" >&2; exit 1; }
