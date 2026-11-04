---
name: facebook-token-ops
description: Facebook/Threads Graph-token health as a platform worker — `app.worker.tasks.facebook_token_check` (6h beat, /debug_token introspection, P2 Telegram alert on expired or <7-day-left tokens). Replaces the legacy external "FB heal" monitor. Use for FB token invalid/expired alerts, 6h health cadence, or locating the canonical token-refresh lane vs legacy emitters.
---

# facebook-token-ops

Worker: `app/worker/tasks/facebook_token_check.py`
Beat: `facebook-token-check` every 6h (`queue: default`).
State: `social_accounts.meta_data.fb_token_checked_at` + `fb_token_invalid`.
Alerts: `send_telegram_alert("ops", …)` → #ops P2.

## What it does
- Introspects every `facebook`/`threads` account via Graph `/debug_token`
  (token + app_access_token from `meta_data`).
- `is_valid:false` → P2 alert "reconnect via Accounts UI OAuth" + marks
  `fb_token_invalid`.
- `expires_at` within 7d → P2 expiring-soon alert.
- FB tokens have NO refresh flow — reconnect is always manual (unlike IG's
  `instagram_token_refresh` 60-day auto-refresh).

## Legacy emitter to retire
The recurring `FB heal: token invalid/expired` Telegram spam came from a
standalone monitor (Windows/omv cron — not in-repo). Once this task is
live, find + kill the legacy job: `crontab -l` on omv/omv-ha, Task
Scheduler on Windows — anything calling an FB-token check + Telegram.
The platform worker is now the canonical lane.

## Related
- `instagram_token_refresh` (daily, auto-refreshes EAA/IGQ tokens)
- `session_healer` (browser-sidecar layer — independent of OAuth)
- `synigoros_status` (daily Ombudsman poll — same platform-monitor pattern)
