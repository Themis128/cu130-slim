---
name: browser-ops
description: >-
  All browser automation infrastructure: the shared browser-novnc bridge (9223), per-platform sidecars (LinkedIn 9225, Facebook, TikTok, Messenger), daemon mode, patchright stealth config, and browser-path publishing (X/Twitter fallback). Use for sidecar, bridge, stealth, or browser-publish work.
---

# Browser Ops

Consolidated skill — each section below was a standalone skill. Member scripts/templates live under `<source-skill>/` inside this directory.

| Section | Source |
|---|---|
| Browser Bridge Operations | `browser-ops` → `browser-bridge-ops/` |
| Browser Daemon Mode (Warm Session + Keepalive) | `browser-ops` |
| Patchright Operations | `browser-ops` → `patchright-ops/` |
| LinkedIn Sidecar Operations | `browser-ops` |
| Twitter/X browser operations | `browser-ops` |

## Browser Bridge Operations

Automate the embedded browser (browser-novnc container, port 9223) to
interact with social platforms that lack write APIs or where API access
is insufficient for profile management.

### When to use

- Update Threads bio/name/profile picture (no write API for profile fields)
- Upload profile pictures to platforms without a picture-update API
- Click through UI flows that require a real browser session
- Extract cookies from an active login session
- Evaluate JavaScript on a page to scrape data APIs don't return
- Fill and submit forms in the embedded browser

### Architecture

```
Agent → browser-bridge (port 9223) → Playwright browser → Social platform
                                         ↓
                                    noVNC viewer (port 6080)
                                    for manual interaction
```

The `browser-novnc` container runs a Python (FastAPI) bridge on port 9223
that controls a Playwright Chromium browser. The browser is also visible
through noVNC at `http://localhost:6080/vnc.html` for manual interaction.

### API base

```
http://localhost:9223
```

No authentication required — the bridge is internal to the compose network.

### Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Check bridge status and supported platforms |
| GET | `/platforms` | List all supported platforms and their login URLs |
| POST | `/session/start` | Start a browser session for a platform |
| GET | `/session/status` | Check current session status |
| GET | `/session/page-info` | Get current page URL and title |
| GET | `/session/cookies` | Extract cookies from active session |
| POST | `/session/stop` | Stop the current session |
| POST | `/session/navigate` | Navigate to a URL |
| POST | `/session/evaluate` | Evaluate JavaScript expression (`frame_url` substring targets an iframe, e.g. reCAPTCHA) |
| GET | `/session/screenshot` | PNG screenshot of the current viewport |
| POST | `/session/click` | Click an element by selector (picks first *visible* match) |
| POST | `/session/fill` | Fill an input/textarea by selector (visible match; falls back to real keystrokes) |
| POST | `/session/upload` | Upload a file to an input[type=file] |
| POST | `/session/login` | Start a login flow for a platform |
| POST | `/session/extract` | Extract cookies immediately |
| POST | `/session/click` | Click element by selector |
| POST | `/session/mouse-click` | Click at specific coordinates |

### Key request formats

#### Start session
```json
POST /session/start
{"platform": "threads"}
```

#### Navigate
```json
POST /session/navigate
{"url": "https://www.threads.com/@cloudless_gr"}
```

#### Evaluate JS
```json
POST /session/evaluate
{"expression": "document.title"}
```

#### Click element
```json
POST /session/click
{"selector": "div[role=button]"}
```

#### Fill input
```json
POST /session/fill
{"selector": "textarea", "value": "New bio text"}
```

#### Upload file
```json
POST /session/upload
{"selector": "input[type=file]", "file_path": "/tmp/logo.png"}
```

With click-to-trigger file chooser:
```json
POST /session/upload
{"selector": "input[type=file]", "file_path": "/tmp/logo.png", "click_selector": "[role=dialog] img"}
```

### Supported platforms

| Platform | Login URL |
|----------|-----------|
| instagram | https://www.instagram.com/accounts/login/ |
| facebook | https://www.facebook.com/login |
| linkedin | https://www.linkedin.com/login |
| tiktok | https://www.tiktok.com/login |
| twitter | https://x.com/i/flow/login |
| threads | https://www.threads.net/login |
| reddit | https://www.reddit.com/login |
| youtube | https://accounts.google.com/v3/signin/identifier?continue=https://www.youtube.com |
| pinterest | https://www.pinterest.com/login/ |
| tumblr | https://www.tumblr.com/login |
| medium | https://medium.com/m/signin |
| skool | https://www.skool.com/login (persistent session — used by the VEC classroom watcher, no cookie extraction) |
| discord | https://discord.com/login |
| telegram | https://web.telegram.org/a/ |
| whatsapp | https://web.whatsapp.com/ |

### Sidecar browsers (separate containers)

| Container | Port | Platform |
|-----------|------|----------|
| browser-novnc | 9223 | All (via bridge) |
| tiktok-browser-sidecar | 9224 | TikTok |
| linkedin-browser-sidecar | 9225 | LinkedIn |
| facebook-browser-sidecar | 9226 | Facebook |

### Tool scripts

Run from repo root `cu130-slim/`:

```bash
## Check browser bridge health and current session
.devin/skills/browser-ops/browser-bridge-ops/scripts/bridge-status.py

## Navigate to a URL in the browser
.devin/skills/browser-ops/browser-bridge-ops/scripts/bridge-navigate.py "https://www.threads.com/@cloudless_gr"

## Evaluate JavaScript and print result
.devin/skills/browser-ops/browser-bridge-ops/scripts/bridge-eval.py "document.title"

## Get current page info (URL + title)
.devin/skills/browser-ops/browser-bridge-ops/scripts/bridge-page-info.py

## Start a session for a platform
.devin/skills/browser-ops/browser-bridge-ops/scripts/bridge-start-session.py threads

## Upload a file to the browser
.devin/skills/browser-ops/browser-bridge-ops/scripts/bridge-upload.py /tmp/logo.png "input[type=file]"

## Click an element
.devin/skills/browser-ops/browser-bridge-ops/scripts/bridge-click.py "div[role=button]"

## Fill a form field
.devin/skills/browser-ops/browser-bridge-ops/scripts/bridge-fill.py "textarea" "New bio text"

## Take a screenshot (saves to /tmp/browser-screenshot.png)
.devin/skills/browser-ops/browser-bridge-ops/scripts/bridge-screenshot.py
```

### Common patterns

#### Edit a Threads profile

```bash
## 1. Navigate to profile
bridge-navigate.py "https://www.threads.com/@cloudless_gr"

## 2. Click Edit profile button
bridge-eval.py "(() => { const btns = document.querySelectorAll('div[role=button]'); for (const btn of btns) { if (btn.textContent.trim() === 'Edit profile') { btn.click(); return 'clicked'; } } return 'not found';; })()"

## 3. Click Bio section
bridge-eval.py "(() => { const dialog = document.querySelector('[role=dialog]'); const all = dialog.querySelectorAll('div[role=button]'); for (const el of all) { if (el.textContent.trim().startsWith('Bio')) { el.click(); return 'clicked'; } } return 'not found';; })()"

## 4. Fill bio textarea
bridge-fill.py "textarea" "Clear skies. Zero friction."

## 5. Click Done to save
bridge-eval.py "(function() { const all = Array.from(document.querySelectorAll('div[role=button], button')); const done = all.filter(b => b.innerText.trim() === 'Done'); if (done.length > 0) { done[done.length - 1].click(); return 'clicked'; } return 'no Done'; })()"
```

#### Upload a profile picture

```bash
## Copy image to browser container first
docker compose cp /tmp/logo.png browser-novnc:/tmp/logo.png

## Upload using click_selector to trigger file chooser
bridge-upload.py /tmp/logo.png "input[type=file]" "[role=dialog] img"
```

### Important notes

- The browser bridge is unauthenticated and internal only.
- File uploads require the file to exist inside the browser-novnc container.
  Use `docker compose cp` to copy files from host to container first.
- Threads profile photos are synced from Instagram — changing the Threads
  profile picture requires changing the linked Instagram profile picture.
- The evaluate endpoint uses `expression` (not `script`) as the JSON key.
- Some platforms (LinkedIn, Facebook, TikTok) have dedicated sidecar
  containers with their own ports (9225, 9226, 9224).
- **Busy-hold + platform attribution (anti-hijack)**: callers identify
  themselves with an `X-Platform` request header
  (`BrowserBridgeClient(url, platform="twitter")` sends it on every
  request). Tagged live-page interactions set a 180s hold owned by that
  platform (`busy_owner`/`busy_until`); while held, any request tagged
  for a *different* platform — or untagged — gets `409 Browser busy`
  at `_ensure_live_page` (covers navigate/evaluate/fill/click/
  mouse-click/upload/login/profile/extract — and `/session/stop`, so a
  foreign platform cannot tear down a held session).
  The owner can re-enter freely, and every owner interaction refreshes
  the hold. `/session/start` for another platform
  also returns 409 while held; a same-platform start reuses the session.
  `{"platform": "...", "force": true}` overrides for manual recovery.
  Untagged calls are safe when no tagged hold is active (legacy
  compatibility) — but all known call sites in backend workers/APIs are
  tagged. For an uninterrupted manual session (e.g. noVNC login), pause
  pollers: `docker compose pause celery-beat` … `unpause` afterwards.
- **Waiting-session timeout**: a session stuck in `waiting` (login page
  open, nobody authenticating) blocks foreign `session/start` for
  `WAITING_TIMEOUT` (300s), then becomes preemptible. `extracting` stays
  a hard block (short-lived cookie extraction). Without this a stale
  poller login page starved publishers for the full 10-min detect loop.
- **Interactive sessions**: `POST /session/start` with `"interactive": true`
  grants `INTERACTIVE_WAITING_TIMEOUT` (3600s) preemption protection, a
  60-min login-detection window (instead of the default 600s), and a
  15-min busy-hold per interaction (instead of 180s) — pauses during
  troubleshooting won't let pollers steal the browser. If the operator
  navigates off the platform's domain the detect loop switches to
  **manual-drive mode** — status `active`, browser stays alive until
  `/session/stop` or a new `/session/start`. Use this for arbitrary-site
  driving (e.g. Slack console) and human noVNC logins.
- **Watchdog tuning via env** (browser-novnc container):
  `BRIDGE_BUSY_HOLD_SECONDS` (180), `BRIDGE_WAITING_TIMEOUT` (300),
  `BRIDGE_INTERACTIVE_TIMEOUT` (3600), `BRIDGE_LOGIN_DETECT_SECONDS` (600),
  `BRIDGE_INTERACTIVE_BUSY_HOLD` (900).
- **Start is synchronous-ready**: `session/start` waits (≤20s) for the
  page to exist before returning — callers can evaluate/navigate
  immediately. It also takes an immediate busy-hold when the caller's
  `X-Platform` matches the session platform, closing the gap where a
  poller could tear down a brand-new session between start and first
  interaction.
- **Contention retries**: `client.start_session(platform,
  contention_retries=14)` retries 409s every 20s, escalating to
  `force` on the last 3 tries — publishers outlast poller churn instead
  of burning queue attempts.
- **Visible-element picking**: `click`/`fill`/`upload` pick the first
  *visible* match (RN-web dialogs on Threads/IG render hidden duplicate
  nodes — `.first` can hit a dead copy) and poll ~10s for async mounts.
  `fill` falls back to `press_sequentially` (real keystrokes) when the
  element rejects programmatic fill.
- **Observability**: `GET /session/screenshot` returns a live PNG —
  use it instead of guessing DOM state. `evaluate` accepts `frame_url`
  to run inside a matching iframe (reCAPTCHA lives in frames).
- **Crash cleanup**: if the context dies mid-extraction, dead
  browser/context/page refs are cleared so the next `/session/start`
  launches cleanly instead of wedging on a corpse.
- **Launch collision**: session start cancels the previous
  `_run_browser` task and closes the old context before relaunching;
  `launch_persistent_context` retries once after `pkill -f chromium`
  when an orphan holds `/app/browser-profile` ("Opening in existing
  browser session").
- **Regression check**: `python3 scripts/browser_contention_check.py`
  verifies the busy-hold end-to-end (owner calls pass, foreign/untagged
  get 409, foreign start 409, same-platform start reuses). Never sends
  `force`. Run after any change to `_ensure_live_page` or `_state`.
- For X/Twitter specifically (two-step login, composer quirks), see the
  `browser-ops` skill.
- Wait 2-3 seconds between navigation and interaction for pages to load.

## Browser Daemon Mode (Warm Session + Keepalive)

Keep the browser-novnc session warm so subsequent social media sends are
fast (~2s vs ~15s cold start). Pre-loads the platform SPA and runs a
background keepalive loop that navigates every 5 minutes to prevent idle
timeouts.

### When to use

- Speed up personal Messenger sends (warm session = ~2s vs ~15s cold)
- Prevent browser session idle timeouts during long polling intervals
- Pre-load Instagram, Facebook, or LinkedIn before bulk operations
- Debug slow send performance on the browser bridge
- Keep a persistent session for daemon-mode messaging

### Performance comparison

| Mode | First send | Subsequent sends | Session lifetime |
|------|------------|------------------|-----------------|
| Cold start (no daemon) | ~15s | ~5s | Times out after ~30min idle |
| Daemon mode (warm + keepalive) | ~2s | ~2s | Persistent (5min keepalive) |

### API endpoints

All endpoints are on the browser-novnc bridge at `http://localhost:9223`
(internal: `http://browser-novnc:9223`).

#### POST /session/warm?platform=facebook

Pre-load the platform SPA and start the keepalive background task.

```bash
## Warm Facebook Messenger session
curl -X POST "http://localhost:9223/session/warm?platform=facebook"

## Warm Instagram session
curl -X POST "http://localhost:9223/session/warm?platform=instagram"

## Warm LinkedIn session
curl -X POST "http://localhost:9223/session/warm?platform=linkedin"
```

Response:
```json
{
  "status": "warmed",
  "platform": "facebook",
  "url": "https://www.facebook.com/messages/"
}
```

**Prerequisite:** An active browser session must exist (call
`POST /session/start` first and log in via noVNC).

#### GET /session/daemon-status

Check if daemon mode (keepalive) is active.

```bash
curl -s http://localhost:9223/session/daemon-status | python3 -m json.tool
```

Response:
```json
{
  "daemon_active": true,
  "platform": "facebook",
  "status": "logged_in",
  "message": "Session warmed for facebook"
}
```

### Supported platforms

| Platform | Warm URL | Use case |
|----------|----------|----------|
| facebook | https://www.facebook.com/messages/ | Personal Messenger sends |
| messenger | https://www.facebook.com/messages/ | Alias for facebook |
| instagram | https://www.instagram.com/direct/inbox/ | Instagram DMs |
| linkedin | https://www.linkedin.com/feed/ | LinkedIn posts/profile |

### Keepalive loop

After warming, a background task runs every 5 minutes:

```javascript
async function _keepalive_loop(platform) {
  const target = _warm_urls[platform];
  while (true) {
    await asyncio.sleep(300);  // 5 minutes
    await page.goto(target, wait_until="domcontentloaded", timeout=30000);
  }
}
```

- Navigates to the platform's main page every 5 minutes
- Prevents session idle timeouts
- Cancels any existing keepalive task when a new warm request is made
- Non-fatal: keepalive errors are logged but don't stop the loop

### Usage workflow

```bash
## 1. Start a browser session (if not already running)
curl -X POST http://localhost:9223/session/start \
  -H 'Content-Type: application/json' \
  -d '{"platform":"facebook"}'

## 2. Log in via noVNC (http://localhost:6080/vnc.html)
## Wait until session status shows "logged_in"

## 3. Warm the session for fast sends
curl -X POST "http://localhost:9223/session/warm?platform=facebook"

## 4. Verify daemon mode is active
curl -s http://localhost:9223/session/daemon-status

## 5. Now sends will be fast (~2s instead of ~15s)
```

### Source files

- `browser-novnc/browser-bridge.py` — Daemon mode endpoints and keepalive loop

### Future enhancements

- Configurable keepalive interval (currently fixed at 5 minutes)
- Multi-platform daemon mode (warm multiple platforms simultaneously)
- Smart keepalive: only navigate if session is about to expire
- Daemon mode statistics (uptime, keepalive count, errors)
- Auto-warm on container startup if session exists

## Patchright Operations

Patchright (`Kaliiiiiiiiii-Vinyzu/patchright`) is a drop-in fork of
Playwright that removes the `Runtime.enable` CDP leak that marks stock
Playwright as automated. SocialAuto uses it for all browser automation
that platforms commonly detect.

### When to use this skill

- A platform action works in a real browser but silently aborts or
  302-redirects under automation.
- Instagram/TikTok/Meta sessions are minted and revoked within seconds.
- `navigator.webdriver` evaluates to `true` inside the bridge/sidecars.
- You are adding a new browser-automation service or sidecar.
- You rebuilt the `browser-novnc` container and all bridge sessions
  disappeared.

### What was deployed

| Component | Language | Import pattern | Browser install |
|---|---|---|---|
| `browser-novnc` (bridge) | Python | `patchright.async_api` → fallback to `playwright.async_api` | `patchright install chromium` |
| `browser-novnc/extract-cookies.py` | Python | same fallback | bundled with bridge |
| `tiktok-browser-sidecar` | Node | `import { chromium } from "patchright"` | `npx patchright install chromium` |
| `linkedin-browser-sidecar` | Node | same | same |
| `facebook-browser-sidecar` | Node | same | same |
| Backend browser services | Python | same fallback | `playwright>=1.46.0` + `patchright>=1.62.1` |

Backend services:
- `app/services/browser_profile.py`
- `app/services/tiktok_bio_update.py`
- `app/services/tiktok_captcha_analyze.py`
- `app/services/tiktok_captcha_api.py`
- `app/services/tiktok_captcha_debug.py`
- `app/services/tiktok_captcha_debug2.py`
- `app/services/tiktok_captcha_debug3.py`

Dependencies declared in `social-automation/backend/pyproject.toml`.

### The `browser_profile` volume

Before this fix, the Chromium profile lived inside the container layer.
Every `--force-recreate` wiped all platform sessions, forcing a full
re-login cycle via noVNC.

After the fix, `docker-compose.yml` mounts a named volume:

```yaml
volumes:
  browser_profile:/app/browser-profile
```

Sessions now survive `docker compose up -d --force-recreate browser-novnc`.

### Quick verification

Run the bundled script:

```bash
.devin/skills/browser-ops/patchright-ops/scripts/verify.py
```

It checks:
1. Bridge `/health` and sidecar `/health` endpoints respond.
2. `navigator.webdriver` is `false` inside the bridge.
3. Every Python import fallback compiles.
4. No bare `playwright` imports remain except the intentional fallbacks.

### Adding patchright to a new Python service

```python
try:
    from patchright.async_api import async_playwright
except ImportError:
    from playwright.async_api import async_playwright
```

Then add to `pyproject.toml`:

```toml
"patchright>=1.62.1",
```

### Adding patchright to a new Node sidecar

`package.json`:

```json
{
  "dependencies": {
    "patchright": "^1.62.1"
  }
}
```

`server.js`:

```js
import { chromium } from "patchright";
```

`Dockerfile`:

```dockerfile
RUN npm install --production --no-cache && npx patchright install chromium
```

### Base image compatibility

Patchright tracks upstream Playwright browser revisions. The sidecar base
images (`mcr.microsoft.com/playwright:v1.*`) install Chromium builds
compatible with the pinned patchright version. Keep `patchright` version
close to the base image's Playwright version to avoid binary/revision
mismatches. The verification script will flag a revision mismatch as a
warning.

### Rollback

If a platform breaks with patchright, temporarily force the old driver
by running the container without patchright installed, or replace the
try/import with a plain playwright import in that single service. Do not
revert the whole stack unless the regression is global.

### References

- GitHub: `Kaliiiiiiiiii-Vinyzu/patchright` — drop-in Playwright fork.
- Original symptom: `Runtime.enable` CDP message leaks `navigator.webdriver`.
- Official Playwright docs for comparison only; SocialAuto runs the
  patched driver.

## LinkedIn Sidecar Operations

Operate and debug the LinkedIn browser sidecar for profile reads and
updates. The sidecar runs on port 9225 and provides browser automation
for LinkedIn personal profiles and company pages.

### When to use

- Read a LinkedIn personal profile (name, headline, about, experience, education, skills)
- Update LinkedIn headline, About, website, or location
- Update LinkedIn company page (about, website, specialties)
- Add experience or education entries to a LinkedIn profile
- Debug LinkedIn profile page not fully loading in the sidecar
- Fix broken edit button selectors after LinkedIn UI changes
- Inject a browser session from SocialAuto into the sidecar
- Upload profile picture or cover photo (only when explicitly requested)

### Architecture

```
SocialAuto API (port 8083)
    /api/v1/profile/{account_id}
         │
         ▼
    LinkedInSidecarClient
    (social-automation/backend/app/services/linkedin_sidecar.py)
         │
         ▼
    LinkedIn Browser Sidecar (port 9225)
    (linkedin-browser-sidecar/server.js)
         │
         ▼
    Playwright + Chromium (headed, with session)
```

### Session management

The sidecar requires a browser session (cookies/storage state) to operate.
Sessions are stored in the SocialAuto account's `meta_data.browser_storage_state`.

#### Inject session from SocialAuto to sidecar

```python
## Inside social-api container
docker compose exec -T social-api python3 -c "
import asyncio, httpx
from app.db.session import async_session_maker
from app.models.social_account import SocialAccount
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

async def save_session():
    # Get cookies from sidecar
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.get('http://linkedin-browser-sidecar:9225/debug/all-cookies')
        cookies = r.json().get('cookies', {})

    # Build storage_state
    cookie_list = [
        {'name': n, 'value': v, 'domain': '.linkedin.com', 'path': '/',
         'httpOnly': True, 'secure': True, 'sameSite': 'Lax'}
        for n, v in cookies.items()
    ]
    storage_state = {'cookies': cookie_list, 'origins': []}

    # Save to SocialAuto account
    async with async_session_maker() as db:
        r = await db.execute(select(SocialAccount).where(
            SocialAccount.id == '18d5cd59-f0c2-4fc4-986e-03601734c7a5'
        ))
        acc = r.scalars().first()
        meta = acc.meta_data or {}
        meta['browser_storage_state'] = storage_state
        acc.meta_data = meta
        flag_modified(acc, 'meta_data')  # Required for JSON column mutations
        await db.commit()

asyncio.run(save_session())
"
```

#### Restore session after sidecar restart

```python
## After docker compose restart linkedin-browser-sidecar
docker compose exec -T social-api python3 -c "
import asyncio, httpx
from app.db.session import async_session_maker
from app.models.social_account import SocialAccount
from sqlalchemy import select

async def restore():
    async with async_session_maker() as db:
        r = await db.execute(select(SocialAccount).where(
            SocialAccount.id == '18d5cd59-f0c2-4fc4-986e-03601734c7a5'
        ))
        acc = r.scalars().first()
        storage = (acc.meta_data or {}).get('browser_storage_state')
        if storage:
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.post('http://linkedin-browser-sidecar:9225/session',
                                json={'storage_state': storage})
                print(f'Session: {r.json().get(\"status\")}')

asyncio.run(restore())
"
```

### SocialAuto API endpoints

All profile operations go through SocialAuto (not the sidecar directly):

```
GET  /api/v1/profile/{account_id}           — Read profile
PUT  /api/v1/profile/{account_id}           — Update profile
POST /api/v1/profile/{account_id}/login     — Login (username/password)
POST /api/v1/profile/{account_id}/picture   — Upload profile picture
POST /api/v1/profile/{account_id}/cover     — Upload cover photo
```

#### LinkedIn personal account ID

```
18d5cd59-f0c2-4fc4-986e-03601734c7a5
```

#### LinkedIn company page account ID

```
9c4451bb-e820-489f-8676-76ddbc788ffe
```

### Profile update fields

The SocialAuto `ProfileUpdateRequest` schema supports:

| Field     | LinkedIn Personal            | LinkedIn Company |
| --------- | ---------------------------- | ---------------- |
| headline  | ✅                           | ❌               |
| about     | ✅                           | ✅ (as about)    |
| website   | ✅                           | ✅               |
| location  | ✅                           | ❌               |
| work      | ✅ (array of WorkEntry)      | ❌               |
| education | ✅ (array of EducationEntry) | ❌               |
| full_name | ❌ (ignored)                 | ❌               |
| biography | ❌ (ignored)                 | ❌               |
| phone     | ❌ (ignored)                 | ❌               |
| email     | ❌ (ignored)                 | ❌               |

### Fixed handlers (commit 37ccde47)

#### Headline update (handleUpdateHeadline)

- Navigates directly to `{profile_url}/edit/intro` (more reliable than clicking edit button)
- Uses `div[role="textbox"]` contenteditable (not dialog textarea)
- Strips `?isSelfProfile=true` query before appending edit path
- Falls back to old approach (click "Update headline" / "Edit intro" button) if direct URL fails

#### About update (handleUpdateAbout)

- Navigates to profile page and scrolls to trigger lazy-loaded About section
- Tries multiple strategies: `button[aria-label="Edit about"]`, pencil icon, edit/details URL
- Falls back to `{profile_url}edit/details/` if no edit button found
- Handles contenteditable div or textarea

#### Profile read (handleReadProfile)

- Scrolls page before reading sections (LinkedIn lazy-loads About, Experience, Education)
- Waits 3s after navigation for page stabilization
- Wrapped `page.evaluate` calls in try/catch for navigation context destruction

#### Company specialties (handleUpdateCompanySpecialties)

- Navigates to `/admin/edit/` directly (not `/about/`)
- Finds Specialties section by `h4:has-text("Specialties")`
- Uses `input.artdeco-pill__input` for specialty input
- Clicks "Add a specialty" ghost to activate input

### Common issues

| Issue                                                | Cause                                    | Fix                                         |
| ---------------------------------------------------- | ---------------------------------------- | ------------------------------------------- |
| "LinkedIn browser session not found"                 | No `browser_storage_state` in meta_data  | Inject session (see above)                  |
| Profile page only 1080px tall                        | LinkedIn anti-bot or session degradation | Re-login via noVNC, re-capture session      |
| "Could not locate Edit about button"                 | About section not loaded (lazy-load)     | Scroll page before reading                  |
| "page.evaluate: Execution context destroyed"         | Navigation during evaluate               | Wrap in try/catch, wait for page settle     |
| URL concatenation error (`?isSelfProfile=trueedit/`) | Query string not stripped                | Use `.split('?')[0]` before appending paths |
| `edit/details/` returns "page doesn't exist"         | LinkedIn removed this URL                | Use profile page + scroll + click approach  |
| Repeated "Execution context destroyed" on eval       | SPA re-navigates during hydration        | Retry the eval 3-4x with 2.5s waits         |
| `ERR_ABORTED` on activity-page navigate              | Transient SPA redirect                   | Retry navigateAndCheck up to 3x             |
| Member post stats missing (`member_stats_not_implemented`) | `ugcPosts` needs restricted `r_member_social` | `GET /profile/activity` scrapes the recent-activity page |

### Member activity scrape (`GET /profile/activity`)

Added for analytics discovery — member post stats have **no API** (the
`ugcPosts` authors finder and versioned `rest/posts` both reject this app's
scopes). Returns `{profile_url, followers, posts: [{urn, text, reactions,
comments, impressions, posted}]}`. Resolves the vanity slug via
`resolveProfileUrl()`, navigates `{slug}/recent-activity/all/`, scrolls for
lazy items, retries extraction across SPA re-navigations.

Used by `sync_linkedin_account` (member branch) in
`app/services/analytics_sync.py` — scraped URNs join local targets on both
full URN and numeric suffix (LinkedIn conflates `activity`/`share`/`ugcPost`
urn types). Followers land in `FollowerSnapshot`; `_linkedin_follower_count`
returns -1 for member accounts so the generic per-account snapshot does not
overwrite the scrape with a false 0.

Clear the in-memory 429 circuit without restarting:
`POST /session/clear-rate-limit`.

### Credential login + 2FA (worked Sep 2026)

`POST /login {username, password}` with `LINKEDIN_EMAIL`/`LINKEDIN_PASSWORD`
from `.env` reaches the checkpoint. LinkedIn offers **SMS to the account
phone** or email — choose SMS (the email is an unreadable external mailbox).

**The handler's 2FA fill is broken** — a second `POST /login` with
`verification_code` returns "2FA required but no code input found" because it
restarts the flow and lands back on the method chooser. Drive it manually:

```bash
## 1. On the chooser page: SMS radio is preselected → click "Αποστολή κωδικού"
##    (Send code) — page moves to secondaryHandleBridgeSubmit with a PIN input
## 2. Fill + submit via /debug/eval (React needs real setter + input event):
curl -X POST localhost:9225/debug/eval -d '{"script":"(()=>{
  const i=document.getElementById(\"input__verification_pin\");
  const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,\"value\").set;
  s.call(i,\"CODE\"); i.dispatchEvent(new Event(\"input\",{bubbles:true}));
  [...document.querySelectorAll(\"button\")].find(b=>b.type===\"submit\").click();
  return \"submitted\"})()"}'
## 3. Success → lands on /feed/. Verify: GET /session → logged_in:true
## 4. Persist: GET /debug/all-cookies → save as browser_storage_state in
##    meta_data of BOTH linkedin accounts (company + personal share the login)
```

Each `/login` retry **sends a new SMS** — only the latest code is valid.
WARP egress is broadly 429'd by LinkedIn — keep `proxy: false` (direct
residential IP). Playwright must be ≥1.62 — 1.48's Chromium fingerprint gets
redirect-looped by LinkedIn bot detection. `ensureBrowser` needs
`fresh=true` on login or it reloads the flagged session cookies.

### Source files

- `linkedin-browser-sidecar/server.js` — Sidecar server (Playwright + Express)
- `social-automation/backend/app/services/linkedin_sidecar.py` — LinkedInSidecarClient
- `social-automation/backend/app/api/profile.py` — SocialAuto profile API

### Future enhancements

- Anti-detection improvements (stealth mode, human-like scrolling delays)
- Session auto-refresh (detect expired session and re-login)
- LinkedIn profile section detection (handle UI changes automatically)
- Experience/education bulk import from CV
- Skills addition via sidecar
- Profile completeness score

## Twitter/X browser operations

The X API is pay-per-use; when `POST /2/tweets` returns `402 credits
depleted` the publisher falls back to posting through the shared
browser-novnc bridge (port 9223). This skill covers keeping that browser
session authenticated and driving the login flow.

### Publishing path

`backend/app/services/publishing.py::_publish_twitter`
→ on `402` → `_publish_twitter_via_browser` → `BrowserBridgeClient.post_tweet`
→ `https://x.com/compose/post` web composer (images via native
`set_input_files`, max 4).

Before posting, the fallback now calls `client.is_twitter_logged_in()`; if
logged out it attempts `client.twitter_login(identifier, password)` once
using `TWITTER_LOGIN_USERNAME` → `TWITTER_LOGIN_EMAIL` → `account.username`
for the identifier and `TWITTER_LOGIN_PASSWORD` for the password. Repeated
credential failures risk X lockouts — the login is attempted at most once
per publish.

### Login flow quirks (verified Sept 2026)

1. `x.com/login` redirects to `/i/jf/onboarding/web?mode=login` — that
   funnel **is** the login page. Do not look for the old two-page login.
2. The identifier field accepts the account's login email or handle
   (`TWITTER_LOGIN_USERNAME`/`TWITTER_LOGIN_EMAIL` —
   `baltzakis.themis@gmail.com`). **Do not fill the password input on step
   1** — doing so flips the funnel into signup mode: *"Get the app to
   finish signing up using email — Email signups are only allowed on the
   apps"* (the `download_app_pivot` loop). Fill ONLY
   `input[name=username_or_email]`, click Continue, and the real
   `login_enter_password` step appears.
3. Two steps: `input[name=username_or_email]` → **Continue** →
   `#/s/login_enter_password` → `input[name=password]` → **Continue**.
   Never fill the password on step 1.
4. On step 2 the `username` input is `disabled` and prefilled — filling it
   throws; fill only the enabled password input.
5. The page renders **duplicate hidden buttons** and **localized labels**
   (the account locale can show Greek `Συνέχεια`/`Σύνδεση`/`Επόμενο` instead
   of Continue/Log in). Match all variants, pick the *visible* one
   (`offsetParent !== null`), and click via `/session/mouse-click`
   (trusted input). JS `el.click()` may be rejected by Arkose.
6. Success = redirect to `x.com/home` with
   `[data-testid=SideNav_AccountSwitcher_Button]` present. Failure signals:
   body contains *"password you entered is incorrect"* (bad
   `TWITTER_LOGIN_PASSWORD`), or an `iframe[src*=arkose]` (manual captcha
   needed — noVNC).
7. Sessions persist in the Chromium profile (`/app/browser-profile`) plus
   `/app/cookies/twitter_storage_state.json`. If `auth_token` is revoked
   server-side, cookie injection redirects to logged-out landing — only a
   fresh login heals it.

### Scripted login

```bash
python3 scripts/twitter_browser_login.py [username]
```

Reads `TWITTER_LOGIN_*` from `.env`, pauses `celery-beat` first (see
contention below), runs the flow, calls `/session/extract` on success,
and always unpauses beat. Exit 2 = manual captcha needed.

#### Playwright MCP driver (headless)

```bash
python3 scripts/pw_mcp_x_login.py [handle]
```

Launches `mcr.microsoft.com/playwright/mcp` per `.devin/mcp_config.json`
and drives the funnel over stdio JSON-RPC in one `browser_run_code_unsafe`
call (locator clicks, `:visible` scoping for X's duplicated DOM clones,
`data-testid=mask`/progressbar waits, screenshots to `.playwright-mcp/`).
Exports x.com cookies to `.playwright-data/x_cookies.json` on success.

**Known wall (observed 2026-09-23):** X's funnel can soft-block automation —
the modal spins forever, or the account-lookup step returns *"We couldn't
find an active X account with that username"* for a handle that is
verified live via the OAuth API. That error is an anti-automation wall,
NOT bad credentials (the password step never runs). Repeated funnel hits
make it worse — stop and use manual noVNC login instead of retrying.

### Browser contention (shared browser-novnc)

All platforms share one Chromium. The bridge enforces a **busy-hold with
platform attribution**: `BrowserBridgeClient(url, platform="twitter")`
sends `X-Platform: twitter` on every request; tagged interactions set a
180s hold owned by that platform (`busy_owner`/`busy_until`). While held,
requests tagged for another platform — or untagged — get
`409 Browser busy` from `_ensure_live_page` (covers navigate/evaluate/
fill/click/extract), and `/session/start` for another platform returns
409 too. The owning platform re-enters freely; a same-platform start
reuses the session. `{"platform": "...", "force": true}` overrides for
manual recovery. Extra rules that make publishes survive poller churn:

- A `waiting` session blocks foreign starts for `WAITING_TIMEOUT`
  (300s), then is preemptible — stale login windows can't starve
  publishers.
- `session/start` returns only after the page is live (≤20s) and a
  tagged start takes an immediate hold — no unprotected gap.
- `client.start_session("twitter", contention_retries=14)` — used by
  `_publish_twitter_via_browser` — retries 409s every 20s and
  force-preempts on the last 3 tries. A publish waits ~5min worst case
  but no longer fails just because pollers are cycling sessions.
- Session start cancels the prior `_run_browser` task and retries
  launch after killing orphan Chromium if the profile lock collides.

All backend call sites are tagged (`twitter`, `instagram`, `facebook`,
`threads`, `tiktok`). For extra safety during a manual noVNC session you
can still **pause beat**:

```bash
docker compose pause celery-beat   # stops all scheduled pollers
docker compose unpause celery-beat # resume afterwards
```

### Composer notes

- X counts weighted chars (URLs = 23 via t.co, emoji = 2). If **Post** is
  disabled, check for *"You have exceeded the character limit by N"* in
  `document.body.innerText` — trim and retype. `_fit_x_limit` in
  `publishing.py` handles this on the automated path.
- Text entry must use `document.execCommand('insertText')` after
  `selectAll`/`delete` — direct `innerText` writes don't update X's
  composer state and leave Post disabled.
- Success signal: composer closes and the SPA returns to `/home`; verify
  on `x.com/<handle>` — latest article shows the tweet + media.

### Quick probes

```bash
## Logged in? (SideNav switcher only renders when authenticated)
curl -s -X POST http://localhost:9223/session/evaluate -H 'Content-Type: application/json' \
  -d '{"expression":"({url:location.href, loggedIn:!!document.querySelector(\"[data-testid=SideNav_AccountSwitcher_Button]\")})"}'

## Fresh session parked on the login page for manual noVNC login
curl -s -X POST http://localhost:9223/session/start \
  -H 'Content-Type: application/json' -d '{"platform":"twitter"}'
## → open http://localhost:6080/vnc.html

## Force-save cookies/storage state after manual login
curl -s -X POST http://localhost:9223/session/extract -H 'Content-Type: application/json' -d '{}'
```

### API-side notes

- `402 {"detail":"credits depleted"}` — X developer account out of write
  credits; buy credits at developer.x.com or rely on the browser fallback.
- `401` on `/2/tweets` — OAuth2 token stale; code auto-refreshes once via
  `POST /2/oauth2/token`.
- Media upload (`upload.twitter.com/1.1/media/upload.json`) works even
  when posting credits are depleted — upload succeeds, tweet create fails.
