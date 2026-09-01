---
name: socialauto-accounts
description: >-
  Manage social accounts in SocialAuto: list connected accounts, check OAuth
  status, validate tokens, refresh tokens, sync business accounts (Facebook Pages,
  Instagram Business), and test account connectivity. Covers /api/v1/accounts/*
  endpoints. Use when checking which social platforms are connected, refreshing
  expired tokens, or syncing Facebook/Instagram business accounts.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# SocialAuto Accounts

Manage connected social media accounts and OAuth tokens.

## When to use

- List all connected social accounts and their status
- Check if an account token is valid
- Refresh an expired token
- Sync Facebook Pages or Instagram Business accounts
- Test account connectivity
- Get account details (display name, platform, type, follower count)

## API base

```
http://127.0.0.1:8083/api/v1/accounts
```

## Authentication

Bearer token from `POST /api/v1/auth/login`.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `` | List all connected accounts |
| GET | `/{id}` | Get a single account |
| POST | `/{id}/test` | Test account connectivity |
| POST | `/{id}/refresh` | Refresh OAuth token |
| GET | `/{id}/validate` | Validate token and permissions |
| POST | `/{id}/sync-business-accounts` | Sync Facebook Pages / IG Business |
| POST | `/{id}/set-business-account` | Set active business account |
| POST | `/linkedin/sync-organizations` | Sync LinkedIn organizations |
| POST | `/connect/{platform}` | Get OAuth connect URL |

## Supported platforms

| Platform | Account types | Token refresh |
|----------|--------------|---------------|
| LinkedIn | person, organization | No expiry |
| Twitter/X | person | 2h tokens, refreshed hourly |
| Facebook | user, page | ~60 day long-lived |
| Instagram | business | ~60 day long-lived |
| Threads | person | ~60 day long-lived |
| TikTok | person | 24h tokens, refreshed daily |

## Auto token refresh

A Celery beat task runs every hour at :15 past and refreshes tokens expiring
within 4 hours. If a refresh fails, the account is marked `expired` and
requires manual reconnect from the Accounts page.

## Tool scripts

Run from repo root `cu130-slim/`:

```bash
# List all connected accounts
.devin/skills/socialauto-accounts/scripts/list-accounts.sh

# Get account details
.devin/skills/socialauto-accounts/scripts/get-account.sh <account-id>

# Test account connectivity
.devin/skills/socialauto-accounts/scripts/test-account.sh <account-id>

# Refresh account token
.devin/skills/socialauto-accounts/scripts/refresh-account.sh <account-id>

# Validate account token and permissions
.devin/skills/socialauto-accounts/scripts/validate-account.sh <account-id>

# Sync Facebook/Instagram business accounts
.devin/skills/socialauto-accounts/scripts/sync-business.sh <account-id>
```

## Cloudless.gr account IDs

These are the connected accounts for cloudless.gr (check with list-accounts.sh
for current IDs):

| Platform | Display name | Type |
|----------|-------------|------|
| LinkedIn | cloudless.gr | organization (Company Page) |
| LinkedIn | Themistoklis Baltzakis | person |
| Facebook | cloudless.gr | page |
| Instagram | (business) | business |
| TikTok | cloudless.gr | person |
| Twitter/X | Themistoklis Baltzakis | person |
| Threads | (person) | person |

## Important notes

- LinkedIn tokens do not expire (no `expires_in` returned).
- TikTok requires HTTPS redirect URIs.
- Facebook stores both user and page accounts; the user token is needed
  for Sync Business.
- If `Sync Business` fails with `(#100) Tried accessing nonexisting field`,
  the stored token is a Page token — disconnect and reconnect Facebook.
