# Playwright MCP Login — Social Platform Session Bootstrap

Log into social platforms (Twitter/X, TikTok, Threads, Instagram) through the
**Playwright MCP server** (not the browser-novnc bridge), extract the session
cookies, and inject them into the **browser-novnc container** (port 9223) so the
SocialAuto polling tasks can read/send DMs 24/7.

## When to use

- The Twitter/X DM bot needs a logged-in `x.com` session but the free API tier
  blocks DM access (403).
- The TikTok DM bot needs a logged-in `tiktok.com` session because the Business
  Messaging API is not available in the EU.
- The Threads DM bot needs a logged-in `threads.com` session because the
  Threads API has no DM endpoints.
- Any platform where the browser-novnc session has expired and the user wants
  to re-login programmatically via the Playwright MCP server instead of
  manually via noVNC.

## Architecture

```
┌─────────────────┐       ┌──────────────────┐       ┌─────────────────┐
│  Playwright MCP │       │  browser-novnc   │       │  SocialAuto     │
│  (Docker)       │       │  container       │       │  polling tasks  │
│                 │       │  port 9223       │       │                 │
│  1. Login to    │       │                  │       │  6. poll_*_dm   │
│     platform    │       │  4. Inject       │       │     tasks use   │
│  2. Extract     │──────▶│     cookies      │──────▶│     bridge      │
│     cookies     │       │  5. Navigate to  │       │     session     │
│  3. Export to   │       │     platform     │       │     to read/    │
│     JSON        │       │                  │       │     send DMs    │
└─────────────────┘       └──────────────────┘       └─────────────────┘
```

The Playwright MCP server runs its own Chromium browser with a persistent
profile at `~/.playwright-data/profile`. The browser-novnc container runs a
separate Playwright browser accessible via noVNC at port 6080 and a bridge API
at port 9223.

This skill bridges the two: log in via Playwright MCP (which can be automated
headlessly), extract cookies, and inject them into the browser-novnc container
so the SocialAuto polling tasks have a valid session.

## Playwright MCP server

The Playwright MCP server is configured in `.devin/mcp_config.json`:

```json
{
  "command": "docker",
  "args": [
    "run", "-i", "--init", "--network", "host", "--shm-size=2g",
    "-v", "/home/tbaltzakis/cu130-slim:/workspace",
    "-v", "/home/tbaltzakis/cu130-slim/.playwright-data:/home/pwuser/.playwright-data",
    "-w", "/workspace",
    "mcr.microsoft.com/playwright/mcp:latest",
    "--config", "/workspace/.playwright-data/pw-mcp-config.json",
    "--user-data-dir", "/home/pwuser/.playwright-data/profile",
    "--ignore-https-errors",
    "--timeout-action", "15000",
    "--timeout-navigation", "120000"
  ]
}
```

The MCP server exposes these tools (used by the agent):
- `browser_navigate` — Go to a URL
- `browser_snapshot` — Get accessibility tree of the page
- `browser_click` — Click an element by ref
- `browser_fill_form` — Fill form fields by ref
- `browser_evaluate` — Run JavaScript on the page
- `browser_press_key` — Press a keyboard key
- `browser_find` — Search the page snapshot for text
- `browser_console_messages` — Get console output

## Credential sources

Credentials are stored in the SocialAuto secret store at `/api/v1/secrets`.
The agent retrieves them at login time — they are never written to disk or
committed to git.

| Platform | Secret keys | Email |
|----------|------------|-------|
| Twitter/X | (user-provided) | `baltzakis.themis@gmail.com` |
| TikTok | (user-provided) | `baltzakis.themis@gmail.com` |
| Threads | `INSTAGRAM_USERNAME`, `INSTAGRAM_PASSWORD` | Instagram username |
| Instagram | `INSTAGRAM_USERNAME`, `INSTAGRAM_PASSWORD` | Instagram username |

## Login flows

### Twitter/X (`x.com/i/flow/login`)

X uses a React SPA with a multi-step login flow:

1. Navigate to `https://x.com/i/flow/login`
2. Dismiss cookie dialog (overlay intercepts clicks — use `browser_evaluate`)
3. Fill email field via `browser_fill_form` (ref from `browser_snapshot`)
4. Fill password field — X's React state requires the **native input value
   setter** + `input` event, not just `element.value =`:
   ```js
   const setter = Object.getOwnPropertyDescriptor(
     window.HTMLInputElement.prototype, 'value'
   ).set;
   setter.call(passInput, password);
   passInput.dispatchEvent(new Event('input', { bubbles: true }));
   ```
5. Click the **Continue** button (ref from snapshot — it becomes a `button`
   once both fields are filled)
6. Handle possible username confirmation step (if email maps to multiple
   accounts)
7. Wait for redirect to `x.com/home` — login successful

### TikTok (`tiktok.com/login`)

TikTok uses a standard form with email/password:

1. Navigate to `https://www.tiktok.com/login/phone-or-email/email`
2. Switch to email login if phone is default
3. Fill email and password fields
4. Click Login
5. Handle possible captcha (pause for manual completion via noVNC if needed)
6. Wait for redirect to `tiktok.com/foryou` — login successful

### Threads (`threads.com/login`)

Threads uses Instagram identity:

1. Navigate to `https://www.threads.com/login`
2. Fill Instagram username and password
3. Click Log in
4. Handle possible 2FA (pause for manual completion if needed)
5. Wait for redirect to `threads.com/` — login successful

## Cookie extraction

After a successful login, extract cookies via `browser_evaluate`:

```js
() => {
  return document.cookie;
}
```

For HTTP-only cookies (which `document.cookie` can't access), use the
Playwright MCP `browser_evaluate` with the `document.cookie` approach for
visible cookies, then use the browser-novnc bridge `/session/cookies` endpoint
which has access to all cookies through Playwright's context.

## Cookie injection into browser-novnc

The browser-novnc bridge (port 9223) has a `/session/navigate` endpoint. After
extracting cookies from the Playwright MCP session:

1. Navigate the browser-novnc browser to the platform URL
2. Use the bridge `/session/evaluate` endpoint to set cookies:
   ```js
   document.cookie = "name=value; domain=.x.com; path=/; secure; max-age=86400";
   ```
3. For HTTP-only cookies, use the bridge's Playwright context directly via
   the `/session/cookies` endpoint (if available) or navigate and let the
   browser pick up session cookies from the domain.

### Alternative: Shared profile

The Playwright MCP profile at `~/.playwright-data/profile` persists across
sessions. If the browser-novnc container can mount the same profile, sessions
are shared automatically. However, the browser-novnc container uses its own
profile, so cookie injection is the primary method.

## Platform-specific notes

| Platform | 2FA | Captcha | Session duration |
|----------|-----|---------|-----------------|
| Twitter/X | Possible | Possible | ~1 year |
| TikTok | Possible | Common | ~30 days |
| Threads | Possible | Rare | ~90 days |
| Instagram | Possible | Rare | ~90 days |

If 2FA or captcha appears, the agent should pause and ask the user to complete
it manually via the noVNC viewer at `http://localhost:6080/vnc.html`.

## Scripts

- `scripts/extract-cookies.sh` — Extract cookies from the Playwright MCP browser
  via `browser_evaluate` and save to a JSON file
- `scripts/inject-cookies.sh` — Inject cookies into the browser-novnc container
  by navigating to the platform and setting cookies via the bridge API
- `scripts/check-session.sh` — Check if a platform session is active in the
  browser-novnc container by navigating and checking login state

## Related skills

- `browser-bridge-ops` — Operate the browser-novnc bridge (port 9223)
- `threads-ops` — Threads account management and OAuth
- `twitter-oauth-setup` — Twitter/X OAuth 2.0 configuration
- `tiktok-publish` — TikTok content publishing
- `instagram-account-config` — Instagram account setup
- `whatsapp-platform` — WhatsApp phone verification
