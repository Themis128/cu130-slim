---
name: socialauto-brand
description: >-
  Cloudless brand system: brand_voices voice_signature, colors/fonts/logo assets, and syncing brand config across SocialAuto, n8n workflows, and generation pipelines. Use for brand voice or visual identity work.
---

# Socialauto Brand

Consolidated skill — each section below was a standalone skill. Member scripts/templates live under `<source-skill>/` inside this directory.

| Section | Source |
|---|---|
| SocialAuto Brand Identity | `socialauto-brand` |
| Brand Sync | `socialauto-brand` → `brand-sync/` |

## SocialAuto Brand Identity

Manage the Cloudless brand identity system.

### When to use

- View or update brand DNA (name, industry, positioning, mission, values)
- Edit brand voice (tone sliders, messaging pillars, banned/preferred phrases)
- Edit brand visual (colors, fonts, type scale, logo)
- Generate or view brand guidelines
- List brand assets (logos, templates, OG images, favicons)

### API base

```
http://127.0.0.1:8083/api/v1/brand
```

### Authentication

Bearer token from `POST /api/v1/auth/login`.

### Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `` | Get full brand profile |
| PUT | `` | Update brand DNA |
| GET | `/voice` | Get brand voice |
| PUT | `/voice` | Update brand voice |
| GET | `/visual` | Get brand visual |
| PUT | `/visual` | Update brand visual |
| GET | `/guidelines` | Get brand guidelines |
| POST | `/guidelines` | Generate/compile guidelines |
| GET | `/assets` | List brand assets |
| POST | `/assets` | Add brand asset |

### Cloudless brand

| Field | Value |
|-------|-------|
| Name | Cloudless |
| Industry | Cloud Computing, Serverless & AI Marketing |
| Tagline | Clear skies. Zero friction. |
| Website | https://cloudless.gr |
| Primary color | #0b1220 (dark navy) |
| Accent color | #22d3e6 (teal) |
| Heading font | Instrument Sans |
| Body font | Work Sans |
| Values | Innovation, Customer-centricity, Flexibility, Collaboration |

### Tool scripts

Run from repo root `cu130-slim/`:

```bash
## View full brand profile
.devin/skills/socialauto-brand/scripts/get-brand.py

## View brand voice
.devin/skills/socialauto-brand/scripts/get-voice.py

## View brand visual
.devin/skills/socialauto-brand/scripts/get-visual.py

## View brand guidelines
.devin/skills/socialauto-brand/scripts/get-guidelines.py

## List brand assets
.devin/skills/socialauto-brand/scripts/list-assets.py

## Update brand DNA (name, tagline, website, mission)
.devin/skills/socialauto-brand/scripts/update-brand-dna.py "Cloudless" "Clear skies. Zero friction." "https://cloudless.gr" "Mission text"

## Update brand visual (colors, fonts, logo)
.devin/skills/socialauto-brand/scripts/update-brand-visual.py "#0b1220" "#00fff5" "Instrument Sans" "Work Sans"

## Upload a logo and set it as brand logo
.devin/skills/socialauto-brand/scripts/upload-logo.py /path/to/logo.png

## Compile/generate brand guidelines
.devin/skills/socialauto-brand/scripts/compile-guidelines.py
```

### Brand voice structure

```json
{
  "tone_dimensions": {
    "professional": 4,
    "friendly": 3,
    "technical": 3,
    "bold": 2,
    "playful": 1
  },
  "messaging_pillars": ["...", "..."],
  "banned_phrases": ["...", "..."],
  "preferred_phrases": ["...", "..."],
  "example_content": "...",
  "voice_signature": { ... }
}
```

### Important notes

- One Brand per team, stored in the `brands` table.
- Brand guidelines are compiled into a shareable JSON document with a token.
- Brand assets are linked to the media library.
- The brand visual identity is used by the carousel pipeline for slide
  composition (dark navy + teal Cloudless brand colors).

## Brand Sync

Sync the Cloudless brand identity (from the SocialAuto brand system) to all
connected social media profiles. Each platform has different capabilities
and update methods — this skill coordinates them all.

### When to use

- Apply brand voice to all social profile bios
- Upload the company logo as profile picture across platforms
- Update display names to match brand identity
- Sync website links across all profiles
- Ensure consistent branding after brand profile changes

### Brand data source

The brand identity is stored in SocialAuto's brand system:
- API: `GET /api/v1/brand` (returns DNA, voice, visual, assets)
- Logo: stored in media library, URL in `brand.visual.logo_url`
- Bio template: derived from brand tagline + mission + voice

### Platform capabilities

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

### Bio template

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

### Tool scripts

Run from repo root `cu130-slim/`:

```bash
## Sync brand to ALL connected profiles (bio + name + picture where supported)
.devin/skills/socialauto-brand/brand-sync/scripts/sync-all.py

## Sync brand to a specific platform
.devin/skills/socialauto-brand/brand-sync/scripts/sync-platform.py threads

## Sync brand bio to all platforms (name and picture unchanged)
.devin/skills/socialauto-brand/brand-sync/scripts/sync-bio.py

## Upload logo as profile picture to all supported platforms
.devin/skills/socialauto-brand/brand-sync/scripts/sync-profile-pic.py

## Generate the brand bio text from the brand profile
.devin/skills/socialauto-brand/brand-sync/scripts/generate-bio.py

## Show brand sync status (what's synced, what's not)
.devin/skills/socialauto-brand/brand-sync/scripts/sync-status.py
```

### Sync workflow

1. **Read brand profile** from SocialAuto (`GET /api/v1/brand`)
2. **Generate bio** from brand tagline + mission + voice
3. **For each connected account**:
   a. Determine the platform and update method
   b. Update bio via the appropriate API or browser bridge
   c. Update display name if supported
   d. Upload logo as profile picture if supported
4. **Report results** per platform

### Per-platform details

#### Threads
- Bio: Updated via browser bridge (no write API for profile fields)
- Name: Updated via browser bridge (limited to 2 changes per 14 days)
- Profile pic: Synced from linked Instagram account (cannot change independently)
- Uses: `browser-ops` skill

#### Instagram (Business + Personal)
- Bio: Updated via instagrapi sidecar (`/api/v1/profile/{id}`)
- Name: Updated via instagrapi sidecar
- Profile pic: Updated via instagrapi sidecar (`/api/v1/profile/{id}/picture`)
- Uses: `social-profile-update` skill

#### Facebook Page
- Bio: Updated via Graph API (`POST /{page-id}` with `about` field)
- Name: Not changeable via API (Facebook policy)
- Profile pic: Updated via Graph API (`POST /{page-id}/picture`)
- Cover: Updated via Graph API (`POST /{page-id}/cover`)
- Uses: `social-profile-update` skill

#### LinkedIn (Org + Personal)
- Bio: Updated via LinkedIn API or browser sidecar
- Name: Not changeable (LinkedIn policy)
- Profile pic: Updated via LinkedIn API
- Uses: `social-profile-update` skill

#### TikTok
- Bio: Not available via API (browser only, TikTok policy)
- Name: Not available via API
- Profile pic: Not available via API
- Uses: Manual update via browser only

#### Twitter/X
- Bio: Updated via Twitter API v2 (`PUT /2/users/me`)
- Name: Updated via Twitter API v2
- Profile pic: Updated via Twitter API v1.1 (`POST /1.1/account/update_profile_image`)
- Uses: `social-profile-update` skill

### Important notes

- Always read the brand profile first to get the latest bio template and logo.
- Threads profile pictures are synced from Instagram — update the Instagram
  profile picture to change Threads automatically.
- LinkedIn and Facebook have name-change limits — avoid frequent name updates.
- TikTok has no profile update API — everything must be done manually.
- The brand bio should use the brand voice (see `socialauto-brand` skill).
- Never change profile pictures without explicit user permission.
