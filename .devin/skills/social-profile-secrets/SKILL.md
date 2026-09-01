---
name: social-profile-secrets
description: >-
  Save and manage credentials for social profile logins through SocialAuto's
  Cloudflare-first secret store with local PostgreSQL/.env failover. Use when
  storing Instagram, Facebook, LinkedIn, Twitter/X, or TikTok credentials so
  the app can log in and update profiles automatically.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# Social Profile Secrets

Store service and account credentials in the SocialAuto secret store
(`/api/v1/secrets`). The store uses **Cloudflare D1 as primary**, **local
PostgreSQL as failover**, and the local `.env` file as a final fallback.

## When to use

- Saving Instagram private API username/password
- Saving Facebook/LinkedIn browser automation credentials
- Saving Twitter/X v1.1 API key/secret and access tokens
- Saving TikTok private API signing key
- Listing, updating, or deleting saved secrets

## Supported secret keys

| Key | Platform | Used by |
|-----|----------|---------|
| `INSTAGRAM_USERNAME` | Instagram | `aiograpi-rest` sidecar |
| `INSTAGRAM_PASSWORD` | Instagram | `aiograpi-rest` sidecar |
| `FACEBOOK_USERNAME` | Facebook (personal) | Playwright browser login |
| `FACEBOOK_PASSWORD` | Facebook (personal) | Playwright browser login |
| `LINKEDIN_USERNAME` | LinkedIn (personal) | Playwright browser login |
| `LINKEDIN_PASSWORD` | LinkedIn (personal) | Playwright browser login |
| `TWITTER_API_KEY` | Twitter/X | `tweepy` v1.1 API |
| `TWITTER_API_SECRET` | Twitter/X | `tweepy` v1.1 API |
| `TWITTER_ACCESS_TOKEN` | Twitter/X | `tweepy` v1.1 API |
| `TWITTER_ACCESS_TOKEN_SECRET` | Twitter/X | `tweepy` v1.1 API |
| `TIKTOK_PRIVATE_API_KEY` | TikTok | `tiktok-private-api` signing server |

## Scripts

Run from repo root `cu130-slim/`:

```bash
# List saved secrets (values are masked)
.devin/skills/social-profile-secrets/scripts/list-secrets.sh

# Save a single secret
.devin/skills/social-profile-secrets/scripts/set-secret.sh INSTAGRAM_USERNAME cloudless_gr

# Get a raw secret value (admin/owner)
.devin/skills/social-profile-secrets/scripts/get-secret.sh INSTAGRAM_USERNAME

# Save Instagram credentials
.devin/skills/social-profile-secrets/scripts/set-instagram.sh cloudless_gr TH!123789th!

# Save Facebook browser credentials
.devin/skills/social-profile-secrets/scripts/set-facebook.sh baltzakis.themis@gmail.com TH!123789th!

# Save LinkedIn browser credentials
.devin/skills/social-profile-secrets/scripts/set-linkedin.sh user@example.com TH!123789th!

# Save Twitter v1.1 credentials
.devin/skills/social-profile-secrets/scripts/set-twitter.sh key secret token token_secret

# Save TikTok private API key
.devin/skills/social-profile-secrets/scripts/set-tiktok.sh key
```

## API base

```
http://127.0.0.1:8083/api/v1/secrets
```

## Authentication

Bearer token from `POST /api/v1/auth/login`.

## Important notes

- The secret store is **Cloudflare-first**: when `CLOUDFLARE_ACCOUNT_ID`,
  `CLOUDFLARE_API_TOKEN`, and `D1_SOCIAL_AUTOMATION_ID` are configured,
  secrets are written to the D1 `social_secrets` table.
- If D1 is unavailable, the service writes to the local `social_secrets`
  PostgreSQL table.
- The `.env` file is read as a final fallback but is **read-only from the
  `social-api` container**. Update `.env` through the Env Manager
  (`http://localhost:8080`) if needed.
- Never print or commit secret values. Use the scripts to avoid typing
  credentials into conversation.
- After saving credentials, call the profile login endpoint for the
  corresponding account:
  ```bash
  curl -X POST http://127.0.0.1:8083/api/v1/profile/{account_id}/login \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}'
  ```
