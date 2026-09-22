---
name: post-media-correctness
description: use when creating, scheduling, publishing, or reviewing SocialAuto posts — every post must have the correct media for its type
---
# Post media correctness

Standing product rule: every post must always have the correct media for its type (carousel, single post, infographic, video/Reel/TikTok, etc.).

## Checklist before schedule/publish
1. `media_ids` non-empty when the format requires media (never accidentally empty).
2. Asset type matches format (image vs video vs multi-slide carousel).
3. Carousel: all slides present and ordered; respect platform slide limits.
4. Infographic/poster: use the infographic renderer path so text is readable.
5. Instagram images: JPEG-compatible public URL (`/api/v1/media/view?format=jpeg` for WebP/HEIC/AVIF).
6. TikTok/Reels video: meet FPS/codec rules (≥23 FPS, H.264 preferred).

If media is wrong or missing: fix/regenerate first. Do not publish broken media.
