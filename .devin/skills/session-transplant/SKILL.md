---
name: session-transplant
description: Move a logged-in browser session between SocialAuto browsers — from a platform sidecar or the Playwright MCP browser into the shared browser-novnc bridge (9223) — via cookie export/inject. Use when a bridge session is dead/wedged at a login page but another browser holds a live session, when pollers fail with login redirects, or to heal Messenger/Instagram/Threads/Twitter sessions without manual noVNC login.
---

# Session transplant

Every SocialAuto browser layer keeps independent cookie state. When one is
logged in and another is dead, copy the cookies — no password, no noVNC, no
2FA needed.

## Cookie sources

| Source                 | How to export                                                                                                                                                                      |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| FB sidecar :9226       | `GET /debug/all-cookies` → `{cookies: {name: value}}` (includes httpOnly `c_user`, `xs`)                                                                                           |
| LinkedIn sidecar :9225 | same endpoint shape                                                                                                                                                                |
| TikTok sidecar :9224   | same endpoint shape                                                                                                                                                                |
| Playwright MCP browser | `browser_run_code_unsafe`: `async (page) => JSON.stringify(await page.context().cookies('https://www.instagram.com'))` — returns Playwright-format list incl. httpOnly `sessionid` |
| Bridge itself          | `GET /session/cookies` or `POST /session/extract` output                                                                                                                           |

## Inject into the bridge (:9223)

```bash
python3 scripts/session_transplant.py --platform facebook --sidecar 9226
python3 scripts/session_transplant.py --platform instagram --cookies ig_cookies.json
```

The script does: `/session/start` (platform) → `/session/cookies` →
`/session/navigate` (verifies not on a login page) → `/session/extract`
(persists `*_storage_state.json` + per-cookie files under the bridge's
`/data` volume).

## Bridge contention (X-Platform busy-hold)

The bridge is ONE shared Chromium. A platform that touches the page owns a
180s `busy_until` hold; foreign-platform or untagged calls get `409`.
Practical rules:

- Always send `X-Platform: <platform>` on every call — untagged calls are
  rejected while anyone holds the browser.
- To act under an active hold, match the current owner's tag (check the
  409 body: "Browser busy with <owner> session") — the pollers rotate
  `facebook`/`instagram`/`threads`/`twitter`/`messenger`.
- `POST /session/stop` clears everything including the hold (drops
  in-memory cookies — only use when the context is expendable).
- Client-side: `BrowserBridgeClient._request()` retries 409s for ~200s;
  `health`/`session_status`/`stop_session` stay fast-fail.

## Caveats

- **Web `sessionid` ≠ mobile private-API session.** A browser-extracted
  Instagram sessionid works for the bridge/DM web paths but aiograpi's
  mobile endpoints (`i.instagram.com`) can still soft-fail or hit the
  `unsupported_version` checkpoint. Prefer the bridge path for DMs.
- Cookies bind loosely to IP — same-host browsers are fine; transplanting
  across hosts/proxies may trigger checkpoints.
- FB/IG cookie values are already percent-encoded — do NOT re-encode them
  when injecting (Playwright wants the stored value verbatim).
- **Cross-domain OAuth bootstraps clobber transplanted sessions.** Driving
  threads.com login through "Log in with Facebook" on a transplanted IG
  session invalidated the IG `sessionid` (had to re-inject). Keep exported
  cookie JSONs so a session can be restored after a failed bootstrap.
- Facebook bio limit is 101 chars; React controlled inputs need real key
  events or `execCommand('insertText')` — programmatic `.value` sets are
  ignored.

## Threads bootstrap via live Instagram session

Threads auth delegates to Instagram SSO — if the bridge has a live IG
session, no password is needed. Verified working 2026-09-20:

1. **Claim the browser first**: `POST /session/start {"platform":"threads",
"force":true}` with `X-Platform: threads` — a start whose header matches
   the platform takes an immediate 180s hold; each subsequent call renews it
   and pollers get 409. Without this, pollers hijack the page mid-flow.
2. `POST /session/navigate` →
   `https://www.threads.com/login/?show_toa_choice_screen=false&variant=toa_ig`
   (the "Use your Instagram account" variant — skips the chooser).
3. Accept the cookie banner if present (`Allow all cookies` role=button).
4. Click **"Continue with Instagram"** — MUST use `POST /session/click`
   (`div[role=button]` + `text`), not JS `.click()`: the div's React handler
   ignores synthetic JS clicks but Playwright's locator click works.
5. Land on `instagram.com/threads/sso/?waterfall_id=…` showing
   "Continue to Threads / <user> / <user>". "Continue to Threads" is a
   heading — the clickable is the **account card**:
   `POST /session/click {"selector":"div[role=button]","text":"<username>"}`.
6. Lands on `threads.com/` logged in (nav shows New thread/Messages/
   Profile/Insights). `POST /session/extract` persists 4 cookies
   (`sessionid`,`csrftoken`,`ds_user_id`,`ig_did` — note the threads
   `sessionid` differs from the IG one).

The IG SSO path does NOT clobber the IG session (unlike the Facebook OIDC
route) — verified: instagram.com still `loginForm:false` afterwards.

## Related

- `session-health-ops` — triage which layer failed before transplanting
- `browser-daemon-mode` — keep the bridge warm so sessions stay alive
- `novnc-login-helper` — when no live session exists anywhere to copy
