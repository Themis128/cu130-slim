---
name: twitter-browser-ops
description: Operate and debug X/Twitter browser publishing and login through the shared browser-novnc bridge (port 9223). Covers the two-step onboarding login flow (username handle NOT email, visible-Continue mouse clicks, disabled username field on the password step), session persistence via the persistent profile, poller hijacking and celery-beat pausing, the 402 credits-depleted API fallback path, and verify-login probes. Use when publishing tweets via the browser fallback, restoring an expired x.com session, debugging "credits depleted" / login-pivot loops, or checking whether the browser is authenticated as @TBaltzakis.
---

# Twitter/X browser operations

The X API is pay-per-use; when `POST /2/tweets` returns `402 credits
depleted` the publisher falls back to posting through the shared
browser-novnc bridge (port 9223). This skill covers keeping that browser
session authenticated and driving the login flow.

## Publishing path

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

## Login flow quirks (verified Sept 2026)

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

## Scripted login

```bash
python3 scripts/twitter_browser_login.py [username]
```

Reads `TWITTER_LOGIN_*` from `.env`, pauses `celery-beat` first (see
contention below), runs the flow, calls `/session/extract` on success,
and always unpauses beat. Exit 2 = manual captcha needed.

## Browser contention (shared browser-novnc)

All platforms share one Chromium. Messenger/personal-DM pollers call
`/session/start` for their platform and **tear down the current context**,
hijacking the page mid-login or mid-compose. Mitigations:

- **Pause beat before manual/scripted login:**
  `docker compose pause celery-beat` … `docker compose unpause celery-beat`
- Publishing uses `browser_session("twitter", client)` (Redis lock
  `browser-bridge:lock`) — pollers that bypass the lock can still hijack;
  pausing beat is the reliable guard.
- If the page jumps to `facebook.com/login` or `instagram.com` mid-flow, a
  poller hijacked it — pause beat and retry.

## Quick probes

```bash
# Logged in? (SideNav switcher only renders when authenticated)
curl -s -X POST http://localhost:9223/session/evaluate -H 'Content-Type: application/json' \
  -d '{"expression":"({url:location.href, loggedIn:!!document.querySelector(\"[data-testid=SideNav_AccountSwitcher_Button]\")})"}'

# Fresh session parked on the login page for manual noVNC login
curl -s -X POST http://localhost:9223/session/start \
  -H 'Content-Type: application/json' -d '{"platform":"twitter"}'
# → open http://localhost:6080/vnc.html

# Force-save cookies/storage state after manual login
curl -s -X POST http://localhost:9223/session/extract -H 'Content-Type: application/json' -d '{}'
```

## API-side notes

- `402 {"detail":"credits depleted"}` — X developer account out of write
  credits; buy credits at developer.x.com or rely on the browser fallback.
- `401` on `/2/tweets` — OAuth2 token stale; code auto-refreshes once via
  `POST /2/oauth2/token`.
- Media upload (`upload.twitter.com/1.1/media/upload.json`) works even
  when posting credits are depleted — upload succeeds, tweet create fails.
