---
name: cloudflare-access-paths
description: >-
  Manage Cloudflare Access on social.cloudless.gr — which paths are
  gated behind the admin SSO and which are publicly bypassed for OAuth
  callbacks and third-party webhooks. Covers listing Access apps,
  creating per-path Bypass apps via the API, and verifying public
  reachability. Use when OAuth callbacks redirect to the Access login
  wall, Meta/Telegram/billing webhooks return 302/403, or when adding
  a new public endpoint to social.cloudless.gr.
allowed-tools:
  - read
  - exec
  - grep
triggers:
  - user
  - model
---

# Cloudflare Access — public paths on social.cloudless.gr

`social.cloudless.gr` is behind Cloudflare Access (Zero Trust). App
`socialauto-app` (id `ed95d3d9-2246-4d44-b15c-5c39170b00dd`) protects the
whole hostname — only the 4 admin emails pass.

## Key model

- Access matches the **most specific** application domain first. A path-scoped
  app (`host/path`) takes precedence over the hostname-wide app.
- "Public" means a **separate self-hosted app** on the same hostname scoped to
  the path, with a single `bypass` policy including `Everyone`. An `allow`
  policy with `Everyone` still runs the identity check — it does NOT open the
  path.
- Bypassed paths are fully public — the endpoint must authenticate itself
  (webhook verify token, HMAC signature, OAuth state).

## Current public (bypassed) paths

| Path prefix | App | Purpose |
|---|---|---|
| `/api/v1/health` | `socialauto-public` | monitoring |
| `/api/v1/auth/oauth/` | `socialauto-oauth-callbacks` (`e5dd7e52-ee37-449f-a03d-3de9fcb10248`) | OAuth callbacks + authorize for all platforms |
| `/api/v1/auth/data-deletion` | `socialauto-meta-data-deletion` (`e3f73ec9-d05a-44e4-bc44-dbc205b71a91`) | Meta data-deletion/deauthorize POSTs |
| `/api/v1/messenger/webhook` | (existing) | Meta Messenger verify+events |
| `/api/v1/whatsapp/webhook` | (existing) | Meta WhatsApp verify+events |
| `/api/v1/telegram/webhook/` | (existing) | Telegram webhooks |
| `/api/v1/billing/*` webhooks | (existing) | Polar/Dodo payment webhooks |

## Credentials

- `CLOUDFLARE_ACCESS_TOKEN` in `.env` — the only token that can read/write
  Access apps (account `fb7dc7b69b662480cd5961a4d1913c78`). It fails
  `user/tokens/verify` but works on `/access/*` endpoints.
- `CLOUDFLARE_API_TOKEN` — works for zone basics (zones list) but NOT Access.

## Tools

`scripts/cf_access.py` — list apps, probe paths, create bypass apps:

```bash
# List all Access apps (name | domain | decision)
python3 scripts/cf_access.py list

# Probe which paths reach origin vs Access wall
python3 scripts/cf_access.py probe /api/v1/messenger/webhook /api/v1/auth/data-deletion

# Create a bypass app for a path prefix
python3 scripts/cf_access.py bypass social.cloudless.gr/api/v1/example/webhook --name my-bypass
```

## How to verify reachability

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://social.cloudless.gr/<path>
```

- `302` to `cloudflareaccess.com` → Access-gated (browser would be redirected to SSO)
- `403` with **no redirect** → origin responded OR Access API-style deny — check
  with a known-good request (e.g. correct `hub.verify_token` for Meta webhooks)
- `4xx/5xx` app-shaped response (400/404/405/422) → reached origin ✓

## Gotchas

- Meta OAuth redirect URIs are registered against `social.cloudless.gr` — if the
  callback path isn't bypassed, the user's browser hits the Access login wall
  mid-flow. The `code` is single-use; the flow must be restarted.
- Meta data-deletion is POSTed server-to-server — no browser, no cookies. Must
  be bypassed or Meta's config check fails.
- Access returns `302` for browser-ish GETs and `403` for API-style requests on
  gated paths — both mean "blocked".
- Do NOT bypass `/api/v1/` broadly — admin endpoints must stay behind Access.
  Bypass only paths that third parties must reach (callbacks, webhooks) or that
  are explicitly public (health).
