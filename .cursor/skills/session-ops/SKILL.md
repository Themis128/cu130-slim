---
name: session-ops
description: >-
  Keep every social session logged in: hourly Celery auto-heal sweep, one-shot health checks across OAuth tokens + browser bridge + sidecars, session transplant between browsers, manual login bootstrap via noVNC or Playwright MCP cookie inject. Use when sessions die, expire, or need manual relogin.
---

# Session Ops

Consolidated skill — each section below was a standalone skill. Member scripts/templates live under `<source-skill>/` inside this directory.

| Section | Source |
|---|---|
| Session auto-heal | `session-ops` |
| Session health operations | `session-ops` |
| Session transplant | `session-ops` |
| Social Session Bootstrap — Master Guide | `session-ops` → `social-session-bootstrap/` |
| Playwright MCP Login — Social Platform Session Bootstrap | `session-ops` → `playwright-mcp-login/` |
| noVNC Manual Login Helper | `session-ops` → `novnc-login-helper/` |

## Session auto-heal

`app.services.session_healer.heal_all_sessions()` keeps every social
platform connected without babysitting. Scheduled hourly at :20 via
celery-beat (`heal-sessions` → `social-worker-default` queue) and callable
on demand:

```bash
## on-demand run (admin token required — returns the full status map)
curl -X POST https://social.cloudless.gr/api/v1/ops/session-heal \
  -H "Authorization: Bearer $TOKEN"

## or inside the stack, no auth needed
docker compose exec -T social-api python3 -c "
import asyncio; from app.services.session_healer import heal_all_sessions
print(asyncio.run(heal_all_sessions()))"
```

A distributed Redis lock (`session_healer:lock`, 25min) prevents
overlapping runs — a second call returns `{skipped: ...}`.

### Recovery ladder

| Layer | Probe | Auto-recovery | Manual fallback |
|---|---|---|---|
| LinkedIn sidecar `:9225` | `GET /session` (navigates /feed/) | 1. stale 429 circuit → `POST /session/clear-rate-limit` (>6h past expiry) 2. credential `POST /login` (LINKEDIN_EMAIL + LINKEDIN_PASSWORD env/secret) → app-push 2FA auto-approves when the owner taps Yes | `pending_2fa` → Slack alert asks for app approval or SMS code. **Never cookie-transplant LinkedIn — `li_at` is fingerprint-bound and gets revoked globally.** |
| Facebook sidecar `:9226` | `GET /session/validate` (deep check incl. profile-picker detection) | Re-inject stored `meta_data.browser_storage_state` via `POST /session verify:true` — same-class restores accepted | `needs_manual_login` → Slack alert |
| Bridge platforms `:9223` (facebook, instagram, threads, twitter, tiktok) | `GET /session/status` + `POST /session/start` (X-Platform tagged) | Platform-specific — see below | `needs_manual_login` → Slack alert with noVNC URL |

Bridge platform recovery paths:

- **threads** — IG-SSO bootstrap: navigate
  `threads.com/login?variant=toa_ig` → click "Continue with Instagram" →
  click the account card → feed. Works while the *instagram* bridge
  session is alive (same shared Chromium profile).
- **instagram** — `/session/login` with `INSTAGRAM_USERNAME` /
  `INSTAGRAM_PASSWORD` (env or secret store).
- **twitter** — `/session/login` with the account's DB username +
  `TWITTER_LOGIN_PASSWORD` (secret store).
- **facebook** — inject `browser_storage_state` cookies via
  `POST /session/cookies`, then navigate-verify.
- **tiktok** — inject `TIKTOK_SESSION_ID` (secret store) via
  `POST /session/cookies`, navigate-verify.

Contention: a `409` from `/session/start` means another platform owns the
browser — the healer marks the platform `contended` and skips it rather
than tearing down live sessions. Everything re-checks next run.

### Persistence

Healthy sessions are written back automatically:

- Sidecars → `meta_data.browser_storage_state` on every account of the
  platform (Playwright storage_state shape), plus `LINKEDIN_COOKIE` in
  the secret store for LinkedIn.
- Bridge → `POST /session/extract` writes the durable
  `/app/cookies/<platform>_*.json` volume files; the name→value jar is
  also synced to `meta_data.browser_cookies`.
- `meta_data.session_healed_at` records the last heal timestamp.

### Alerting

Slack via `post_alert_to_slack`, deduped per platform for 24h
(`session_healer:alerted:<platform>` in Redis). Two alert types:

- `needs_manual_login` / `pending_2fa` / `sidecar_down` — human required
- one-shot "is back / recovered" notice when a previously-alerted
  platform heals

Status values in the summary map: `healthy`, `recovered`, `contended`,
`rate_limited`, `pending_2fa`, `needs_manual_login`, `sidecar_down`,
`error`. `unhealthy[]` lists everything needing attention; empty = all
green.

### Verified live 2026-09-30

```json
{"linkedin": "healthy", "facebook_sidecar": "healthy",
 "twitter": "healthy", "facebook/instagram/threads/tiktok": "contended",
 "unhealthy": []}
```

### Gotchas

- LinkedIn `POST /login` retries each send a fresh push/SMS — the healer
  parks `session_healer:linkedin_pending_2fa` (24h) instead of spamming.
- The bridge is a single Chromium context — healing platform X briefly
  preempts platform Y's live page. The hourly cadence keeps this cheap.
- Never log cookie values; logs show names/counts only.
- Secrets read via `secret_store` fall back to `.env` — keep
  `LINKEDIN_PASSWORD`, `INSTAGRAM_USERNAME/PASSWORD`,
  `TWITTER_LOGIN_PASSWORD`, `TIKTOK_SESSION_ID` populated for
  credential-driven recovery to work.

### Related

- `.devin/skills/session-ops/SKILL.md` — manual cookie moves
- `.devin/skills/session-ops/SKILL.md` — one-shot health report
- `.devin/skills/browser-ops/SKILL.md` — sidecar internals,
  2FA quirk notes

## Session health operations

SocialAuto has three independent session layers — a failure in any one
breaks that platform:

1. **OAuth tokens** (`social_accounts` table) — used by API publishing.
   Auto-refreshed hourly by `token_refresh.refresh_expiring_tokens`;
   `expired` accounts **with** a stored refresh token now self-heal
   (auto-fix, Sept 2026). `expired` + **no** refresh token = manual
   reconnect required.
2. **browser-novnc bridge** (port 9223) — shared Chromium for Twitter/X,
   Instagram, Threads, TikTok, personal-Messenger fallback paths. Has a
   platform-attributed busy-hold (`X-Platform` header → `busy_owner`);
   foreign callers get 409 while a platform holds it.
3. **Dedicated sidecars** — TikTok 9224, LinkedIn 9225, Facebook 9226,
   Messenger 9230. Separate browser sessions with their own login state.

### Ops tool

```bash
python3 scripts/session_health.py          # full report
python3 scripts/session_health.py --json   # machine-readable
```

Checks OAuth account rows, the bridge `/health` + `/session/status`, and
each sidecar `/health`; exits 1 on hard failures and prints a
"Needs attention" section.

### Interpretation matrix

| Symptom | Layer | Fix |
|---|---|---|
| account `expired`, has refresh token | OAuth | None — hourly task retries unconditionally; verify next run or trigger `celery call app.worker.tasks.token_refresh.refresh_expiring_tokens` |
| account `expired`, no refresh token | OAuth | Manual reconnect via Accounts page OAuth |
| bridge session `status: idle` / wrong platform | Bridge | `POST /session/start {"platform": "<p>"}`; if 409 busy, another platform holds it — wait or `force:true` for manual recovery |
| x.com publish fails `402 credits depleted` | X billing, NOT auth | Browser fallback engages automatically; session must be logged in (see `browser-ops`) |
| sidecar `/health` down | Sidecar | `docker compose restart <sidecar>`; check `docker compose ps` |
| bridge session `done` but page on wrong site | Bridge | Cosmetic — busy-hold owns the page; next tagged op navigates back |
| bridge sessions lost after `docker compose up -d --force-recreate browser-novnc` | Bridge storage | Mount the `browser_profile` volume (`browser-ops` skill); otherwise the Chromium profile lives in the container layer and is wiped on recreate |

### Refresh/recovery entry points

- Twitter/X browser login: `python3 scripts/twitter_browser_login.py`
  (two-step flow, see `browser-ops`)
- LinkedIn sidecar session: `browser-ops` skill (weekly refresh
  task exists; manual login via noVNC if dead)
- Personal Messenger / Threads / Instagram browser sessions:
  `browser-ops` + `session-ops` skills
- OAuth reconnect: `social-accounts-manager` skill

### Notes

- OAuth token health and browser-session health are independent — e.g.
  Twitter's OAuth refresh works fine while x.com API publishing is
  billing-blocked, and the browser session is what the fallback needs.
- Never expose the bridge publicly; all session endpoints are internal.

## Session transplant

Every SocialAuto browser layer keeps independent cookie state. When one is
logged in and another is dead, copy the cookies — no password, no noVNC, no
2FA needed.

### Cookie sources

| Source                 | How to export                                                                                                                                                                      |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| FB sidecar :9226       | `GET /debug/all-cookies` → `{cookies: {name: value}}` (includes httpOnly `c_user`, `xs`)                                                                                           |
| LinkedIn sidecar :9225 | same endpoint shape                                                                                                                                                                |
| TikTok sidecar :9224   | same endpoint shape                                                                                                                                                                |
| Playwright MCP browser | `browser_run_code_unsafe`: `async (page) => JSON.stringify(await page.context().cookies('https://www.instagram.com'))` — returns Playwright-format list incl. httpOnly `sessionid` |
| Bridge itself          | `GET /session/cookies` or `POST /session/extract` output                                                                                                                           |

### Inject into the bridge (:9223)

```bash
python3 scripts/session_transplant.py --platform facebook --sidecar 9226
python3 scripts/session_transplant.py --platform instagram --cookies ig_cookies.json
```

The script does: `/session/start` (platform) → `/session/cookies` →
`/session/navigate` (verifies not on a login page) → `/session/extract`
(persists `*_storage_state.json` + per-cookie files under the bridge's
`/data` volume).

### Inject into a platform sidecar (MCP → sidecar)

Sidecars accept a Playwright `storage_state` directly — heals the FB
profile-picker gate without a trusted click. Verified 2026-09-21:
`logged_in: true, profile_picker: false` persisted across `/session/validate`.

1. Export from the MCP browser (must already be logged in):
   `browser_run_code_unsafe`:
   `async (page) => JSON.stringify(await page.context().cookies('https://www.facebook.com'))`
   — needs `c_user` + `xs` present.
2. `POST http://localhost:9226/session` with body
   `{"storage_state": {"cookies": [<exported list>], "origins": []}}`
   (a `{"cookies": {name: value}}` dict also works — it wraps each as
   `.facebook.com` domain).
3. The endpoint restarts the context, loads facebook.com, runs its own
   `isLoggedIn` check, and saves the session on success — response is
   `{"status":"ok","logged_in":true}`.

Same shape on LinkedIn :9225 / TikTok :9224 (`POST /session` with the
platform's cookie set — check `handleSetSession` in each `server.js`).

### Inject into the Playwright MCP browser (bridge → MCP)

Needed when the MCP browser must act *as* a logged-in user (e.g. Meta
developer console work) and the only live session is in a sidecar/bridge.
**`document.cookie` can write the *values* of httpOnly cookies** — the flag
blocks reads, not same-domain writes. Verified working 2026-09-21 with the
Meta dev console.

1. Pull the name→value map: sidecar `GET http://localhost:9226/debug/all-cookies`
   → `{cookies: {name: value, ...}}` (all domains mixed together).
2. Filter to the names the target domain needs (for facebook.com keep
   `c_user`, `xs`, `datr`, `fr`, `sb`, `wd`, `dpr`, `presence`, `locale` —
   skip `m_pixel_ratio`, checkpoint junk, and any `*.tiktok.com` etc values).
3. In the MCP browser: `browser_navigate` to the target domain first (cookie
   writes are domain-scoped — you must be ON facebook.com to set its cookies),
   then `browser_evaluate`:
   ```js
   () => {
     const c = {c_user: "...", xs: "...", datr: "...", fr: "...", sb: "..."};
     for (const [k, v] of Object.entries(c))
       document.cookie = `${k}=${v}; path=/; domain=.facebook.com; secure`;
     return document.cookie.length;
   }
   ```
4. `browser_navigate` to the protected page — it loads logged in. The MCP
   profile persists the cookies, so later navigations stay authenticated.

This browser is contention-free (unlike the shared bridge's X-Platform
busy-hold) — prefer it for long multi-step console automation.

### Bridge contention (X-Platform busy-hold)

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

### Caveats

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
- **FB profile picker ("Continue as X") is a soft logout** — the sidecar
  reports `logged_in: false, profile_picker: true`. Synthetic JS clicks
  (`el.click()`, dispatched MouseEvents) are ignored — the picker needs a
  trusted click (noVNC) or a credential login. Easier path: transplant
  fresh `c_user`/`xs` cookies from the MCP browser if it's logged in.
- **MCP cookie export**: `browser_run_code_unsafe` takes `async (page) => …`
  (single `page` arg, NOT `{page}`), runs as an expression, and has no
  `require` — return `JSON.stringify(await page.context().cookies([...]))`
  and save the result host-side. The tool result is a *double-escaped* JSON
  literal — decode the outer string first (`raw_decode` at the `"` after
  `### Result`), then parse the array inside.
- **Healed sessions still 401 via `/api/v1/profile` — stale DB state.**
  For FB personal profile reads the backend does NOT use the live sidecar
  session: `_get_facebook_sidecar()` re-injects
  `social_accounts.meta_data.browser_storage_state` into the sidecar on
  every call. Facebook rotates `xs`, so a stored state older than its
  rotation window is dead even when the sidecar itself is logged in.
  Symptom: sidecar `GET /session` → `logged_in:true` but the API returns
  `401 {"error":"Not logged in to Facebook"}`.
  Fix — after any transplant also refresh the stored state:
  ```python
  meta["browser_storage_state"] = {"cookies": fresh_cookies,
                                   "origins": old.get("origins", [])}
  # UPDATE social_accounts SET meta_data=$1::jsonb WHERE id=<acct uuid>
  ```
  (asyncpg inside `social-api`; fresh cookies = the MCP export list).
  Verified 2026-09-23: profile read returned 200 immediately after.
- **Check the MCP profile before any credential login.** Its persistent
  profile (`/home/pwuser/.playwright-data/profile`) holds long-lived
  sessions — on 2026-09-23 it still had a fully working FB session
  (`c_user`+`xs`+`datr`+`fr`+`sb`) weeks after the sidecar/bridge died.
  Export from it first; only fall back to credentials if `c_user`/`xs`
  are absent — a weak export (missing `datr`/`sb`) bounces straight back
  to the profile picker.

### Threads bootstrap via live Instagram session

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

### Related

- `session-ops` — triage which layer failed before transplanting
- `browser-ops` — keep the bridge warm so sessions stay alive
- `session-ops` — when no live session exists anywhere to copy

## Social Session Bootstrap — Master Guide

End-to-end guide for bootstrapping all social media bot sessions through
SocialAuto. Covers every manual step needed to activate 24/7 bot coverage
across all platforms, with the Playwright MCP server as the primary login
mechanism.

### When to use

- Setting up SocialAuto bots for the first time
- After a platform session has expired and bots stopped responding
- After adding a new social account that needs login/verification
- Debugging why a specific platform's DM bot isn't responding

### Prerequisites

1. SocialAuto stack running (`docker compose ps` shows all containers healthy)
2. Playwright MCP server configured in `.devin/mcp_config.json`
3. Browser-novnc container running (port 9223 bridge, port 6080 noVNC)
4. Admin credentials in `.env` (`SOCIAL_ADMIN_EMAIL` / `SOCIAL_ADMIN_PASSWORD`)
5. SocialAuto API healthy (`curl http://localhost:8083/health`)

### Platform activation checklist

| # | Platform | Method | Skill | Status after setup |
|---|----------|--------|-------|-------------------|
| 1 | Twitter/X | Playwright MCP login → cookie inject | `session-ops` | DM polling every 5 min |
| 2 | TikTok | Playwright MCP login → cookie inject | `session-ops` | DM polling every 5 min |
| 3 | Threads | Playwright MCP login → cookie inject | `session-ops` | DM polling every 3 min |
| 4 | Instagram | OAuth reconnect + App Review | `instagram-ops` | DM polling every 3 min |
| 5 | WhatsApp | Phone verification via API | `whatsapp-ops` | Webhook-based 24/7 |
| 6 | LinkedIn | Sidecar session refresh | `browser-ops` | DM polling every 6 min |
| 7 | Facebook Page | Webhook setup | `messenger-ops` | Webhook-based 24/7 |
| 8 | Facebook Personal | Browser bridge login | `browser-ops` | DM polling every 2 min |

### Bootstrap order

Run these in order — some steps depend on earlier ones:

#### Phase 1: Browser session logins (Twitter, TikTok, Threads)

These use the Playwright MCP server to log in and inject cookies into the
browser-novnc container.

```bash
## 1a. Twitter/X
## Use the playwright-mcp-login skill:
##   1. Navigate to https://x.com/i/flow/login
##   2. Fill email + password (from SocialAuto secrets)
##   3. Handle 2FA if needed
##   4. Extract cookies
##   5. Inject into browser-novnc
##   6. Verify session
python3 .devin/skills/session-ops/playwright-mcp-login/scripts/check-session.py twitter

## 1b. TikTok
## Use the playwright-mcp-login skill:
##   1. Navigate to https://www.tiktok.com/login/phone-or-email/email
##   2. Fill email + password
##   3. Handle captcha if needed
##   4. Extract + inject cookies
##   5. Verify session
python3 .devin/skills/session-ops/playwright-mcp-login/scripts/check-session.py tiktok

## 1c. Threads (uses Instagram credentials)
## Use the playwright-mcp-login skill:
##   1. Navigate to https://www.threads.com/login
##   2. Fill Instagram username + password
##   3. Handle 2FA if needed
##   4. Extract + inject cookies
##   5. Verify session
python3 .devin/skills/session-ops/playwright-mcp-login/scripts/check-session.py threads
```

#### Phase 2: Instagram OAuth reconnection

```bash
## 2a. Check token status
python3 .devin/skills/instagram-ops/instagram-token-reconnect/scripts/check-token-status.py

## 2b. Reconnect invalid tokens via OAuth
python3 .devin/skills/instagram-ops/instagram-token-reconnect/scripts/reconnect-oauth.py <account_id>

## 2c. Validate the new token
python3 .devin/skills/instagram-ops/instagram-token-reconnect/scripts/validate-token.py <account_id>
```

#### Phase 3: WhatsApp phone verification

```bash
## 3a. Check current status
python3 .devin/skills/whatsapp-ops/whatsapp-phone-verify/scripts/check-phone-status.py <account_id>

## 3b. Request verification code (SMS or voice)
python3 .devin/skills/whatsapp-ops/whatsapp-phone-verify/scripts/request-code.py <account_id> SMS el_GR

## 3c. Verify the code (user provides the 6-digit code from SMS)
python3 .devin/skills/whatsapp-ops/whatsapp-phone-verify/scripts/verify-code.py <account_id> 123456

## 3d. Register the number
python3 .devin/skills/whatsapp-ops/whatsapp-phone-verify/scripts/register-phone.py <account_id> 123456

## 3e. Verify registration
python3 .devin/skills/whatsapp-ops/whatsapp-phone-verify/scripts/check-phone-status.py <account_id>
```

#### Phase 4: LinkedIn session refresh

```bash
## 4a. Check sidecar session
curl -s http://localhost:9225/session/validate | python3 -m json.tool

## 4b. Trigger session refresh task
docker compose exec -T social-worker-default celery -A app.worker.celery_app call \
  app.worker.tasks.linkedin_session_refresh.refresh_linkedin_sessions

## 4c. If rate-limited, wait for cooldown (6h) or inject session manually
## See linkedin-sidecar-ops skill
```

#### Phase 5: Verify all polling tasks

After all sessions are active, trigger each polling task manually to verify
they can now read/send messages:

```bash
## Trigger all 6 polling tasks
docker compose exec -T social-worker-messenger celery -A app.worker.celery_app call \
  app.worker.tasks.personal_messenger.poll_personal_messenger

docker compose exec -T social-worker-messenger celery -A app.worker.celery_app call \
  app.worker.tasks.instagram_messenger.poll_instagram_messenger

docker compose exec -T social-worker-messenger celery -A app.worker.celery_app call \
  app.worker.tasks.threads_messenger.poll_threads_messenger

docker compose exec -T social-worker-messenger celery -A app.worker.celery_app call \
  app.worker.tasks.twitter_messenger.poll_twitter_messenger

docker compose exec -T social-worker-messenger celery -A app.worker.celery_app call \
  app.worker.tasks.tiktok_messenger.poll_tiktok_messenger

docker compose exec -T social-worker-messenger celery -A app.worker.celery_app call \
  app.worker.tasks.linkedin_messenger.poll_linkedin_messenger
```

### Account IDs reference

| Platform | Account ID (prefix) | Display name |
|----------|---------------------|-------------|
| Instagram Business | c1e2d99b | @cloudless.gr |
| Instagram Personal | d4e670ac | Themistoklis |
| LinkedIn Personal | 18d5cd59 | Themistoklis |
| LinkedIn Business | 58706dd3 | cloudless.gr |
| Twitter | 8af5fe23 | Themistoklis |
| TikTok | 08418574 | cloudless.gr |
| WhatsApp 1 | 77f17091 | Cloudless 1 |
| Threads | 96e9adaa | Cloudless |

### Troubleshooting

| Symptom | Check | Fix |
|---------|-------|-----|
| Polling task skips account | Browser session not active | Run login via Playwright MCP |
| `InvalidToken` / `Incorrect padding` | Instagram token corrupted | Reconnect via OAuth |
| `403 Forbidden` | Twitter API tier / session | Use browser bridge instead of API |
| `404 Not Found` | TikTok API not in EU | Use browser bridge instead of API |
| `NOT_VERIFIED` | WhatsApp phone not verified | Run phone verification flow |
| Rate-limited | LinkedIn session cooldown | Wait 6h or inject fresh session |
| `(#3) Application does not have capability` | Missing App Review permission | Submit for Meta App Review |

### Related skills

- `session-ops` — Login to platforms via Playwright MCP
- `whatsapp-ops` — WhatsApp phone verification
- `instagram-ops` — Instagram OAuth reconnection
- `browser-ops` — Browser-novnc bridge operations
- `browser-ops` — LinkedIn sidecar operations
- `threads-ops` — Threads account management
- `developer-apps-ops` — Meta App Review submission
- `social-stack-ops` — Docker Compose stack operations

## Playwright MCP Login — Social Platform Session Bootstrap

Log into social platforms (Twitter/X, TikTok, Threads, Instagram) through the
**Playwright MCP server** (not the browser-novnc bridge), extract the session
cookies, and inject them into the **browser-novnc container** (port 9223) so the
SocialAuto polling tasks can read/send DMs 24/7.

### When to use

- The Twitter/X DM bot needs a logged-in `x.com` session but the free API tier
  blocks DM access (403).
- The TikTok DM bot needs a logged-in `tiktok.com` session because the Business
  Messaging API is not available in the EU.
- The Threads DM bot needs a logged-in `threads.com` session because the
  Threads API has no DM endpoints.
- Any platform where the browser-novnc session has expired and the user wants
  to re-login programmatically via the Playwright MCP server instead of
  manually via noVNC.

### Architecture

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

### Playwright MCP server

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

### Credential sources

Credentials are stored in the SocialAuto secret store at `/api/v1/secrets`.
The agent retrieves them at login time — they are never written to disk or
committed to git.

| Platform | Secret keys | Email |
|----------|------------|-------|
| Twitter/X | (user-provided) | `baltzakis.themis@gmail.com` |
| TikTok | (user-provided) | `baltzakis.themis@gmail.com` |
| Threads | `INSTAGRAM_USERNAME`, `INSTAGRAM_PASSWORD` | Instagram username |
| Instagram | `INSTAGRAM_USERNAME`, `INSTAGRAM_PASSWORD` | Instagram username |

### Login flows

#### Twitter/X (`x.com/i/flow/login`)

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

#### TikTok (`tiktok.com/login`)

TikTok uses a standard form with email/password:

1. Navigate to `https://www.tiktok.com/login/phone-or-email/email`
2. Switch to email login if phone is default
3. Fill email and password fields
4. Click Login
5. Handle possible captcha (pause for manual completion via noVNC if needed)
6. Wait for redirect to `tiktok.com/foryou` — login successful

#### Threads (`threads.com/login`)

Threads uses Instagram identity:

1. Navigate to `https://www.threads.com/login`
2. Fill Instagram username and password
3. Click Log in
4. Handle possible 2FA (pause for manual completion if needed)
5. Wait for redirect to `threads.com/` — login successful

### Cookie extraction

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

### Cookie injection into browser-novnc

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

#### Alternative: Shared profile

The Playwright MCP profile at `~/.playwright-data/profile` persists across
sessions. If the browser-novnc container can mount the same profile, sessions
are shared automatically. However, the browser-novnc container uses its own
profile, so cookie injection is the primary method.

### Platform-specific notes

| Platform | 2FA | Captcha | Session duration |
|----------|-----|---------|-----------------|
| Twitter/X | Possible | Possible | ~1 year |
| TikTok | Possible | Common | ~30 days |
| Threads | Possible | Rare | ~90 days |
| Instagram | Possible | Rare | ~90 days |

If 2FA or captcha appears, the agent should pause and ask the user to complete
it manually via the noVNC viewer at `http://localhost:6080/vnc.html`.

### Scripts

- `scripts/extract-cookies.py` — Extract cookies from the Playwright MCP browser
  via `browser_evaluate` and save to a JSON file
- `scripts/inject-cookies.py` — Inject cookies into the browser-novnc container
  by navigating to the platform and setting cookies via the bridge API
- `scripts/check-session.py` — Check if a platform session is active in the
  browser-novnc container by navigating and checking login state

### Related skills

- `browser-ops` — Operate the browser-novnc bridge (port 9223)
- `threads-ops` — Threads account management and OAuth
- `developer-apps-ops` — Twitter/X OAuth 2.0 configuration
- `tiktok-console-ops` — TikTok content publishing
- `instagram-ops` — Instagram account setup
- `whatsapp-ops` — WhatsApp phone verification

## noVNC Manual Login Helper

Complete manual logins for platforms with anti-automation measures
(Twitter/X, Threads, Instagram) through the browser-novnc container's
noVNC viewer, then verify and persist the session.

### When to use

- Twitter/X login is blocked by the "knowledge check" anti-automation step
- Threads login form returns "Something went wrong" on programmatic submit
- Any platform detects Playwright automation and blocks the login
- A platform requires captcha, 2FA, or interactive consent that can't
  be handled programmatically

### Architecture

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

### noVNC viewer URL

```
http://localhost:6080/vnc.html?autoconnect=1&resize=scale
```

Or via the bridge:

```
GET http://localhost:9223/novnc-url
```

### Login URLs per platform

| Platform | Login URL | Success indicator |
|----------|-----------|-------------------|
| Twitter/X | `https://x.com/i/flow/login` | `x.com/home` in URL, no `login` link |
| Threads | `https://www.threads.com/login/` | `threads.com/` in URL, "Messages" in body |
| TikTok | `https://www.tiktok.com/login` | `tiktok.com/foryou` in URL |
| Instagram | `https://www.instagram.com/accounts/login/` | `instagram.com/` without `login` in URL |

### Workflow

#### Step 1: Start a browser session and navigate to the login page

```bash
python3 scripts/start-login.py <platform>
```

This starts a browser session in the browser-novnc container and
navigates to the platform's login page. It prints the noVNC URL for
the user to open.

#### Step 2: Complete the login manually via noVNC

Open the noVNC URL in your browser and complete the login:
- Type your email/username and password
- Solve any captcha or knowledge check
- Handle 2FA if prompted
- Wait for the page to load the authenticated home feed

#### Step 3: Wait for login to complete (optional — automated polling)

```bash
python3 scripts/wait-for-login.py <platform> [timeout_seconds]
```

Polls the browser bridge every 5 seconds and checks if the session is
active. Returns exit code 0 when logged in, 1 on timeout.

Default timeout: 300 seconds (5 minutes).

#### Step 4: Verify the session and extract cookies

```bash
python3 scripts/verify-session.py <platform>
```

Confirms the session is active and saves cookies to the browser-novnc
container's cookie store for persistence across restarts.

#### Full automated flow

```bash
## Start login and get noVNC URL
python3 scripts/start-login.py twitter

## User completes login in noVNC (manual)

## Poll until logged in (auto-detects)
python3 scripts/wait-for-login.py twitter 600

## Verify and persist
python3 scripts/verify-session.py twitter
```

### Scripts

- `scripts/start-login.py` — Start browser session and navigate to login page
- `scripts/wait-for-login.py` — Poll until login is detected
- `scripts/verify-session.py` — Verify session and save cookies
- `scripts/check-session.py` — Check if session is currently active (alias of playwright-mcp-login's check)

### Browser bridge API

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

### Related skills

- `session-ops` — Login via Playwright MCP (for platforms without anti-automation)
- `browser-ops` — Browser bridge operations
- `session-ops` — Master session verification
