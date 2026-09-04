# CLAUDE.md

Project context for Claude Code sessions.

- Browser control + BrightSpace automation notes: @.claude/brightspace-and-selenium-mcp.md
- BrightSpace session handoff (status, kickoff prompt): @docs/BRIGHTSPACE_AUTOMATION_HANDOFF.md
- Selenium MCP setup/usage: @docs/SELENIUM_MCP.md

The `selenium` MCP server (`.mcp.json`) drives the project's Docker Selenium grid
(`SELENIUM_REMOTE_URL=http://localhost:14444/wd/hub`). Start the container first:
`docker compose -p cpcc_task_automation -f docker-compose.yml up -d selenium-chrome`.

## Cross-project context
Global rules for every session live in `~/.claude/CLAUDE.md` (sourced from the CQC Boss Vault, `00-Home/CLAUDE.global.md`). The vault is at `$CQC_VAULT` (fallback: `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/CQC Boss Vault`); read it as plain files.
- This project's vault note: `60-Projects/CPCC-Task-Automation.md` (create it per `00-Home/Vault-Conventions.md` if missing).
- Handoff packets: `80-Handoffs/HO-<date>-<n>-<slug>.md` per `80-Handoffs/Handoff-Protocol.md`.
- Tracker: none recorded.
- Other projects: look them up in `00-Home/Source-Map.md`; write anything another project needs to the vault, not to auto-memory.
- Decisions for Christopher: options with a recommendation, in chat (see `00-Home/Working-With-Christopher.md`).
