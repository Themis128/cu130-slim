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
| Email Routing | `gate_runner.py email-routing` | Dashboard DOM: add destination `tbaltzakis@cloudless.gr` → polls omv maildir for the CF verify link → GETs it → verified. Retest `/send`. |
| Deploy token | `gate_runner.py deploy-token` | Dashboard wizard: create `ci-workers-deploy-auto` → captures the shown-once token from DOM → `gh secret set` opaquely (never printed). If the SPA wizard stalls mid-flow it reports the exact point. |

## After each gate

- FB: Accounts are healthy; `facebook_token_check` (6h beat) alerts if a
  token drifts again.
- Email: `POST /send` returns `{"ok":true}` when live.
- Deploy: next `cloudflare-deploy.yml` run goes green.

## Related

- `consent-root-ops` — the CDP mechanism + debug-profile launcher
- `cf-worker-bringup-ops` — the API-side probes these gates unlock
