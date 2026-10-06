---
name: social-content-core
description: >-
  Shared content rules every SocialAuto channel skill depends on — the account
  map, the Sofia Kakkava source material, the monetization funnel (FB Stars vs
  Polar), the NLP/SEO quality gate, no-duplicate rule, media pre-flight, and
  the monthly automation-checklist ritual. Read this BEFORE any
  *-content-ops skill.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# Social Content Core

## Account map (verified from Postgres `social_accounts`)

| Platform | Account ID | Handle | Role |
|----------|-----------|--------|------|
| facebook | `9355ed63-7787-43e5-a22d-ae0a33d5176b` | Themistoklis Baltzakis | Personal profile, professional mode, Stars funnel |
| facebook | `ad83c946-0f6b-4fbf-bc63-543d2c2237f5` | cloudless.gr (page ID 116436681562585) | **Canonical brand page** — Messenger bot lives here |
| facebook | `e280d96f-2807-4150-babe-f388d02bcfba` | Cloudless.gr | **DELETED 2026-10-02 — never target** |
| instagram | `38ddbd44-8811-4d0b-be62-a23fd2f50490` | cloudless.gr | Brand |
| linkedin | `18d5cd59-f0c2-4fc4-986e-03601734c7a5` | personal (baltzakis.themis) | Founder voice, finished stories |
| linkedin | `9c4451bb-e820-489f-8676-76ddbc788ffe` | cloudless-gr | Company Page, corporate voice |
| threads | `1071dcd5-1bc9-4770-923c-d897eb124485` | cloudless.gr | Brand |
| tiktok | `dcda73fb-75b6-47af-b9d1-c1ba404d91a8` | cloudless.gr | Brand |
| twitter | `a89d6852-eff8-479a-835f-50d806cf59dd` | @TBaltzakis | Personal/dev voice |
| telegram | `f75d0132-0964-4236-9766-a6603c997dfd` | Cloudless_newbot | Bot channel |
| whatsapp | `77f17091-3639-4633-b04d-0a3345dd7d3a` | Themistoklis Baltzakis | Business messaging |

Refresh: `docker exec social-postgres psql -U social_user -d social_automation -c "SELECT id, platform, username, status FROM social_accounts ORDER BY platform;"`

## Source material — Sofia Kakkava "Visibility Era"

Always draw from `/mnt/c/Users/tbaltzakis/OneDrive/Kakkava Sofia/` (extract with
`pymupdf` inside `social-api`). Pick the file matching the piece:
DAY 1 (creator type), DAY 2 (2-platform rule), DAY 3 (own your lane),
DAY 4 (5-second profile test), DAY 5 (bio that sells), `2 week/`, `Linkedin/`.

Core frameworks: niche formula "We help [WHO] who [PROBLEM] so they can [RESULT]";
"Clarity attracts. Vagueness gets ignored"; plain audience-first language.
Applied voice lives in `brand_voices.voice_signature` — see `content-strategy` skill.

## Monetization funnel (primary optimization goal)

Configured in `brand_voices.voice_signature.monetization_funnel`, propagates to
`/api/v1/ai/generate-content` + all n8n workflows:

1. **Personal funnel → FB Stars**: personal LinkedIn + X soft-funnel to
   `facebook.com/themis.baltzakis` (~1 in 3 posts max) — goal 66→500 followers.
2. **Brand funnel → Polar**: brand accounts close with
   `https://social.cloudless.gr/pricing` or audit `https://cloudless.gr/contact`.

Rules: CTA must be earned by the content; real numbers only; rotate phrasing.
Accounts below monetization thresholds (IG, TikTok, Threads) get product-funnel
CTAs only.

## Quality gate — mandatory before publishing

1. Generate via `POST /api/v1/ai/generate-content` (or `/improve-content`) —
   writes in the owner voice on the right platform.
2. NLP plain-English check + SEO score **≥90** before publish.
3. **Protect from LanguageTool `auto_correct`**: proper nouns, hashtags,
   `urn:li:` mention markup, domains — never "corrected".
4. Coach attribution: `#sofiakakkavacoach` hashtag only — never her name in prose.
5. **No duplicated posts** — never the same content/story twice, including the
   same piece repackaged in a different format. Different cuts per platform OK.

## Media pre-flight

- Deterministic rules run in `validate_media_for_platform`
  (`app/services/publishing.py::_PLATFORM_MEDIA_RULES`) — wrong dims/format/size
  = skipped, never partial.
- Semantic QA (VLM caption match) via `media-qa-jupyter` skill before any
  generated-media publish.
- IG + TikTok have `required: True` — no text-only posts exist there.

## Publish API

```bash
POST /api/v1/content/posts                 # create draft
POST /api/v1/content/posts/{id}/schedule   # schedule
POST /api/v1/content/posts/{id}/publish-now
POST /api/v1/content/posts/{id}/submit-review  # review workflow
POST /api/v1/content/posts/{id}/cross-post     # fan out to other accounts
```

Failed/odd publishes → `publish-ops` skill (signature table for every
known platform error).

## Monthly ritual — automation checklist

Every month: review services → regenerate `automation-checklist.pdf`
(`scripts/generate_automation_checklist.py` in cloudless.gr) → deploy → announce
on all channels (`monthly-checklist-update` skill).

## Per-channel skills

- `linkedin-content-ops` — personal finished-stories + company page
- `facebook-content-ops` — personal build-log (Stars) + canonical page
- `instagram-content-ops` — media-required brand grid
- `threads-content-ops` — brand text+media
- `x-content-ops` — dev-voice @TBaltzakis
- `tiktok-content-ops` — DIRECT_POST video/photo
- `messaging-channel-ops` — Telegram bot + WhatsApp
