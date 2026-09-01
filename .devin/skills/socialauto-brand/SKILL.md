---
name: socialauto-brand
description: >-
  Manage the brand identity system in SocialAuto: brand DNA, voice, visual
  identity, guidelines, and assets. Covers /api/v1/brand/* endpoints. Use
  when viewing or updating brand identity, tone sliders, messaging pillars,
  voice signature, colors, fonts, logo, or generating brand guidelines.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# SocialAuto Brand Identity

Manage the Cloudless brand identity system.

## When to use

- View or update brand DNA (name, industry, positioning, mission, values)
- Edit brand voice (tone sliders, messaging pillars, banned/preferred phrases)
- Edit brand visual (colors, fonts, type scale, logo)
- Generate or view brand guidelines
- List brand assets (logos, templates, OG images, favicons)

## API base

```
http://127.0.0.1:8083/api/v1/brand
```

## Authentication

Bearer token from `POST /api/v1/auth/login`.

## Endpoints

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

## Cloudless brand

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

## Tool scripts

Run from repo root `cu130-slim/`:

```bash
# View full brand profile
.devin/skills/socialauto-brand/scripts/get-brand.sh

# View brand voice
.devin/skills/socialauto-brand/scripts/get-voice.sh

# View brand visual
.devin/skills/socialauto-brand/scripts/get-visual.sh

# View brand guidelines
.devin/skills/socialauto-brand/scripts/get-guidelines.sh

# List brand assets
.devin/skills/socialauto-brand/scripts/list-assets.sh
```

## Brand voice structure

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

## Important notes

- One Brand per team, stored in the `brands` table.
- Brand guidelines are compiled into a shareable JSON document with a token.
- Brand assets are linked to the media library.
- The brand visual identity is used by the carousel pipeline for slide
  composition (dark navy + teal Cloudless brand colors).
