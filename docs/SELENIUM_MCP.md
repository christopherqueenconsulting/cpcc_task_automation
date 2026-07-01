# Selenium MCP — Claude-driven browser for this project

Lets Claude operate a real **Selenium WebDriver** browser (same engine the product
code uses), so it can develop/verify browser-control features end-to-end —
including shadow-DOM piercing (`execute_script`) and iframe switching (`frame`),
which BrightSpace/D2L requires.

Server: [`@angiejones/mcp-selenium`](https://www.npmjs.com/package/@angiejones/mcp-selenium)
(pinned to `0.2.3`). Configured in [`.mcp.json`](../.mcp.json) (project scope).

## Enable it

`.mcp.json` is project-scoped, so Claude Code will prompt you to approve/enable it.

- **VS Code / Claude Code:** reload the window (or restart Claude Code). When asked
  to trust the project MCP server, approve it. Verify with `/mcp` — you should see
  `selenium` listed and `connected`.
- **Manual / CLI (if it doesn't auto-load):**
  ```bash
  # project scope (writes to .mcp.json — already created):
  claude mcp add selenium -s project -- npx -y @angiejones/mcp-selenium@0.2.3
  # or just-for-you (local scope):
  claude mcp add selenium -- npx -y @angiejones/mcp-selenium@0.2.3
  # list / check status:
  claude mcp list
  ```

First run downloads the package via `npx`; it also needs Chrome + a matching
chromedriver on PATH (the project already uses chromedriver locally).

## Operate the SAME browser/session the code uses

The server launches a **local** Chrome (it doesn't attach to the Docker grid). To
share the product's login/MFA session, start it with the project's local profile —
the exact `user-data-dir` + `profile-directory` the code uses for `LOCAL_CHROME`:

Ask Claude to call the **`start_browser`** tool with:

```json
{
  "browser": "chrome",
  "options": {
    "arguments": [
      "user-data-dir=/Users/christopherqueen/workspace/CPCC_Task_Automation/src/cqc_cpcc/utilities/selenium_profiles",
      "--profile-directory=<INSTRUCTOR_USERID>"
    ]
  }
}
```

Replace `<INSTRUCTOR_USERID>` with the value from your `.env`. Log in / approve MFA
once in that window; the session persists in the profile for later runs.

**Caveat — one writer at a time:** Chrome locks a `user-data-dir`. Don't run the
app's `LOCAL_CHROME` browser and the MCP browser against this profile
simultaneously. For development, drive the browser via the MCP; when you run the
app for an end-to-end check, close the MCP browser first (or use a separate
profile / `DOCKER_CHROME`). To force a fresh login, clear the profile:
`poetry run python -c "from cqc_cpcc.utilities.selenium_util import clear_persisted_browser_profile as c; print(c())"`.

> Want to watch the Docker browser instead? That path can't be MCP-driven by this
> server, but you can watch the product's `DOCKER_CHROME` run live over VNC at
> http://localhost:17900 (password `secret`), and discover selectors with
> `scripts/brightspace_selector_probe.py`, which attaches to the real Selenium
> session.

## Useful tools this server exposes
`start_browser`, `navigate`, `find_element`, `click_element`, `send_keys`,
`get_element_text`, `get_element_attribute`, `hover`, `upload_file`,
`take_screenshot`, **`execute_script`** (run the product's shadow-DOM JS verbatim),
**`frame`** (switch into the TinyMCE iframe), `window`.

Because the product reads shadow/iframe content via `execute_script`, you can paste
the exact JS from `src/cqc_cpcc/utilities/brightspace_fetch.py`
(e.g. `_READ_EDITOR_INSTRUCTIONS_JS`) into the `execute_script` tool and get the
same result the shipping code would — then lock the fix into that file.
