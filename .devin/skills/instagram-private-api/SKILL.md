---
name: instagram-private-api
description: >-
  Manage Instagram private API login, challenge resolution, session
  persistence, and profile writes through the aiograpi-rest Docker
  sidecar. Use when logging in to Instagram, resolving 2FA/challenge,
  importing a sessionid cookie, saving/restoring settings, or writing
  profile fields (bio, picture, name, website) that the Graph API does
  not support.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# Instagram Private API (aiograpi-rest sidecar)

## Overview

The `aiograpi-rest` sidecar wraps the `aiograpi` async Instagram private
mobile API as a RESTful HTTP service. It enables profile writes (bio,
picture, name, website, phone, email) that the official Graph API does
not support.

## Sidecar endpoint

```
http://localhost:8011   (host port)
http://instagram-private-api:8000  (internal Docker network)
```

## Authentication model

The sidecar uses `X-Session-ID` header for all authenticated calls.
Sessions are stored in a TinyDB JSON file at `/data/db.json` inside the
container, persisted via the `instagram_sessions` Docker volume.

## Login flow

```mermaid
flowchart TD
    A[POST /auth/login\nusername+password+locale+timezone] --> B{Response}
    B -->|200 string| C[Success: session_id returned]
    B -->|ChallengeRequired| D[Challenge: SMS/email code needed]
    B -->|TwoFactorRequired| E[2FA: TOTP/SMS code needed]
    D --> F[POST /auth/challenge/resolve\nlast_json + security_code]
    E --> G[POST /auth/login\nsame u+p + verification_code]
    F --> C
    G --> C
    C --> H[GET /auth/settings\nsave settings JSON for restore]
    H --> I[Store session_id + settings\nin social_accounts.meta_data]
```

## Key parameters for reducing challenge risk

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `locale` | `el_GR` | Match the account owner's language |
| `timezone` | `10800` | UTC+3 (Athens) in seconds |
| `proxy` | (none) | Mobile/residential proxy URL to avoid datacenter IP blocks |

## Session persistence

After a successful login:
1. The sidecar stores the session in `/data/db.json` automatically.
2. The SocialAuto backend saves `session_id` and `settings` JSON in
   `social_accounts.meta_data` for cross-restart restore.
3. To restore without password: `PATCH /auth/settings` with the saved
   settings JSON.

## Scripts

```bash
# Check sidecar health
.devin/skills/instagram-private-api/scripts/health.sh

# Login with username/password (reads from secret store)
.devin/skills/instagram-private-api/scripts/login.sh

# Login with 2FA verification code
.devin/skills/instagram-private-api/scripts/login-2fa.sh <code>

# Resolve a challenge with a security code
.devin/skills/instagram-private-api/scripts/challenge-resolve.sh <session_id> <last_json> <code>

# Import an existing sessionid cookie
.devin/skills/instagram-private-api/scripts/import-session.sh <sessionid>

# Save settings for session restore
.devin/skills/instagram-private-api/scripts/save-settings.sh <session_id>

# Restore session from saved settings
.devin/skills/instagram-private-api/scripts/restore-session.sh <settings_json_file>

# Get current account profile
.devin/skills/instagram-private-api/scripts/get-profile.sh <session_id>

# Update biography
.devin/skills/instagram-private-api/scripts/update-bio.sh <session_id> "new bio text"

# Update profile picture
.devin/skills/instagram-private-api/scripts/update-picture.sh <session_id> <image_file>
```

## Important notes

- Instagram aggressively blocks datacenter IPs. A mobile or residential
  proxy is strongly recommended for reliable login.
- The `challenge_required` error means Instagram sent a security code
  via SMS or email. The account owner must provide that code.
- The `two_factor_required` error means 2FA is enabled. The user must
  provide a TOTP or SMS code.
- Sessions stored in the sidecar's `/data/db.json` persist across
  container restarts via the `instagram_sessions` Docker volume.
- Settings JSON saved in `social_accounts.meta_data` allows session
  restore without re-entering the password.
- Never log or commit session IDs, settings JSON, or passwords.
