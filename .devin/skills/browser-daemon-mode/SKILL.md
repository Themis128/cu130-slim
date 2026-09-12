---
name: browser-daemon-mode
description: >-
  Keep the browser-novnc session warm for fast social media sends via the
  daemon mode endpoints on the browser bridge (port 9223). Pre-loads the
  platform SPA and runs a background keepalive loop to prevent idle
  timeouts. Covers POST /session/warm, GET /session/daemon-status, and
  the keepalive background task. Use when optimizing send speed for
  personal Messenger, Instagram DMs, or LinkedIn posts, or when the
  browser session keeps timing out between sends.
allowed-tools:
  - read
  - exec
  - grep
  - web_search
triggers:
  - user
  - model
---

# Browser Daemon Mode (Warm Session + Keepalive)

Keep the browser-novnc session warm so subsequent social media sends are
fast (~2s vs ~15s cold start). Pre-loads the platform SPA and runs a
background keepalive loop that navigates every 5 minutes to prevent idle
timeouts.

## When to use

- Speed up personal Messenger sends (warm session = ~2s vs ~15s cold)
- Prevent browser session idle timeouts during long polling intervals
- Pre-load Instagram, Facebook, or LinkedIn before bulk operations
- Debug slow send performance on the browser bridge
- Keep a persistent session for daemon-mode messaging

## Performance comparison

| Mode | First send | Subsequent sends | Session lifetime |
|------|------------|------------------|-----------------|
| Cold start (no daemon) | ~15s | ~5s | Times out after ~30min idle |
| Daemon mode (warm + keepalive) | ~2s | ~2s | Persistent (5min keepalive) |

## API endpoints

All endpoints are on the browser-novnc bridge at `http://localhost:9223`
(internal: `http://browser-novnc:9223`).

### POST /session/warm?platform=facebook

Pre-load the platform SPA and start the keepalive background task.

```bash
# Warm Facebook Messenger session
curl -X POST "http://localhost:9223/session/warm?platform=facebook"

# Warm Instagram session
curl -X POST "http://localhost:9223/session/warm?platform=instagram"

# Warm LinkedIn session
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

### GET /session/daemon-status

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

## Supported platforms

| Platform | Warm URL | Use case |
|----------|----------|----------|
| facebook | https://www.facebook.com/messages/ | Personal Messenger sends |
| messenger | https://www.facebook.com/messages/ | Alias for facebook |
| instagram | https://www.instagram.com/direct/inbox/ | Instagram DMs |
| linkedin | https://www.linkedin.com/feed/ | LinkedIn posts/profile |

## Keepalive loop

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

## Usage workflow

```bash
# 1. Start a browser session (if not already running)
curl -X POST http://localhost:9223/session/start \
  -H 'Content-Type: application/json' \
  -d '{"platform":"facebook"}'

# 2. Log in via noVNC (http://localhost:6080/vnc.html)
# Wait until session status shows "logged_in"

# 3. Warm the session for fast sends
curl -X POST "http://localhost:9223/session/warm?platform=facebook"

# 4. Verify daemon mode is active
curl -s http://localhost:9223/session/daemon-status

# 5. Now sends will be fast (~2s instead of ~15s)
```

## Source files

- `browser-novnc/browser-bridge.py` — Daemon mode endpoints and keepalive loop

## Future enhancements

- Configurable keepalive interval (currently fixed at 5 minutes)
- Multi-platform daemon mode (warm multiple platforms simultaneously)
- Smart keepalive: only navigate if session is about to expire
- Daemon mode statistics (uptime, keepalive count, errors)
- Auto-warm on container startup if session exists
