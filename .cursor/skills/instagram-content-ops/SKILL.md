---
name: instagram-content-ops
description: >-
  What to publish on Cloudless Instagram (@cloudless.gr) — media-required brand
  grid/reels content, "link in bio" product funnel, and the 5-path publish
  chain (Business Graph → instagrapi → web API → sidecar → FB Login Graph).
  Covers the 2200-char caption cap, aspect-ratio and size rules, and
  session/media error handling. Use for any IG post, reel, or carousel.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# Instagram Content Ops

Read `social-content-core` first.

## Surface

- Account `38ddbd44-8811-4d0b-be62-a23fd2f50490` · `@cloudless.gr` · ~10 followers.

## Content rules

- **Media is mandatory** — IG has no text-only posts on any path
  (`required: True` in `_PLATFORM_MEDIA_RULES`). A post with zero resolved
  media is skipped, never published bare.
- Content: product visuals, carousel slides, before/after infra shots,
  branded graphics (ComfyUI FLUX → brand compose), short reels.
- Caption ≤2200 chars; hooks in the first line; 3–8 hashtags incl.
  `#sofiakakkavacoach` where earned.
- **Links don't work in captions** — CTA is "link in bio" →
  `https://social.cloudless.gr/pricing`. The API cannot write the bio; keep
  the bio link pointing at pricing (verify manually, never auto-change
  profile fields — no avatar/bio edits without explicit user instruction).
- Below monetization thresholds → product-funnel CTAs only; don't chase
  Meta creator tools here.

## Publishing priority (`_publish_instagram`, first success wins)

1. **Business Login Graph API** (`graph.instagram.com`) — official path for
   Instagram-Business-Login connections; no FB Page linkage needed.
2. **instagrapi** — private mobile API; needs `INSTAGRAM_USERNAME` +
   `INSTAGRAM_PASSWORD` env.
3. **Web API** (`rupload_igphoto`) — browser sessionid + csrf + ds_user_id
   in account meta_data.
4. **Sidecar** — aiograpi-rest fallback, esp. video uploads.
5. **FB Login Graph** — last resort (needs App Review + Page linkage).

## Media rules

- jpg/jpeg/png + mp4/mov. Width 320–1440px; aspect 4:5–1.91:1.
- ≤8MB image, ≤300MB video; ≤10 items/carousel.

## Gotchas

- "Session expired", "media not available", "missing image" →
  `publish-alert-triage` signature table.
- Session recovery → `session-auto-heal` (IG sessions are fragile; re-login
  via stored creds).
- Semantic media QA before publish → `media-qa-jupyter`.
