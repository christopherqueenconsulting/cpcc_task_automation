# CLAUDE.md

Project context for Claude Code sessions.

- Browser control + BrightSpace automation notes: @.claude/brightspace-and-selenium-mcp.md
- BrightSpace session handoff (status, kickoff prompt): @docs/BRIGHTSPACE_AUTOMATION_HANDOFF.md
- Selenium MCP setup/usage: @docs/SELENIUM_MCP.md

The `selenium` MCP server (`.mcp.json`) drives the project's Docker Selenium grid
(`SELENIUM_REMOTE_URL=http://localhost:14444/wd/hub`). Start the container first:
`docker compose -p cpcc_task_automation -f docker-compose.yml up -d selenium-chrome`.
