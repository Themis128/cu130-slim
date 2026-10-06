---
name: linkedin-content-ops
description: >-
  What to publish on Cloudless LinkedIn — the personal founder account
  (finished stories only) and the cloudless-gr Company Page (corporate value,
  carousels, audit CTA). Covers the sidecar + API publish paths, PDF/carousel
  rules, mention markup, and the FB-Stars vs Polar funnel split. Use whenever
  writing, scheduling, or debugging a LinkedIn post.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# LinkedIn Content Ops

Read `social-content-core` first (account map, Kakkava source, quality gate, funnel).

## Surfaces

| Account | ID | Voice | Job |
|---------|----|-------|-----|
| Personal | `18d5cd59-f0c2-4fc4-986e-03601734c7a5` | Founder, first-person | **Finished stories only** — wins, metrics, completed builds |
| Company Page | `9c4451bb-e820-489f-8676-76ddbc788ffe` | Corporate "we" | Product value, carousels, audit funnel |

## Content rules

### Personal (`18d5cd59`)
- **Finished stories only.** Polished outcome posts: what shipped, real
  numbers, the lesson. Never the messy middle — that goes to personal Facebook.
- Real outcomes from this stack only (e.g. "200s → 10ms session polls") —
  no invented metrics.
- Funnel: soft-point to `facebook.com/themis.baltzakis` when the story fits
  (≤1 in 3 posts); otherwise end clean or with the product angle.
- Coach hashtag `#sofiakakkavacoach` when the post draws on the Visibility Era work.

### Company Page (`9c4451bb`)
- Corporate value angle: server/cloud cost savings, reliability wins,
  self-hosted vs SaaS comparisons, engineering outcomes.
- **CTA: free 30-minute audit → `https://cloudless.gr/contact`.**
- Carousels: use `cloudless-carousel-pipeline` skill (CF-only: DMR copy →
  NLP check → FLUX schnell → SD img2img → brand compose → org post).
- Monthly checklist announcement posts here too.

## Publishing paths

1. **Sidecar** (`_publish_linkedin_via_sidecar`) — primary for the personal
   account; browser session on the LinkedIn sidecar.
2. **API** (`_publish_linkedin`) — Graph/REST for the org page.
- LinkedIn sidecar ops (sessions, port 9225) → `linkedin-ads-ops` /
  `session-ops` skills.

## Media rules (`_PLATFORM_MEDIA_RULES.linkedin`)

- Optional (text-only fine). jpg/png/gif + video; PDF allowed (≤100MB — carousels).
- 200–6024px per side; ≤100MB image, ≤500MB video; ≤20 items.
- Mentions use `urn:li:person:...` markup — protect it from LanguageTool.

## Cadence guidance

- Personal: 2–3 finished-story posts/week.
- Company: 1–2 posts/week + carousel every ~2 days via the n8n
  `cloudless-cf-carousel-linkedin` workflow (`n8n-cloudless` skill).

## Gotchas

- `publish-ops` covers LinkedIn session-expiry and media errors.
- LinkedIn Marketing API dev→standard tier upgrade in progress — see
  `developer-apps-ops` skill before assuming new scopes work.

## Media capabilities

Full pipeline index: `media-pipelines` skill.

| Type | Constraint | Pipeline |
|---|---|---|
| Image | JPG/PNG brand-composed | `cloudless-carousel-pipeline` (single slide) |
| Carousel PDF | Multi-slide doc post, Company Page only | `cloudless-carousel-pipeline` |
| Video | MP4 H.264/yuv420p, silent AAC, faststart, spec-QA'd | `branded-video-pipeline` |
| AI video | Rare — prefer branded for corp voice | ComfyUI LTX/Wan |
