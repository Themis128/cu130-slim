---
name: playwright-ops
description: >-
  Drive browsers with Playwright: the MCP driver helpers plus writing, running, and debugging Playwright E2E tests against the real stack (social-frontend:8082 + social-api:8083). Use for E2E tests or scripted browser control.
---

# Playwright Ops

Consolidated skill — each section below was a standalone skill. Member scripts/templates live under `<source-skill>/` inside this directory.

| Section | Source |
|---|---|
| Playwright E2E tests for the Cloudless social stack | `playwright-ops` → `playwright-e2e/` |
| Playwright MCP driver | `playwright-ops` → `playwright-mcp-driver/` |

## Playwright E2E tests for the Cloudless social stack

This skill runs **real** Playwright E2E tests against the running Docker Compose
stack — no mocks, no stubs. The frontend (`social-frontend`) serves on
`http://localhost:8082` and the backend API (`social-api`) on
`http://localhost:8083`.

### Stack prerequisites

Before running any E2E test, verify the stack is up:

```bash
curl -s http://localhost:8083/health | grep -q '"ok"' && echo "API up"
curl -s -o /dev/null -w "%{http_code}" http://localhost:8082/   # expect 200
```

If either is down, start them:

```bash
docker compose up -d social-api social-frontend social-worker
```

### Test layout

```
social-automation/frontend/
├── playwright.config.ts        # baseURL=http://localhost:8082, no webServer block
├── tests/
│   ├── helpers/
│   │   └── auth.ts             # real-user auth fixture (register + login + localStorage)
│   ├── login.test.ts
│   ├── register.test.ts
│   ├── dashboard.test.ts
│   ├── forgot-password.test.ts
│   ├── reset-password.test.ts
│   ├── content-creation.test.ts
│   ├── calendar.test.ts
│   └── analytics.test.ts
```

### Running the suite

```bash
## All tests, chromium only (fastest)
cd social-automation/frontend
PATH=/home/tbaltzakis/.local/bin:$PATH ./node_modules/.bin/playwright test --project=chromium --reporter=line

## Single file
./node_modules/.bin/playwright test tests/login.test.ts --project=chromium

## Headed (watch the browser)
./node_modules/.bin/playwright test tests/login.test.ts --project=chromium --headed

## Generate HTML report
./node_modules/.bin/playwright test --project=chromium --reporter=html
```

If chromium isn't installed:

```bash
./node_modules/.bin/playwright install chromium
```

### Real auth fixture (`tests/helpers/auth.ts`)

Every authenticated test imports from `tests/helpers/auth.ts`. The fixture:

1. Registers a real test user via `POST /api/v1/auth/register` (idempotent —
   duplicate-email errors are ignored).
2. Logs in via `POST /api/v1/auth/login` (form-encoded) to get real JWTs.
3. Injects the tokens into `localStorage` on the frontend origin so dashboard
   pages render without the login UI.

```typescript
import { test, expect, TEST_USER } from './helpers/auth';

test('dashboard renders for a real user', async ({ authenticatedPage: page }) => {
  await page.goto('/dashboard');
  await expect(page.getByRole('heading', { name: /good (morning|afternoon|evening)/i })).toBeVisible();
});
```

Use the `authenticatedPage` fixture for any page under `(dashboard)/`. Use the
plain `page` fixture for `(auth)/` pages (login, register, forgot-password).

### Writing new tests — the rules

1. **No `page.route()` mocks.** Hit the real backend. If a test needs data,
   create it via the real API first (e.g. `POST /api/v1/content/posts`).
2. **Use accessible locators.** Prefer `getByRole`, `getByLabel`,
   `getByText` over CSS selectors. The accessibility tree is what users see.
3. **Clean up after yourself.** If a test creates a post or account, delete it
   via the API in `test.afterEach` so the suite is idempotent.
4. **Time-based assertions.** The dashboard greeting changes by hour — use
   `/good (morning|afternoon|evening)/i` not a fixed string.
5. **Auth redirects.** Unauthenticated visits to `/dashboard` redirect to
   `/login`. Test this by clearing localStorage first.

### Debugging a failing test

```bash
## Run with a visible browser and Playwright inspector
./node_modules/.bin/playwright test tests/login.test.ts --project=chromium --debug

## View trace of a failed run
./node_modules/.bin/playwright show-trace test-results/.../trace.zip

## Last screenshot on failure
ls social-automation/frontend/test-results/*/failure-*.png
```

### playwright-cli (interactive browser automation)

For exploratory testing or generating new tests from a live session, use
`playwright-cli` — it's more token-efficient than the MCP server for coding
agents.

```bash
## Open the real frontend
playwright-cli open http://localhost:8082/login

## Take an accessibility snapshot (returns element refs like e5, e10)
playwright-cli snapshot

## Interact using refs from the snapshot
playwright-cli fill e5 "user@example.com"
playwright-cli fill e7 "password123"
playwright-cli click e10

## Generate a test from the session
playwright-cli test --generate
```

See the bundled references for full command details:

- [Running and debugging tests](references/playwright-tests.md)
- [Request mocking](references/request-mocking.md) — use sparingly; prefer real API
- [Running Playwright code](references/running-code.md)
- [Browser session management](references/session-management.md)
- [Storage state (cookies, localStorage)](references/storage-state.md)
- [Test generation](references/test-generation.md)
- [Tracing](references/tracing.md)
- [Video recording](references/video-recording.md)
- [Inspecting element attributes](references/element-attributes.md)

### Common pitfalls

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ERR_CONNECTION_REFUSED` on goto | frontend container not running | `docker compose up -d social-frontend` |
| Redirect to `/login` on dashboard | tokens not in localStorage | use the `authenticatedPage` fixture |
| `401` on API calls | expired JWT | the fixture logs in fresh per worker |
| `Timeout on expect(toHaveURL)` | real login is slower than mock | use `{ timeout: 20000 }` on navigation asserts |
| Test passes locally but fails in CI | baseURL differs | set `E2E_FRONTEND_URL` and `E2E_API_URL` env vars |

### Test gate (per AGENTS.md)

After any frontend change that touches pages or components, run:

```bash
cd social-automation/frontend
./node_modules/.bin/tsc --noEmit --incremental false          # typecheck
./node_modules/.bin/playwright test --project=chromium --reporter=line   # E2E
```

Both must pass before committing.

## Playwright MCP driver

Drive the repo's dockerized Playwright MCP server (`mcr.microsoft.com/playwright/mcp`)
from a plain Python script over stdio JSON-RPC — for any browser automation that
benefits from the MCP tool surface (snapshot refs, `browser_run_code_unsafe`)
instead of hand-written eval strings against a sidecar's `/debug/eval`.

Use when: automating multi-step browser flows, when a sidecar `/debug/eval`
one-liner gets fragile, when the task needs the persistent MCP profile's
sessions, or when the user says "use the docker playwright mcp server".

### Files

- `scripts/mcp_client.py` — stdio JSON-RPC client. `PlaywrightMCP()` spawns the
  container with the exact `.devin/mcp_config.json` args (network host,
  `/workspace` mount, persistent profile at `.playwright-data/profile`).
  `mcp.tool(name, **args)` returns the joined text payload; raises on isError.

### Tool surface (25 tools)

Key ones: `browser_navigate`, `browser_snapshot` (aria-ref tree — use `ref` for
`browser_click`/`browser_type`), `browser_evaluate` (page-context JS),
`browser_run_code_unsafe` (full Playwright — `async (page) => {...}`),
`browser_type`, `browser_press_key`, `browser_take_screenshot`,
`browser_wait_for`, `browser_tabs`, `browser_file_upload`.

### Pitfalls (learned the hard way)

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

### Quick check

```bash
cd .devin/skills/playwright-ops/playwright-mcp-driver/scripts && python3 mcp_client.py
## → prints the 25 tool names
```
