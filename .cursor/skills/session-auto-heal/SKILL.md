---
name: session-auto-heal
description: Self-healing session automation that keeps every social platform connected to SocialAuto — hourly Celery sweep (session_healer.heal_sessions) plus on-demand POST /api/v1/ops/session-heal. Probes LinkedIn/Facebook sidecars and all shared-bridge platforms, recovers sessions via cookie re-inject, credential login, or Threads IG-SSO bootstrap, persists fresh session material to Postgres, and Slack-alerts only when human action is needed. Use when sessions keep dying, when pollers report "browser session not logged in", or to understand/trigger the automatic recovery ladder.
---

# Session auto-heal

`app.services.session_healer.heal_all_sessions()` keeps every social
platform connected without babysitting. Scheduled hourly at :20 via
celery-beat (`heal-sessions` → `social-worker-default` queue) and callable
on demand:

```bash
# on-demand run (admin token required — returns the full status map)
curl -X POST https://social.cloudless.gr/api/v1/ops/session-heal \
  -H "Authorization: Bearer $TOKEN"

# or inside the stack, no auth needed
docker compose exec -T social-api python3 -c "
import asyncio; from app.services.session_healer import heal_all_sessions
print(asyncio.run(heal_all_sessions()))"
```

A distributed Redis lock (`session_healer:lock`, 25min) prevents
overlapping runs — a second call returns `{skipped: ...}`.

## Recovery ladder

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

## Persistence

Healthy sessions are written back automatically:

- Sidecars → `meta_data.browser_storage_state` on every account of the
  platform (Playwright storage_state shape), plus `LINKEDIN_COOKIE` in
  the secret store for LinkedIn.
- Bridge → `POST /session/extract` writes the durable
  `/app/cookies/<platform>_*.json` volume files; the name→value jar is
  also synced to `meta_data.browser_cookies`.
- `meta_data.session_healed_at` records the last heal timestamp.

## Alerting

Slack via `post_alert_to_slack`, deduped per platform for 24h
(`session_healer:alerted:<platform>` in Redis). Two alert types:

- `needs_manual_login` / `pending_2fa` / `sidecar_down` — human required
- one-shot "is back / recovered" notice when a previously-alerted
  platform heals

Status values in the summary map: `healthy`, `recovered`, `contended`,
`rate_limited`, `pending_2fa`, `needs_manual_login`, `sidecar_down`,
`error`. `unhealthy[]` lists everything needing attention; empty = all
green.

## Verified live 2026-09-30

```json
{"linkedin": "healthy", "facebook_sidecar": "healthy",
 "twitter": "healthy", "facebook/instagram/threads/tiktok": "contended",
 "unhealthy": []}
```

## Gotchas

- LinkedIn `POST /login` retries each send a fresh push/SMS — the healer
  parks `session_healer:linkedin_pending_2fa` (24h) instead of spamming.
- The bridge is a single Chromium context — healing platform X briefly
  preempts platform Y's live page. The hourly cadence keeps this cheap.
- Never log cookie values; logs show names/counts only.
- Secrets read via `secret_store` fall back to `.env` — keep
  `LINKEDIN_PASSWORD`, `INSTAGRAM_USERNAME/PASSWORD`,
  `TWITTER_LOGIN_PASSWORD`, `TIKTOK_SESSION_ID` populated for
  credential-driven recovery to work.

## Related

- `.devin/skills/session-transplant/SKILL.md` — manual cookie moves
- `.devin/skills/session-health-ops/SKILL.md` — one-shot health report
- `.devin/skills/linkedin-sidecar-ops/SKILL.md` — sidecar internals,
  2FA quirk notes
