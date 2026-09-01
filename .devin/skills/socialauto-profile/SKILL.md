---
name: socialauto-profile
description: >-
  Manage social media profile metadata through SocialAuto: read profile,
  update bio/headline/about, upload profile picture and cover photo, and
  log in to private API or browser sessions. Covers /api/v1/profile/*
  endpoints. Use when editing profile info, avatars, banners, or bio text
  for Instagram, Facebook, LinkedIn, Twitter/X, and TikTok.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# SocialAuto Profile

Update profile metadata (bio, avatar, cover, headline, etc.) for connected
social accounts through the unified `/api/v1/profile` API.

## When to use

- Read the current profile for any connected account
- Update bio, headline, about, name, website, location, phone
- Upload a profile picture or cover photo
- Log in to Instagram private API or browser sessions (Facebook/LinkedIn)
- Check profile field support per platform

## API base

```
http://127.0.0.1:8083/api/v1/profile
```

## Authentication

Bearer token from `POST /api/v1/auth/login`.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/{account_id}` | Read current profile |
| PUT | `/{account_id}` | Update profile fields |
| POST | `/{account_id}/picture` | Upload profile picture |
| POST | `/{account_id}/cover` | Upload cover/banner photo |
| POST | `/{account_id}/login` | Log in to private API or browser session |

## Platform support

| Platform | Read | Write fields | Picture | Cover | Login method |
|----------|------|--------------|---------|-------|--------------|
| Instagram | ✅ | bio, name, website, phone, email | ✅ | ❌ | `aiograpi-rest` (username+password) |
| Facebook Page | ✅ | about, description, website, phone | ✅ | ✅ | OAuth (already connected) |
| Facebook personal | ✅ | about | ✅ | ❌ | Playwright (username+password) |
| LinkedIn | ✅ | headline, about | ❌ | ❌ | Playwright (username+password) |
| Twitter/X | ✅ | name, bio, location, website | ✅ | ✅ | `tweepy` v1.1 API credentials |
| TikTok | ✅ | name/nickname, bio/signature | ✅ | ❌ | `tiktok-private-api` signing key |

## Scripts

Run from repo root `cu130-slim/`:

```bash
# Read a profile
.devin/skills/socialauto-profile/scripts/get-profile.sh <account-id>

# Update profile fields
.devin/skills/socialauto-profile/scripts/update-profile.sh <account-id> '{"about":"..."}'

# Upload a profile picture
.devin/skills/socialauto-profile/scripts/upload-picture.sh <account-id> <image-file>

# Upload a cover/banner photo
.devin/skills/socialauto-profile/scripts/upload-cover.sh <account-id> <image-file>

# Log in to Instagram / Facebook / LinkedIn private API or browser session
.devin/skills/socialauto-profile/scripts/login.sh <account-id> <username> <password>

# List accounts to find account IDs
.devin/skills/socialauto-accounts/scripts/list-accounts.sh
```

## Example update payloads

```bash
# Instagram / TikTok bio
.devin/skills/socialauto-profile/scripts/update-profile.sh <id> '{"biography":"Cloud consulting & AI marketing ☁️"}'

# LinkedIn headline and about
.devin/skills/socialauto-profile/scripts/update-profile.sh <id> '{"headline":"Founder @ cloudless.gr","about":"Cloud consulting, serverless & AI marketing."}'

# Facebook personal about
.devin/skills/socialauto-profile/scripts/update-profile.sh <id> '{"about":"Cloud consulting & AI marketing"}'
```

## Important notes

- Profile updates may be ignored by platforms if the account does not have
  the required API tier or permissions. The response lists `updated_fields`
  and `ignored_fields`.
- Browser automation (Facebook/LinkedIn) requires a successful `/login` first
  to store cookies/session state in the account metadata.
- Twitter profile writes require a paid API tier (Basic/Pro) with v1.1
  credentials.
- The `aiograpi-rest` sidecar must be running for Instagram (`docker compose up -d instagram-private-api`).
