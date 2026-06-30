#!/usr/bin/env python
#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Print the Selenium MCP ``start_browser`` arguments (Docker profile preset).

Resolves ``INSTRUCTOR_USERID`` from the environment (or ``.env``) so you never have
to hand-edit the Chrome profile each session. Copy the printed JSON into the
``selenium`` MCP ``start_browser`` tool call.

    python3 scripts/selenium_mcp_start_args.py

Env overrides:
  MCP_CHROME_PROFILE_DIR   Chrome --user-data-dir (default: /home/seluser/chrome-profile,
                           the volume-mounted profile inside the Docker grid).
                           For LOCAL_CHROME mode use the host path
                           src/cqc_cpcc/utilities/selenium_profiles instead.
"""

import json
import os
import re
import sys
from pathlib import Path

DOCKER_PROFILE_DIR = "/home/seluser/chrome-profile"


def _resolve_userid() -> str | None:
    uid = os.getenv("INSTRUCTOR_USERID")
    if uid:
        return uid.strip()
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*INSTRUCTOR_USERID\s*=\s*(.+)", line)
            if m:
                return m.group(1).strip().strip('"').strip("'")
    return None


def main() -> int:
    uid = _resolve_userid()
    if not uid:
        print("ERROR: INSTRUCTOR_USERID not found in environment or .env", file=sys.stderr)
        return 1

    profile_dir = os.getenv("MCP_CHROME_PROFILE_DIR", DOCKER_PROFILE_DIR)
    start_args = {
        "browser": "chrome",
        "options": {
            "arguments": [
                f"user-data-dir={profile_dir}",
                f"--profile-directory={uid}",
            ]
        },
    }
    print(json.dumps(start_args, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
