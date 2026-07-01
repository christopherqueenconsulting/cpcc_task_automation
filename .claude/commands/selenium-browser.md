---
description: Start the Selenium MCP browser on the project's Docker grid with the shared login profile
allowed-tools: Bash(grep:*), Bash(cut:*), Bash(head:*), Bash(tr:*), Bash(curl:*), Bash(docker:*), Bash(python3:*), mcp__selenium__start_browser, mcp__selenium__navigate
argument-hint: "[optional BrightSpace URL to open after launch]"
---

Start a Chrome session via the `selenium` MCP server against the project's **Docker
Selenium grid**, reusing the persisted login profile so I don't have to log in again.

Resolved now:
- INSTRUCTOR_USERID: !`grep -E '^INSTRUCTOR_USERID=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'"`
- Docker grid (http://localhost:14444/status): !`curl -s http://localhost:14444/status >/dev/null 2>&1 && echo UP || echo DOWN`

Do this:
1. If the grid is **DOWN**, start it and wait until ready:
   `docker compose -p cpcc_task_automation -f docker-compose.yml up -d selenium-chrome`
   then poll `curl -s http://localhost:14444/status` until `"ready":true`.
2. Call the **`start_browser`** tool with:
   - `browser`: `"chrome"`
   - `options.arguments`: `["user-data-dir=/home/seluser/chrome-profile", "--profile-directory=<INSTRUCTOR_USERID resolved above>"]`
   (If INSTRUCTOR_USERID came back empty, stop and tell me to set it in `.env`.)
3. If `$ARGUMENTS` is non-empty, `navigate` the browser to that URL; otherwise stop
   and ask me which BrightSpace page to open.

Note: don't run the app's Docker browser and this MCP browser against the profile at
the same time (Chrome locks `user-data-dir`). If `start_browser` fails with a profile
lock, tell me to close the other session.
