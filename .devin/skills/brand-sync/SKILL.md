---
name: brand-sync
description: >-
  Sync the Cloudless brand identity from SocialAuto to all connected social
  media profiles. Updates bios, display names, and profile pictures across
  Threads, Instagram, Facebook, LinkedIn, TikTok, and Twitter/X using the
  appropriate API or browser bridge per platform. Use when branding all
  social profiles consistently, applying brand voice to bios, or uploading
  the company logo as profile picture.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# Brand Sync

Sync the Cloudless brand identity (from the SocialAuto brand system) to all
connected social media profiles. Each platform has different capabilities
and update methods — this skill coordinates them all.

## When to use

- Apply brand voice to all social profile bios
- Upload the company logo as profile picture across platforms
- Update display names to match brand identity
- Sync website links across all profiles
- Ensure consistent branding after brand profile changes

## Brand data source

The brand identity is stored in SocialAuto's brand system:
- API: `GET /api/v1/brand` (returns DNA, voice, visual, assets)
- Logo: stored in media library, URL in `brand.visual.logo_url`
- Bio template: derived from brand tagline + mission + voice

## Platform capabilities

| Platform | Bio | Display name | Profile pic | Cover/Banner | Method |
|----------|-----|-------------|-------------|-------------|--------|
| Threads | Yes (browser) | Yes (browser) | Via Instagram sync | No | Browser bridge |
| Instagram | Yes (API/sidecar) | Yes (API/sidecar) | Yes (API/sidecar) | No | instagrapi sidecar |
| Facebook Page | Yes (Graph API) | Yes (Graph API) | Yes (Graph API) | Yes (Graph API) | Graph API |
| Facebook Personal | Limited | Limited | No | No | — |
| LinkedIn Org | Yes (API) | Yes (API) | Yes (API) | Yes (API) | LinkedIn API |
| LinkedIn Personal | Yes (API) | Yes (API) | Yes (API) | Yes (API) | LinkedIn API |
| TikTok | No (API) | No (API) | No (API) | No (API) | Browser only |
| Twitter/X | Yes (API) | Yes (API) | Yes (API) | Yes (API) | Twitter API v2 |

## Bio template

The brand-sync bio is generated from the brand profile:

```
{tagline}
{mission_short}
{location} | Worldwide
↓ {website}
```

For Cloudless:
```
Clear skies. Zero friction.
Serverless cloud, AI marketing & custom software for startups
Build, run, grow — without the enterprise BS
📍 Athens | Worldwide
↓ cloudless.gr
```

## Tool scripts

Run from repo root `cu130-slim/`:

```bash
# Sync brand to ALL connected profiles (bio + name + picture where supported)
.devin/skills/brand-sync/scripts/sync-all.sh

# Sync brand to a specific platform
.devin/skills/brand-sync/scripts/sync-platform.sh threads

# Sync brand bio to all platforms (name and picture unchanged)
.devin/skills/brand-sync/scripts/sync-bio.sh

# Upload logo as profile picture to all supported platforms
.devin/skills/brand-sync/scripts/sync-profile-pic.sh

# Generate the brand bio text from the brand profile
.devin/skills/brand-sync/scripts/generate-bio.sh

# Show brand sync status (what's synced, what's not)
.devin/skills/brand-sync/scripts/sync-status.sh
```

## Sync workflow

1. **Read brand profile** from SocialAuto (`GET /api/v1/brand`)
2. **Generate bio** from brand tagline + mission + voice
3. **For each connected account**:
   a. Determine the platform and update method
   b. Update bio via the appropriate API or browser bridge
   c. Update display name if supported
   d. Upload logo as profile picture if supported
4. **Report results** per platform

## Per-platform details

### Threads
- Bio: Updated via browser bridge (no write API for profile fields)
- Name: Updated via browser bridge (limited to 2 changes per 14 days)
- Profile pic: Synced from linked Instagram account (cannot change independently)
- Uses: `browser-bridge-ops` skill

### Instagram (Business + Personal)
- Bio: Updated via instagrapi sidecar (`/api/v1/profile/{id}`)
- Name: Updated via instagrapi sidecar
- Profile pic: Updated via instagrapi sidecar (`/api/v1/profile/{id}/picture`)
- Uses: `socialauto-profile` skill

### Facebook Page
- Bio: Updated via Graph API (`POST /{page-id}` with `about` field)
- Name: Not changeable via API (Facebook policy)
- Profile pic: Updated via Graph API (`POST /{page-id}/picture`)
- Cover: Updated via Graph API (`POST /{page-id}/cover`)
- Uses: `socialauto-profile` skill

### LinkedIn (Org + Personal)
- Bio: Updated via LinkedIn API or browser sidecar
- Name: Not changeable (LinkedIn policy)
- Profile pic: Updated via LinkedIn API
- Uses: `socialauto-profile` skill

### TikTok
- Bio: Not available via API (browser only, TikTok policy)
- Name: Not available via API
- Profile pic: Not available via API
- Uses: Manual update via browser only

### Twitter/X
- Bio: Updated via Twitter API v2 (`PUT /2/users/me`)
- Name: Updated via Twitter API v2
- Profile pic: Updated via Twitter API v1.1 (`POST /1.1/account/update_profile_image`)
- Uses: `socialauto-profile` skill

## Important notes

- Always read the brand profile first to get the latest bio template and logo.
- Threads profile pictures are synced from Instagram — update the Instagram
  profile picture to change Threads automatically.
- LinkedIn and Facebook have name-change limits — avoid frequent name updates.
- TikTok has no profile update API — everything must be done manually.
- The brand bio should use the brand voice (see `socialauto-brand` skill).
- Never change profile pictures without explicit user permission.
