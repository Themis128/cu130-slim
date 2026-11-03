---
name: operator-gate-ops
description: Automate the three residual operator-only gates via the user's real logged-in Chrome (CDP :9222) + omv mailbox — FB OAuth reconnect, Email Routing destination verify, and CLOUDFLARE_WORKERS_DEPLOY_TOKEN mint. Companion to consent-root-ops and cf-worker-bringup-ops. Use for "automate the manual steps", FB OAuth, Email Routing destination, deploy-token mint.
---

# operator-gate-ops

Drives the user's real Chrome over CDP `:9222` (same mechanism as
`consent-root-ops` — `scripts/start-chrome-debug.bat`, one-time +
one login to Facebook/dashboard). `scripts/gate_runner.py <gate>`:

| Gate | Command | What it automates | True residual |
|---|---|---|---|
| FB OAuth | `gate_runner.py fb-token <authorize_url>` | navigates logged-in Chrome → captures `access_token` from redirect → writes omv `~/secrets/fb-user-token` + SocialAuto `secret_store` — daily `fb-token-heal.sh` + #1309 worker take over | none (needs FB logged-in in debug Chrome) |
| Email Routing | `gate_runner.py email-routing` | opens destination-addresses dash, best-effort DOM add, then polls omv maildir for the CF verify link + GETs it → verified | dashboard DOM may need a manual "add" click if the SPA changed |
| Deploy token | `gate_runner.py deploy-token` | opens the API-tokens page + pre-clicks Create; emits the `gh secret set` line | token is shown once — paste into the emitted `gh secret set` (2nd click can't be automated without exposing it) |

## After each gate

- FB: `docker compose exec -T social-api python3 -c "from app.services.secret_store import get; print(bool(get('FB_USER_TOKEN')))"`; page token self-renews next 8:30 cron.
- Email Routing: retest `mail-outbound /send` (`{"ok":true}` = live; still fails on arbitrary recipients → Resend stays primary).
- Deploy token: `gh workflow run cloudflare-deploy.yml` goes green.

## Related

- `consent-root-ops` — the CDP mechanism + Chrome-debug launcher
- `cf-worker-bringup-ops` — the API-side probes these gates unlock
- `mail-code-fetch` — mailbox code/link polling (used by the verify follower)
