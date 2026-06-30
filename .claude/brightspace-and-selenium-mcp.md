# Project note: Selenium MCP + BrightSpace automation

## Browser control via the Selenium MCP (Docker mode)

Claude can drive a real Selenium browser through the **`selenium`** MCP server
(configured in [`.mcp.json`](../.mcp.json)). It is wired to the project's **Docker
Selenium grid** via `SELENIUM_REMOTE_URL=http://localhost:14444/wd/hub`, so it uses
the **same container the project starts** (`selenium-chrome`).

Prereq each time: the container must be running.
```bash
docker compose -p cpcc_task_automation -f docker-compose.yml up -d selenium-chrome
# grid:  http://localhost:14444/wd/hub     watch (VNC): http://localhost:17900  (pw: secret)
```

To start the browser sharing the project's persistent login profile, call the MCP
**`start_browser`** tool with:
```json
{ "browser": "chrome", "options": { "arguments": [
  "user-data-dir=/home/seluser/chrome-profile",
  "--profile-directory=<INSTRUCTOR_USERID>"
] } }
```
`<INSTRUCTOR_USERID>` = value in `.env`. The profile is the volume-mounted
`./chrome-profile` (same one the product's `DOCKER_CHROME` uses), so login/MFA
persists. Run the MCP browser **or** the app — not both against this profile at once.

Tools: `navigate`, `find_element`, `click_element`, `send_keys`, `get_element_text`,
`get_element_attribute`, `hover`, `upload_file`, `take_screenshot`, `window`,
**`frame`** (TinyMCE iframe), **`execute_script`** (run the product's shadow-DOM JS
verbatim — same engine as the shipping Selenium code).

Full details: [`docs/SELENIUM_MCP.md`](../docs/SELENIUM_MCP.md).

## BrightSpace feature status (in progress)

One BrightSpace URL fetch yields submissions ZIP + instructions; auto-fills the
Rubric grading steps; MFA number is surfaced on the Streamlit page.

**Assignment-instructions extraction — FIXED (verified live 2026-06-29).** The
`_READ_EDITOR_INSTRUCTIONS_JS` editor read was always correct (returns the 9.3k-char
instructions on the live DOM). The real bug was *ordering* in
`fetch_assignment_instructions`: it scraped view-mode `<d2l-html-block>` content
**first**, but the submissions/marking page renders *student submission* text in
those same blocks — so it short-circuited with the wrong text and never opened the
editor. Fix: the **Edit Assignment editor is now the authoritative/primary source**;
view-mode scraping is only a fallback when no editor exists. Full context,
verified/unverified checklist, and the kickoff prompt:
[`docs/BRIGHTSPACE_AUTOMATION_HANDOFF.md`](../docs/BRIGHTSPACE_AUTOMATION_HANDOFF.md).

Selector-discovery helper that attaches to the real Selenium session:
`scripts/brightspace_selector_probe.py`. Fetch walkthrough:
`scripts/brightspace_fetch_walkthrough.py`.
