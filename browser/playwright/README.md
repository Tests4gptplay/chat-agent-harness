# Playwright browser backend

The current CAH browser is Chrome started by `host/start_playwright_host.ps1`
with a dedicated persistent CAH profile and loopback CDP. Authentication belongs
to the user; Python Host, official MCP and official CLI attach to the same browser.
Do not launch a second browser with the same profile or copy cookies into Git.

## Current providers

- `playwright_host`: deterministic response-end observation, role/wake bindings,
  Worker pools/rollover, task-owned cleanup and Helper `browser.recover_input`.
- Official `@playwright/mcp`: general browser tools through the existing CAH
  role tool transport. Discover schemas on demand with `browser.mcp.list`.
- Official `@playwright/cli`: commands through the existing Runner, with named
  sessions and CDP attach/detach through `host/playwright_cli.ps1`.

Packages are pinned, not forked. Dependencies/configuration are outside source in
`__CAH_TOOLS_ROOT__`. Disposable CLI work is under
`__CAH_WORK_ROOT__\browser-cli`. Detach and retain wanted products elsewhere before
removing a finished session's work directory. Login state is not stored there.

## Canonical instructions

- [AI-led installation and Project binding](../../installation/README.md)
- [Role tools, results and CLI examples](../../docs/TOOL_SYSTEM.md)

The retired browser extension and generic normal-profile debug-toggle instructions
are not deployment prerequisites. The native launcher owns browser startup;
MCP and CLI do not replace CAH roles or machine control lifecycle.

## Evidence boundary

Provider fixture checks prove shared CDP operations, not authenticated ChatGPT
readiness. Verify real account access and the required role flow separately before
claiming a new machine/session is ready. No model API substitutes for webpage AI.
