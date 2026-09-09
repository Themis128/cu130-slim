---
name: browser-bridge-ops
description: >-
  Automate browser interactions through the browser-novnc bridge (port 9223):
  navigate, click, fill forms, upload files, evaluate JS, extract cookies,
  and manage sessions for Instagram, Facebook, LinkedIn, TikTok, Twitter,
  Threads, and other platforms. Use when automating social profile updates
  via the embedded browser, uploading profile pictures, editing bios,
  or scraping profile data that APIs don't expose.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# Browser Bridge Operations

Automate the embedded browser (browser-novnc container, port 9223) to
interact with social platforms that lack write APIs or where API access
is insufficient for profile management.

## When to use

- Update Threads bio/name/profile picture (no write API for profile fields)
- Upload profile pictures to platforms without a picture-update API
- Click through UI flows that require a real browser session
- Extract cookies from an active login session
- Evaluate JavaScript on a page to scrape data APIs don't return
- Fill and submit forms in the embedded browser

## Architecture

```
Agent → browser-bridge (port 9223) → Playwright browser → Social platform
                                         ↓
                                    noVNC viewer (port 6080)
                                    for manual interaction
```

The `browser-novnc` container runs a Python (FastAPI) bridge on port 9223
that controls a Playwright Chromium browser. The browser is also visible
through noVNC at `http://localhost:6080/vnc.html` for manual interaction.

## API base

```
http://localhost:9223
```

No authentication required — the bridge is internal to the compose network.

## Endpoints

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
| POST | `/session/evaluate` | Evaluate JavaScript expression |
| POST | `/session/click` | Click an element by selector |
| POST | `/session/fill` | Fill an input/textarea by selector |
| POST | `/session/upload` | Upload a file to an input[type=file] |
| POST | `/session/login` | Start a login flow for a platform |
| POST | `/session/extract` | Extract cookies immediately |
| POST | `/session/click` | Click element by selector |
| POST | `/session/mouse-click` | Click at specific coordinates |

## Key request formats

### Start session
```json
POST /session/start
{"platform": "threads"}
```

### Navigate
```json
POST /session/navigate
{"url": "https://www.threads.com/@cloudless_gr"}
```

### Evaluate JS
```json
POST /session/evaluate
{"expression": "document.title"}
```

### Click element
```json
POST /session/click
{"selector": "div[role=button]"}
```

### Fill input
```json
POST /session/fill
{"selector": "textarea", "value": "New bio text"}
```

### Upload file
```json
POST /session/upload
{"selector": "input[type=file]", "file_path": "/tmp/logo.png"}
```

With click-to-trigger file chooser:
```json
POST /session/upload
{"selector": "input[type=file]", "file_path": "/tmp/logo.png", "click_selector": "[role=dialog] img"}
```

## Supported platforms

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
| discord | https://discord.com/login |
| telegram | https://web.telegram.org/a/ |
| whatsapp | https://web.whatsapp.com/ |

## Sidecar browsers (separate containers)

| Container | Port | Platform |
|-----------|------|----------|
| browser-novnc | 9223 | All (via bridge) |
| tiktok-browser-sidecar | 9224 | TikTok |
| linkedin-browser-sidecar | 9225 | LinkedIn |
| facebook-browser-sidecar | 9226 | Facebook |

## Tool scripts

Run from repo root `cu130-slim/`:

```bash
# Check browser bridge health and current session
.devin/skills/browser-bridge-ops/scripts/bridge-status.sh

# Navigate to a URL in the browser
.devin/skills/browser-bridge-ops/scripts/bridge-navigate.sh "https://www.threads.com/@cloudless_gr"

# Evaluate JavaScript and print result
.devin/skills/browser-bridge-ops/scripts/bridge-eval.sh "document.title"

# Get current page info (URL + title)
.devin/skills/browser-bridge-ops/scripts/bridge-page-info.sh

# Start a session for a platform
.devin/skills/browser-bridge-ops/scripts/bridge-start-session.sh threads

# Upload a file to the browser
.devin/skills/browser-bridge-ops/scripts/bridge-upload.sh /tmp/logo.png "input[type=file]"

# Click an element
.devin/skills/browser-bridge-ops/scripts/bridge-click.sh "div[role=button]"

# Fill a form field
.devin/skills/browser-bridge-ops/scripts/bridge-fill.sh "textarea" "New bio text"

# Take a screenshot (saves to /tmp/browser-screenshot.png)
.devin/skills/browser-bridge-ops/scripts/bridge-screenshot.sh
```

## Common patterns

### Edit a Threads profile

```bash
# 1. Navigate to profile
bridge-navigate.sh "https://www.threads.com/@cloudless_gr"

# 2. Click Edit profile button
bridge-eval.sh "(() => { const btns = document.querySelectorAll('div[role=button]'); for (const btn of btns) { if (btn.textContent.trim() === 'Edit profile') { btn.click(); return 'clicked'; } } return 'not found';; })()"

# 3. Click Bio section
bridge-eval.sh "(() => { const dialog = document.querySelector('[role=dialog]'); const all = dialog.querySelectorAll('div[role=button]'); for (const el of all) { if (el.textContent.trim().startsWith('Bio')) { el.click(); return 'clicked'; } } return 'not found';; })()"

# 4. Fill bio textarea
bridge-fill.sh "textarea" "Clear skies. Zero friction."

# 5. Click Done to save
bridge-eval.sh "(function() { const all = Array.from(document.querySelectorAll('div[role=button], button')); const done = all.filter(b => b.innerText.trim() === 'Done'); if (done.length > 0) { done[done.length - 1].click(); return 'clicked'; } return 'no Done'; })()"
```

### Upload a profile picture

```bash
# Copy image to browser container first
docker compose cp /tmp/logo.png browser-novnc:/tmp/logo.png

# Upload using click_selector to trigger file chooser
bridge-upload.sh /tmp/logo.png "input[type=file]" "[role=dialog] img"
```

## Important notes

- The browser bridge is unauthenticated and internal only.
- File uploads require the file to exist inside the browser-novnc container.
  Use `docker compose cp` to copy files from host to container first.
- Threads profile photos are synced from Instagram — changing the Threads
  profile picture requires changing the linked Instagram profile picture.
- The evaluate endpoint uses `expression` (not `script`) as the JSON key.
- Some platforms (LinkedIn, Facebook, TikTok) have dedicated sidecar
  containers with their own ports (9225, 9226, 9224).
- Wait 2-3 seconds between navigation and interaction for pages to load.
