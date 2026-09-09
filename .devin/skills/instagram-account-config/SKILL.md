---
name: instagram-account-config
description: >-
  Configure Instagram accounts (personal and business) end-to-end: login via
  VNC/Facebook, update bio with stylish Unicode fonts and SEO keywords, set
  website URL, apply optimal personal account settings, register in SocialAuto,
  and verify changes. Uses natural browser navigation to avoid Instagram's
  __coig_login redirect guard. Includes bio generator with 6 style templates
  and a 15-point settings checklist based on 2025 Instagram optimization
  research.
allowed-tools:
  - read
  - exec
  - write
  - edit
  - grep
  - glob
triggers:
  - user
  - model
---

# Instagram Account Configuration

## Overview

This skill provides the complete workflow for configuring Instagram accounts
through the SocialAuto stack. It solves three key problems:

1. **Instagram's `__coig_login` redirect** — Direct navigation to
   `/accounts/edit/` triggers a session invalidation guard. The browser
   bridge uses **natural navigation** (profile page → click "Edit profile")
   to bypass this.
2. **Stylish bio generation** — Instagram doesn't support fonts natively,
   but Unicode mathematical characters (Sans Bold, Italic, Small Caps)
   render as styled text. The bio generator creates SEO-optimized bios
   with aesthetic dividers and arrow CTAs.
3. **Optimal settings checklist** — Based on 2025 Instagram optimization
   research: 2FA with authenticator app, activity status off, story
   sharing controls, comment filters, tag approval, and more.

## Architecture

```
User → VNC (port 6080) → browser-novnc (port 9223) → Instagram
                           ↑
                           ├── /session/start    — open browser for platform
                           ├── /session/navigate — navigate to URL
                           ├── /session/evaluate — run JS in page
                           ├── /session/fill     — fill form fields
                           ├── /session/click    — click elements
                           ├── /profile/instagram (GET)  — read profile
                           └── /profile/instagram (PATCH) — update profile
                                                      ↑
                                                      uses natural navigation
```

## Prerequisites

1. **Docker Compose stack running** — `docker compose ps` shows
   `browser-novnc` and `instagram-private-api` containers healthy.
2. **VNC access** — `http://localhost:6080/vnc.html` opens the noVNC viewer.
3. **Facebook-linked Instagram account** — Login via "Log in with Facebook"
   button (avoids password login throttling for Facebook-linked accounts).

## Step-by-step workflow

### Step 1: Start a browser session

```bash
# Stop any existing session
curl -s -X POST http://localhost:9223/session/stop

# Start a new Instagram browser session
curl -s -X POST http://localhost:9223/session/start \
  -H 'Content-Type: application/json' \
  -d '{"platform":"instagram"}' | python3 -m json.tool
```

### Step 2: Login via VNC

1. Open `http://localhost:6080/vnc.html` in your browser.
2. On the Instagram login page, click **"Log in with Facebook"**.
3. If multiple accounts appear, select the correct one.
4. Wait until you see the Instagram feed.
5. Verify the logged-in account:

```bash
curl -s -X POST http://localhost:9223/session/navigate \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.instagram.com/"}' | python3 -m json.tool

curl -s -X POST http://localhost:9223/session/evaluate \
  -H 'Content-Type: application/json' \
  -d '{"expression":"document.body.innerText.substring(0, 100)"}' | \
  python3 -c "import sys,json; print(json.load(sys.stdin).get('result',''))"
```

### Step 3: Generate a stylish bio

```bash
# Inside the social-api container
docker compose exec -T social-api python /app/app/scripts/instagram_bio_generator.py \
  --name "Cloudless" \
  --title "Founder @ " \
  --skills "Cloud Architect · Azure · AWS" \
  --experience "15+ yrs building systems" \
  --location "Athens" \
  --links "cloudless.gr | baltzakisthemis.com" \
  --style bold-brand

# List all styles
docker compose exec -T social-api python /app/app/scripts/instagram_bio_generator.py --list-styles

# Check a bio for SEO/NLP
docker compose exec -T social-api python /app/app/scripts/instagram_bio_generator.py \
  --check "🚀 Founder @ Cloudless..."
```

### Step 4: Update the profile

```bash
# Using the profile update script (recommended)
docker compose exec -T social-api python /app/app/scripts/instagram_profile_update.py \
  --bio "🚀 Founder @ 𝗖𝗹𝗼𝘂𝗱𝗹𝗲𝘀𝘀
☁️ Cloud Architect · Azure · AWS
💡 15+ yrs building systems
📍 Athens → Worldwide
↓ cloudless.gr | baltzakisthemis.com"

# Or using curl directly
curl -s -X PATCH http://localhost:9223/profile/instagram \
  -H 'Content-Type: application/json' \
  -d '{"biography":"🚀 Founder @ Cloudless\n☁️ Cloud Architect · Azure · AWS"}' | \
  python3 -m json.tool
```

### Step 5: Verify the update

```bash
# Read the profile back
docker compose exec -T social-api python /app/app/scripts/instagram_profile_update.py --read

# Verify a specific bio text was saved
docker compose exec -T social-api python /app/app/scripts/instagram_profile_update.py \
  --verify-bio "Founder"
```

### Step 6: Apply optimal settings

```bash
# Print the settings checklist
docker compose exec -T social-api python /app/app/scripts/instagram_settings_checklist.py --list

# Check current settings via browser
docker compose exec -T social-api python /app/app/scripts/instagram_settings_checklist.py --check
```

Settings that require manual VNC action (Instagram doesn't expose these via API):

| Setting | Action | Priority |
|---------|--------|----------|
| 2FA | Settings → Accounts Center → 2FA → Authenticator App | CRITICAL |
| Activity Status | Settings → How you use Instagram → Activity Status → OFF | MEDIUM |
| Story Sharing | Settings → Story and reels → Sharing → OFF | MEDIUM |
| Comment Filter | Settings → Comments → Hide offensive comments → ON | MEDIUM |
| Tag Approval | Settings → Tags and mentions → Manual approval | MEDIUM |

### Step 7: Register in SocialAuto

```bash
# Register the account in the SocialAuto database
docker compose exec -T social-api python -c "
import asyncio
from sqlalchemy import text
from app.db.session import get_db
import uuid

async def register():
    async for db in get_db():
        result = await db.execute(text(
            \"SELECT id FROM social_accounts WHERE platform='instagram' AND username='USERNAME'\"
        ))
        if result.fetchone():
            print('Already registered')
            return
        result = await db.execute(text('SELECT id FROM teams LIMIT 1'))
        team = result.fetchone()
        if not team:
            print('No team found')
            return
        await db.execute(text(
            \"INSERT INTO social_accounts (id, team_id, platform, account_id, username, \"
            \"display_name, account_type, is_business, access_token_enc, refresh_token_enc, \"
            \"token_expires_at, scopes, status, meta_data, created_at, updated_at) \"
            \"VALUES (:id, :team_id, 'instagram', :account_id, 'USERNAME', 'Display Name', \"
            \"'personal', false, 'browser-session', '', NULL, '{}', 'active', \"
            "'{\\\"auth_method\\\": \\\"facebook_linked\\\", \\\"requires_vnc\\\": true}', NOW(), NOW())\"
        ), {'id': str(uuid.uuid4()), 'team_id': team[0], 'account_id': 'USERNAME'})
        await db.commit()
        print('Registered')
        break

asyncio.run(register())
"
```

## Bio style templates

| Style | Description | Best for |
|-------|-------------|----------|
| `bold-brand` | Brand name in Sans Bold Unicode | Personal branding |
| `small-caps` | Brand name in Small Caps | Minimalist aesthetic |
| `italic-tagline` | Italic tagline | Creative professionals |
| `aesthetic-divider` | Star divider line | Artistic accounts |
| `clean-arrows` | Plain text with arrows | Maximum readability |
| `minimal-bullets` | Bullet points | Service listings |

## Key constraints

- **Bio limit**: 150 characters (including emojis, line breaks, invisible chars)
- **Bio lines**: 5 lines maximum (Instagram renders max 5)
- **Name field**: Keep plain text for searchability (no Unicode fonts)
- **Website field**: Only available for business/creator accounts (disabled for personal)
- **Session lifetime**: Instagram sessions last ~1 week; re-login via VNC when expired
- **Rate limit**: 30 requests/min via WARP proxy; backoff after 429s

## Troubleshooting

### `__coig_login` redirect

**Cause**: Direct navigation to `/accounts/edit/` triggers Instagram's
session guard.

**Fix**: The browser bridge now uses natural navigation (profile page →
click "Edit profile"). If you still see this error, the session is stale —
re-login via VNC.

### Form submit doesn't persist

**Cause**: React state not updated by JavaScript value assignment.

**Fix**: Use Playwright's `fill()` method (via `/session/fill` endpoint),
which dispatches proper React-compatible events. The `update_instagram_profile`
endpoint in browser-bridge.py handles this correctly.

### `LoginRequired` from instagrapi

**Cause**: The sessionid was extracted from a different IP context than
the instagrapi client.

**Fix**: Use the browser bridge (same IP as the VNC session) instead of
instagrapi for profile updates. instagrapi is for publishing, not profile
editing.

### Website field disabled

**Cause**: Personal accounts don't have the website field. Only business
and creator accounts do.

**Fix**: Include URLs in the bio text instead. Use `↓ cloudless.gr` format
with a downward arrow (proven to increase CTR per 2025 research).

## Tool scripts

| Script | Location | Purpose |
|--------|----------|---------|
| `instagram_profile_update.py` | `app/scripts/` | Update bio, name, website via browser bridge |
| `instagram_bio_generator.py` | `app/scripts/` | Generate stylish SEO bios with Unicode fonts |
| `instagram_settings_checklist.py` | `app/scripts/` | Check and list optimal account settings |
| `update-bio.sh` | `scripts/` | Quick bio update via sidecar (instagrapi) |
| `login-by-sessionid.sh` | `scripts/` | Login to sidecar with sessionid |
| `login-via-facebook.sh` | `scripts/` | Full Facebook login flow via VNC |
