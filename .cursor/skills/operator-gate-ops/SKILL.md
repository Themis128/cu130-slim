---
name: operator-gate-ops
description: Full automation for the three operator gates via Chrome CDP :9222 — FB OAuth token capture+write, Email Routing destination add + mailbox verify-link follow, and CLOUDFLARE_WORKERS_DEPLOY_TOKEN mint + opaque gh secret set. Use for "automate the manual steps", FB OAuth, Email Routing, deploy token.
---

# operator-gate-ops

`scripts/gate_runner.py <gate>` — end-to-end automation of the three
gates that used to need a human, via Chrome CDP `:9222`.

## Profiles

- **debug (default)** — `chrome-debug-profile`; safe, own logins (persist).
  One-time: open facebook.com + dash.cloudflare.com in it and log in.
- **--real** — kills all chrome and relaunches the REAL profile with CDP.
  Already logged in everywhere — zero-login path. Destructive (closes
  user tabs); `--restore` relaunches a normal chrome after.

## Gates

| Gate | Command | Fully automated path |
|---|---|---|
| FB token | `gate_runner.py fb-token <oauth_authorize_url>` | Chrome auto-launches → OAuth authorize → token captured from redirect → written to omv `~/secrets/fb-user-token` + SocialAuto `secret_store`. Daily `fb-token-heal.sh` + `facebook_token_check` take over. |
| Email Routing | `gate_runner.py email-routing` | Dashboard DOM: add destination `tbaltzakis@cloudless.gr` → polls the destination-addresses row for `Verified` (maildir grep kept as best-effort — inbound mail routes to mail-ingest → webmail ingest, not omv Maildir). Retest `/send`. |
| Deploy token | `gate_runner.py deploy-token` | Dashboard wizard: create `ci-workers-deploy-auto` → captures the shown-once token from DOM → `gh secret set` opaquely (never printed). If the SPA wizard stalls mid-flow it reports the exact point. |

## Companion tools

| Tool | Purpose |
|---|---|
| `scripts/fb_secret_reveal.py` | Meta App Secret capture — Meta's "Show" needs a password re-entry (the one human step). Script opens the dialog in the debug Chrome, waits for the operator to type the password, then captures the revealed secret opaquely → `PUT /api/env/FACEBOOK_CLIENT_SECRET` via env-manager → verifies `/oauth/facebook/authorize` emits a URL. `FACEBOOK_CLIENT_ID` (= App ID `1936126137016578`, `Cloudless` app) is already in `.env`. |
| `scripts/fb_gate_one_shot.py` | Whole FB gate in one command — waits for the operator's facebook.com login (auto-detects), stages Show, toasts, captures the secret. Run + forget. |
| `scripts/fb_secret_ask.py` | Stages the Show dialog + Windows toast/beep when already logged in — tells the operator the exact moment to type. |
| `scripts/fb_cred_inject.py` | One-paste mode — local password box captures the FB password in-memory only, auto-types it into the FB login form AND the Meta re-auth dialog, discards it, captures the secret. Minimum possible human surface. |
| `scripts/post_merge_restart.py` | Post-merge stack restart — watches given PRs (default: the worker-wiring set `1302 1308 1309 1301`) via `gh pr view`, and when each merges runs `docker compose restart social-api social-worker-* celery-beat` + health/ping check. Run in background: `python scripts/post_merge_restart.py`. |

## After each gate

- FB: Accounts are healthy; `facebook_token_check` (6h beat) alerts if a
  token drifts again.
- Email: `POST /send` returns `{"ok":true}` when live.
- Deploy: next `cloudflare-deploy.yml` run goes green.

## Related

- `consent-root-ops` — the CDP mechanism + debug-profile launcher
- `cf-worker-bringup-ops` — the API-side probes these gates unlock
