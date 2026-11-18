---
name: linkedin-reauth-ops
description: Re-authenticate the LinkedIn OAuth surface when the stored token dies. `scripts/rw_ads_reauth.sh` drives the re-auth through the LinkedIn sidecar, refreshes the client-id/secret-backed token, and writes it back to the secret store. Use when LinkedIn publishes start failing with 401/expired_token.
---

# linkedin-reauth-ops

LinkedIn token recovery. The stack's LinkedIn token lives in the secret
store; when it expires or gets revoked, organic publishing 401s — the
re-auth script re-runs the OAuth handshake through the sidecar and stores
the fresh token.

## Scripts

- `scripts/rw_ads_reauth.sh` — the re-auth driver: pulls `LINKEDIN_CLIENT_ID`/
  `LINKEDIN_CLIENT_SECRET` from the secret store (via the social-worker
  env), kicks the OAuth flow through the sidecar (`LINKEDIN_SIDECAR`,
  default `http://localhost:9225`), and writes the resulting token back.
  Run inside the compose network (needs `docker exec social-worker-media`).

## Notes

- The sidecar must be up and logged into LinkedIn (the browser session).
- Marketing API access (ads) is a separate product tier — this re-auth
  covers the Community Management (organic) scope the app holds.
- After re-auth, re-run the publish health-check to confirm writes land.

## Browser-session reauth (sidecar `logged_in:false`)

A separate failure surface from the OAuth token: the sidecar's Playwright
session dies (`GET /session` → `logged_in:false`, URL on `/login?…`),
then every `post_*` publish call fails mid-flight. Recovery lanes:

- **Automated (2026-11-18+):** `_publish_linkedin_via_sidecar` runs a
  pre-publish session gate — on a dead session it calls
  `app/services/linkedin_reauth.py::run_reauth` (auto SMS-OTP via ADB)
  once, persists the fresh `storage_state` into
  `social_accounts.meta_data->>'browser_storage_state'`, and retries.
  Unrecoverable sessions return a soft deferral (`retry_after` +30min)
  instead of burning attempts.
- **API:** `POST /api/v1/linkedin/reauth {username,password,method:'sms',
  auto_sms:true}` (social-api). Creds resolve from secret store
  (`LINKEDIN_USERNAME`/`LINKEDIN_PASSWORD`, per-account suffixed
  variants) via `resolve_linkedin_credentials()`. Responds
  `logged_in:true` + `detail.storage_state`.
- **Critical:** persist the fresh `storage_state` to the account's
  `meta_data` (jsonb_set) — otherwise the next publish re-poisons the
  live session with the dead stored cookies and you're back here.
- **Cadence:** `check-linkedin-sessions` beat (12h) refreshes the session
  proactively; `heal-sessions` (hourly) re-injects `li_at` when the
  sidecar drops it. A LinkedIn **429 cooldown** kills sessions too —
  during the window, don't probe (it escalates); reauth only after it
  lifts (`/session/clear-rate-limit` for the sidecar's own breaker).
