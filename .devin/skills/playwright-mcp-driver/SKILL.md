# Playwright MCP driver

Drive the repo's dockerized Playwright MCP server (`mcr.microsoft.com/playwright/mcp`)
from a plain Python script over stdio JSON-RPC — for any browser automation that
benefits from the MCP tool surface (snapshot refs, `browser_run_code_unsafe`)
instead of hand-written eval strings against a sidecar's `/debug/eval`.

Use when: automating multi-step browser flows, when a sidecar `/debug/eval`
one-liner gets fragile, when the task needs the persistent MCP profile's
sessions, or when the user says "use the docker playwright mcp server".

## Files

- `scripts/mcp_client.py` — stdio JSON-RPC client. `PlaywrightMCP()` spawns the
  container with the exact `.devin/mcp_config.json` args (network host,
  `/workspace` mount, persistent profile at `.playwright-data/profile`).
  `mcp.tool(name, **args)` returns the joined text payload; raises on isError.

## Tool surface (25 tools)

Key ones: `browser_navigate`, `browser_snapshot` (aria-ref tree — use `ref` for
`browser_click`/`browser_type`), `browser_evaluate` (page-context JS),
`browser_run_code_unsafe` (full Playwright — `async (page) => {...}`),
`browser_type`, `browser_press_key`, `browser_take_screenshot`,
`browser_wait_for`, `browser_tabs`, `browser_file_upload`.

## Pitfalls (learned the hard way)

- **`browser_run_code_unsafe` runs in Node, not the page.** `document`, `window`,
  `fetch` are undefined — DOM access must go through `page.evaluate(() => ...)`
  inside it. It takes `async (page) => ...`, no `require`.
- **Every `PlaywrightMCP()` is a fresh container + `about:blank`.** Cookies in the
  persistent profile survive; open tabs do not. Do navigate→inject→act in ONE
  session.
- **Snapshots are written to `.playwright-mcp/*.yml`** under the workspace —
  ignore them in commits.
- **First call is slow** (~10-15s container+browser boot). Set generous timeouts
  on `browser_navigate` (server already defaults `--timeout-navigation 120000`).
- **Cookie injection for httpOnly cookies**: `document.cookie = "k=v; ..."` CAN
  write httpOnly *values* (the flag only blocks reads) — see session-transplant
  skill for the FB cookie whitelist (`c_user`, `xs`, `datr`, `fr`, `sb`, `wd`,
  `dpr`, `presence`).

## Quick check

```bash
cd .devin/skills/playwright-mcp-driver/scripts && python3 mcp_client.py
# → prints the 25 tool names
```
