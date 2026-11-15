---
name: tiktok-content-ops
description: >-
  What to publish on Cloudless TikTok (@cloudless.gr) — video-first DIRECT_POST
  content (ComfyUI LTX/Wan renders, build clips, branded shorts) via the
  Content Posting API. Covers media-required rules, the DIRECT_POST toggles
  (duet/stitch/comment, brand flags, is_aigc), url_ownership_unverified, and
  PULL_FROM_URL domain verification. Use for any TikTok post or publish error.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# TikTok Content Ops

Read `social-content-core` first. Console/app ops → `tiktok-console-ops` skill.

## Surface

- Account `ab439e1e-549f-4a8b-901a-2f7eeb8a1c88` · profile `@cloudless.gr`
  (display: "Cloudless | Managed Cloud"). `dcda73fb-…` is the same
  profile but its token is EXPIRED (401) — never target it; `7c33ed15-…`
  shares ab439e1e's open_id (duplicate row). Verify live with
  `tiktok_tool.py accounts` before publishing.

## Content rules

<!-- PLATFORM-FIT:BEGIN tiktok -->
### What the TikTok audience/algorithm (FYP) favors
- Watch-time + engagement over production quality: raw, authentic, or humorous vertical video beats slick ads; trending audio/challenges and educational micro-content (life hacks, niche explainers like #TechTok) all travel. Hook in the first second — the caption supports the video.
- 2026 signals: the FYP is interest-graph driven — follower count is not a direct ranking factor, so a small account's great post can out-reach a big account's weak one.
- Cadence (Buffer 2026): 2–5 videos/week — the biggest view lift comes from going 1 → 2–5/wk; keyword in caption + on-screen text + spoken in the first seconds covers FYP and TikTok Search.

<!-- PLATFORM-FIT:END -->

- **Media required** — video or photo posts; no text-only.
- **≥60s minimum for video** — hard owner rule (2026-11-15):
  TikTok videos must be at least 60 seconds; shorter clips are ineligible.
- Best content: short vertical clips — infra timelapses, automation runs,
  generated video (ComfyUI Wan2.2 TI2V-5B GGUF via `media_gpu_lock`),
  screen-capture walkthroughs.
- Product-funnel CTA only (below monetization thresholds) → pricing/audit.
- Set `is_aigc: true` in `platform_specific.tiktok` when the clip is
  AI-generated — TikTok requires the disclosure flag.

## Publishing path (`_publish_tiktok`)

- Official **Content Posting API — DIRECT_POST** (in-feed publish, not the
  upload-only `MEDIA_UPLOAD` mode — check the console config drift via
  `tiktok-console-ops`).
- **DIRECT_POST can be gated at publish time** — TikTok answers
  `unaudited_client_can_only_post_to_private_accounts` despite the approved
  app (audit lag); `_publish_tiktok` then falls back to `MEDIA_UPLOAD`
  → `SEND_TO_USER_INBOX` + `v_inbox_file~` publish_id = draft in the
  creator's app inbox needing a manual finish, NOT a live post.
  Full publish runbook: `tiktok-video-publish-ops`.
- `platform_specific.tiktok` options map to Direct Post fields:
  `brand_content_toggle`, `brand_organic_toggle`, `disable_duet`,
  `disable_stitch`, `disable_comment`, `video_cover_timestamp_ms`, `is_aigc`.
- Chunked video upload via `_video_chunk_plan` for large files.

## Media rules

- jpg/jpeg/png/webp + video; 360–4096px per side; ≤1GB image, ≤4GB video;
  ≤35 items (photo carousels).

## Gotchas

- `url_ownership_unverified` → the PULL_FROM_URL domain verify hasn't landed;
  fix via `tiktok-console-ops` (DNS TXT through Cloudflare).
- Session/token issues → TikTok browser sidecar (`tiktok-console-ops`,
  `session-ops`).
- App is in audit — DIRECT_POST scopes must match the console config or
  posts fail with scope errors.

## Media capabilities

Video-first channel — media REQUIRED. Full index: `media-pipelines`.

| Type | Constraint | Pipeline |
|---|---|---|
| Video | 9:16 vertical (480x832 default), DIRECT_POST or MEDIA_UPLOAD | ComfyUI LTX/Wan (primary) / `branded-video-pipeline` shorts |
| Photo slideshow | Photo-mode post | Brand slides |
| `is_aigc` flag | Required for AI-generated video | — |
