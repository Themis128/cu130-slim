---
name: media-pipelines
description: >-
  Index of every media type SocialAuto can produce and which pipeline builds it:
  branded static images, LinkedIn carousel PDFs, branded slideshow videos (no AI
  model), ComfyUI FLUX backgrounds, AI video (LTX/Wan), emoji/icons, and the media
  library + QA gate. Includes the per-channel media compatibility matrix. Use
  when deciding what media a post can carry or which pipeline to invoke.
---

# Media Pipelines

Single source of truth for "what media can I put on this channel and how do I build it".
Channel-specific constraints live in each `*-content-ops` skill; this index covers
capabilities and routing.

## Pipelines

| Pipeline | Skill | Produces | Cost/Time |
|---|---|---|---|
| Branded static image | `cloudless-carousel-pipeline` (single-slide mode) | JPG/PNG, brand-composed | DMR copy + FLUX schnell bg, seconds |
| LinkedIn carousel PDF | `cloudless-carousel-pipeline` | Multi-slide PDF doc post | FLUX → SD img2img → compose |
| Branded slideshow video | `branded-video-pipeline` (`scripts/gen_branded_video.py`) | MP4 H.264/yuv420p, zoom+xfade, silent AAC, faststart | ~40s, zero GPU |
| AI video (real motion) | ComfyUI Wan2.2 TI2V-5B via `tiktok-video-post` n8n workflow | Vertical clip (default 704x1280, ~16min render) | GPU |
| Carousel backgrounds | ComfyUI FLUX.1-schnell GGUF → CF Workers AI fallback | Slide background images | local GPU, free |
| Emoji/icon assets | `emoji-generator` | Unicode/styled assets for copy | instant |
| Media library | `socialauto-media` | Stored asset_ids for posts | reuse |
| QA gate (mandatory) | `media-qa-jupyter` | deterministic platform rules + qwen3-vl semantic check | seconds |

## Channel compatibility

| Media type | LinkedIn | Facebook | Instagram | Threads | X | TikTok | Messaging |
|---|---|---|---|---|---|---|---|
| Image | ✅ page/personal | ✅ page / sidecar | ✅ required | ✅ public URL only | ✅ ≤4 | slideshow only | ✅ |
| Carousel PDF | ✅ Company Page | — | images ≤10 | ≤20 (img only) | — | — | doc attach |
| Branded video MP4 | ✅ spec QA | ✅ | ✅ reel 9:16 | ✅ | ✅ ≤2:20 | ✅ 9:16 | ✅ |
| AI video (LTX/Wan) | rare | rare | reel | — | — | ✅ primary | — |
| Emoji/icon | ✅ in copy | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

## Rules that always apply

- **Never publish without media** — the `post-media-correctness` rule (now in
  `media-qa-jupyter`): every post needs ≥1 correct, on-topic asset passing
  `validate_media_for_platform` + semantic QA. Text-only posts are skipped, not shipped.
- **No duplicates** — same piece in a different format still counts as a duplicate
  (carousel → video of the same slides). Repackage only when it's a genuinely different cut.
- **Invented-metric trap** — `servers`/`pricing`/`rocket` brand motifs render built-in
  stat cards unless `chart_data` is passed; verify slide numbers are real before posting.
- Funnel CTAs per `social-content-core` monetization map — Polar pricing on brand
  channels, FB Stars soft-funnel on personal.

## Choosing a pipeline

1. Need a document/carousel on LinkedIn → `cloudless-carousel-pipeline`.
2. Need motion without GPU/AI-video wait → `branded-video-pipeline`.
3. Need photoreal/animated vertical clip (TikTok, reels) → ComfyUI LTX/Wan workflow.
4. Need a quick branded still → carousel pipeline single slide or FLUX + brand compose.
5. Always end at `media-qa-jupyter` QA before upload; then draft-first publish.
