---
name: session-health-ops
description: One-shot health check across all SocialAuto session types — OAuth tokens in Postgres, the shared browser-novnc bridge (9223), and the per-platform sidecars (TikTok 9224, LinkedIn 9225, Facebook 9226, Messenger 9230). Covers interpreting session states, what auto-heals vs needs manual login, and the scripts/session_health.py tool. Use when bots stop responding, publishes fail with auth errors, or after restarting the stack.
---

# Session health operations

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

## Ops tool

```bash
python3 scripts/session_health.py          # full report
python3 scripts/session_health.py --json   # machine-readable
```

Checks OAuth account rows, the bridge `/health` + `/session/status`, and
each sidecar `/health`; exits 1 on hard failures and prints a
"Needs attention" section.

## Interpretation matrix

| Symptom | Layer | Fix |
|---|---|---|
| account `expired`, has refresh token | OAuth | None — hourly task retries unconditionally; verify next run or trigger `celery call app.worker.tasks.token_refresh.refresh_expiring_tokens` |
| account `expired`, no refresh token | OAuth | Manual reconnect via Accounts page OAuth |
| bridge session `status: idle` / wrong platform | Bridge | `POST /session/start {"platform": "<p>"}`; if 409 busy, another platform holds it — wait or `force:true` for manual recovery |
| x.com publish fails `402 credits depleted` | X billing, NOT auth | Browser fallback engages automatically; session must be logged in (see `twitter-browser-ops`) |
| sidecar `/health` down | Sidecar | `docker compose restart <sidecar>`; check `docker compose ps` |
| bridge session `done` but page on wrong site | Bridge | Cosmetic — busy-hold owns the page; next tagged op navigates back |
| bridge sessions lost after `docker compose up -d --force-recreate browser-novnc` | Bridge storage | Mount the `browser_profile` volume (`patchright-ops` skill); otherwise the Chromium profile lives in the container layer and is wiped on recreate |

## Refresh/recovery entry points

- Twitter/X browser login: `python3 scripts/twitter_browser_login.py`
  (two-step flow, see `twitter-browser-ops`)
- LinkedIn sidecar session: `linkedin-sidecar-ops` skill (weekly refresh
  task exists; manual login via noVNC if dead)
- Personal Messenger / Threads / Instagram browser sessions:
  `browser-bridge-ops` + `playwright-mcp-login` skills
- OAuth reconnect: `social-oauth-ops` skill

## Notes

- OAuth token health and browser-session health are independent — e.g.
  Twitter's OAuth refresh works fine while x.com API publishing is
  billing-blocked, and the browser session is what the fallback needs.
- Never expose the bridge publicly; all session endpoints are internal.
