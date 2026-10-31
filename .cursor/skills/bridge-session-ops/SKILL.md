---
name: bridge-session-ops
description: >
  Check and seed platform sessions on the shared browser bridge
  (browser-novnc:9223). `status` shows which platforms hit a login wall vs
  a live session; `seed-x` injects X_WEB_AUTH_TOKEN + ct0 cookies (X's
  session is cookie-based — bypasses the login wall entirely); `eval`
  is a raw /session/evaluate passthrough. Handles 409 shared-session
  contention with retry. Use before li_export_request / x_oauth_approve /
  skool_ops when they hit "login wall". Tool: scripts/bridge_sessions.py
  (run inside social-api).
---

# Bridge Session Ops — which platform is logged in, and fix the walls

The shared browser bridge holds one session at a time — each platform
needs its own login state inside it. This tool makes the state visible
and seeds sessions where the creds exist.

## Commands

```bash
bridge_sessions.py status      # x / linkedin / tiktok / threads / skool
bridge_sessions.py seed-x      # auth_token + ct0 → x.com cookies
bridge_sessions.py eval <js>   # raw /session/evaluate
```

## Session sources

- **X** — `X_WEB_AUTH_TOKEN` + `X_WEB_CT0` in `.env` are the real session
  cookies (`ct0` is JS-settable; `auth_token` needs the bridge's cookie
  endpoint or CDP — the script tries both). Once seeded, X surfaces work.
- **LinkedIn** — `seed-linkedin` injects `LINKEDIN_LI_AT` (+ optional
  `LINKEDIN_JSESSIONID`) via the bridge cookie endpoint — the same
  HttpOnly-injection path as X's auth_token. Get `li_at` once from a
  logged-in browser (DevTools → Application → Cookies → linkedin.com)
  or by a manual login on the sidecar; after that the session persists.
  The 401 "Not logged in to LinkedIn" on scrapes = a dead li_at, seed it.
- **Other platforms** — session_healer relogs via stored
  `*_EMAIL`/`*_PASSWORD` creds on expiry; if `status` shows LOGIN_WALL
  and no creds exist, that's a user-input gap (add to secret store).

## Bridge contention

409 "busy with <platform> session" is normal — the bridge is shared.
All scripts here and in `human-gate-bridges` retry; if a platform's
session persistently holds it, its healer flow is running — that's good.

## form_fill.py — generic web-form driver

    form_fill.py '<url>' '{"#email":"me@x.com","[name=company]":"Cloudless"}' [submit_sel]

Pins keepawake → navigates → waits for first field → fills each
selector via input+change events → clicks submit when all fields land.
Use for Qualtrics verification forms, dev-portal request forms, or any
unauthenticated web form — the field-map is a JSON object of
selector->value pairs.
