# noVNC Manual Login Helper

Complete manual logins for platforms with anti-automation measures
(Twitter/X, Threads, Instagram) through the browser-novnc container's
noVNC viewer, then verify and persist the session.

## When to use

- Twitter/X login is blocked by the "knowledge check" anti-automation step
- Threads login form returns "Something went wrong" on programmatic submit
- Any platform detects Playwright automation and blocks the login
- A platform requires captcha, 2FA, or interactive consent that can't
  be handled programmatically

## Architecture

```
User (browser)                    browser-novnc container
     │                                    │
     ├─ open noVNC viewer ───────────────▶│ http://localhost:6080/vnc.html
     │                                    │
     ├─ complete login manually ─────────▶│ Chromium inside Xvfb
     │  (type credentials, solve          │
     │   captcha, handle 2FA)              │
     │                                    │
     ├─ wait-for-login.py ───────────────▶│ polls /session/evaluate
     │  (polls until logged in)           │ for login indicators
     │                                    │
     └─ verify-session.py ──────────────▶│ confirms session active
                                        │ extracts cookies
```

The browser-novnc container runs Chromium inside Xvfb with a noVNC web
viewer on port 6080. The browser bridge API on port 9223 provides
programmatic access to the same browser for verification and cookie
extraction after the manual login.

## noVNC viewer URL

```
http://localhost:6080/vnc.html?autoconnect=1&resize=scale
```

Or via the bridge:

```
GET http://localhost:9223/novnc-url
```

## Login URLs per platform

| Platform | Login URL | Success indicator |
|----------|-----------|-------------------|
| Twitter/X | `https://x.com/i/flow/login` | `x.com/home` in URL, no `login` link |
| Threads | `https://www.threads.com/login/` | `threads.com/` in URL, "Messages" in body |
| TikTok | `https://www.tiktok.com/login` | `tiktok.com/foryou` in URL |
| Instagram | `https://www.instagram.com/accounts/login/` | `instagram.com/` without `login` in URL |

## Workflow

### Step 1: Start a browser session and navigate to the login page

```bash
python3 scripts/start-login.py <platform>
```

This starts a browser session in the browser-novnc container and
navigates to the platform's login page. It prints the noVNC URL for
the user to open.

### Step 2: Complete the login manually via noVNC

Open the noVNC URL in your browser and complete the login:
- Type your email/username and password
- Solve any captcha or knowledge check
- Handle 2FA if prompted
- Wait for the page to load the authenticated home feed

### Step 3: Wait for login to complete (optional — automated polling)

```bash
python3 scripts/wait-for-login.py <platform> [timeout_seconds]
```

Polls the browser bridge every 5 seconds and checks if the session is
active. Returns exit code 0 when logged in, 1 on timeout.

Default timeout: 300 seconds (5 minutes).

### Step 4: Verify the session and extract cookies

```bash
python3 scripts/verify-session.py <platform>
```

Confirms the session is active and saves cookies to the browser-novnc
container's cookie store for persistence across restarts.

### Full automated flow

```bash
# Start login and get noVNC URL
python3 scripts/start-login.py twitter

# User completes login in noVNC (manual)

# Poll until logged in (auto-detects)
python3 scripts/wait-for-login.py twitter 600

# Verify and persist
python3 scripts/verify-session.py twitter
```

## Scripts

- `scripts/start-login.py` — Start browser session and navigate to login page
- `scripts/wait-for-login.py` — Poll until login is detected
- `scripts/verify-session.py` — Verify session and save cookies
- `scripts/check-session.py` — Check if session is currently active (alias of playwright-mcp-login's check)

## Browser bridge API

The browser-novnc container exposes a REST API on port 9223:

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Bridge health check |
| GET | `/novnc-url` | Get noVNC viewer URL |
| POST | `/session/start` | Start a browser session (requires `platform`) |
| POST | `/session/stop` | Stop the current session |
| POST | `/session/navigate` | Navigate to a URL |
| POST | `/session/evaluate` | Evaluate JavaScript in the page |
| GET | `/session/cookies` | Get stored cookies for the platform |
| POST | `/session/cookies` | Inject cookies into the browser |
| POST | `/session/extract` | Extract cookies from the browser |
| GET | `/session/status` | Get session status |

## Related skills

- `playwright-mcp-login` — Login via Playwright MCP (for platforms without anti-automation)
- `browser-bridge-ops` — Browser bridge operations
- `social-session-bootstrap` — Master session verification
