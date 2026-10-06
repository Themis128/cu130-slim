---
name: threads-content-ops
description: >-
  What to publish on Cloudless Threads (@cloudless.gr) — conversational
  brand text and mixed-media posts via the two-step container API. Covers the
  public-URL media requirement, 20-item carousel cap, JPEG/PNG constraint,
  and product-funnel CTAs. Use for any Threads post or repost of IG content.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# Threads Content Ops

Read `social-content-core` first.

## Surface

- Account `1071dcd5-1bc9-4770-923c-d897eb124485` · `@cloudless.gr`.

## Content rules

- Conversational, shorter-form brand voice — hot takes on self-hosting,
  cloud costs, dev-infra wins. More casual than LinkedIn, more polished than
  the FB build log.
- Text-only posts work here (unlike IG/TikTok).
- Product-funnel CTA → `https://social.cloudless.gr/pricing` (links DO work
  in Threads posts).
- Good reuse target for IG carousel images — but rewrite the caption, don't
  copy-paste (no-duplicate rule: same piece needs a genuinely different cut).

## Publishing path (`_publish_threads` → `ThreadsAPIClient`)

- **Two-step flow**: create media container → publish container.
- Supports text-only, single image, single video, carousel (≤20 mixed
  image/video items).
- **Media must be hosted at public URLs** — resolved via `_media_public_url`
  (R2/CDN storage path); local files are uploaded to storage first.
- Caption attaches to the parent carousel container, not items.
- Threads IG-SSO bootstrap exists in `session-ops` if the token dies.

## Media rules

- jpg/jpeg/png/gif + video. Width 320–1440px; aspect 1:10–10:1 (very flexible).
- ≤8MB image, ≤1GB video; ≤20 items.
- **Carousel images must be JPEG/PNG** — `_media_public_url(force_jpeg=True)`
  converts; webp/gif carousels will fail.

## Gotchas

- "object-not-found" → `publish-ops` (usually a media URL that
  expired or isn't publicly fetchable — check R2/MinIO object ACL).
- Container publishing is async — a 200 on create ≠ published; the worker
  polls container status before publish.

## Media capabilities

Full pipeline index: `media-pipelines` skill.

| Type | Constraint | Pipeline |
|---|---|---|
| Image | Must be a **public URL** (R2/MinIO), JPEG/PNG only | Brand-composed image |
| Carousel | ≤20 items, images only | — |
| Video | Public-URL MP4 | `branded-video-pipeline` |
