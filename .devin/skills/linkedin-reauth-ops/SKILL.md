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
