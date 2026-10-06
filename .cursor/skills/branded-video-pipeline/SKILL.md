---
name: branded-video-pipeline
description: >-
  Create Cloudless-branded social videos (LinkedIn Company Page, TikTok-ready)
  WITHOUT an AI video model: PIL brand-composed slides → ffmpeg zoom/crossfade
  slideshow → LinkedIn-spec QA → media upload + draft/publish. Use when making
  LinkedIn video posts, branded motion content, or when AI video gen
  (ComfyUI LTXV/Wan) is too slow or overkill. ~40s end-to-end, zero GPU.
---

# Branded Video Pipeline

## When to use

- LinkedIn Company Page video posts for cloudless.gr (proven live:
  `ugcPost:7513270473229561857`)
- Any branded square (1:1) video where legibility beats photorealism —
  LinkedIn autoplays **muted**, so the message must be readable on-screen
- When AI video/image generation is too slow: ComfyUI FLUX cold-loads
  300s+/slide under GPU contention; this path renders in seconds with
  zero GPU and zero inference

## The proven path (~40s total)

```
slides.json → compose_branded_slide(bg_img=None)  [social-api, PIL only]
           → ffmpeg zoompan+xfade slideshow        [social-worker-media]
           → ffprobe spec QA + slide-center frames
           → media upload + draft post → publish-now
```

The brand composer **is** the visual — no AI background needed. Real slides
ship `1080x1080`, dark navy + cyan `cloudless.gr` branding, footer strip.

## Tool

```bash
# Render + assemble + QA only (no upload) — always eyeball qa_frames/
.devin/skills/branded-video-pipeline/scripts/gen_branded_video.py \
    --spec slides.json --out /tmp/video.mp4

# + draft post for the Company Page (review before publishing)
... --upload --account 9c4451bb-e820-489f-8676-76ddbc788ffe \
    --caption-file caption.txt --hashtags CloudCostOptimization,cloudless,sofiakakkavacoach \
    --link https://cloudless.gr/contact

# + publish immediately (verify stored caption FIRST — auto-correct mangles)
... --publish
```

Flags: `--slide-seconds 5.0 --fade 0.8 --fps 25 --size 1080`
(linkedin sweet spot: 5-6 slides × 5s ≈ 26s — inside the 15–30s
recommended band), `--skip-render` reuses workdir PNGs,
`--workdir` overrides the scratch dir.

An example spec (the shipped offer video — DO NOT republish verbatim,
it is already live; use as a structural template):
`scripts/example-slides.json`

## Slide spec

```json
{"slides": [{
  "slide_type": "cover|content|stat|cta",
  "motif": "cover|servers|pricing|calendar|rocket|chat|stat|cta|compare",
  "title": "big headline (auto-shrinks >30/44/60 chars)",
  "body": "supporting copy (auto-truncates to fit)",
  "highlight": "pull-quote / big number / CTA url",
  "chart_data": {"label|attribution|heading|steps": "..."} | null
}]}
```

Motif rendering lives in `compose_branded_slide` → `_draw_infographic`
(`app/services/carousel_pipeline.py`). `motif` is optional — `_motif_for`
picks one by position — but pass it explicitly for predictable layouts.

### ⚠ Invented-metric trap (bit us once — verified frames before publish)

`servers`, `pricing`, `rocket` motifs render **built-in stat cards**
("85% reduction", "99.9% uptime", "80% ops saved", "<5 min deploy")
when `chart_data` is empty. These are NOT verified Cloudless numbers —
the rule is real claims only. The tool warns; either pass `chart_data`
overrides or pick `chat`/`stat`/`calendar`/`compare`.

`chart_data` shapes that work: `stat` → `{"label": ...}` +
`highlight`; `calendar` → `{"heading": ..., "steps": [{"when","what"}]}`;
`chat` → `{"attribution": ...}` + `highlight` as the quote.

## Kakkava slide arc (DAY 3 niche formula)

Hook (ICP pain) → WHO (the specific person) → PROBLEM (their words) →
RESULT (real, verifiable number) → CTA (one destination). Clarity
attracts; vagueness gets ignored. Write for one specific person, not a
generic audience.

## LinkedIn video spec (official docs — verified 2026-10-06)

- MP4, 75KB–500MB, 3s–30min, H.264 or VP8, ≤30fps, yuv420p
- Ratios: 16:9 / **1:1** / **4:5 (recommended)** / 9:16; 360–1920px/side
- **15–30s** recommended for full placement eligibility
- Autoplay is **muted** — on-screen text is the message; audio track is
  silent AAC (included for player compatibility)
- Upload lifecycle (server-side, `linkedin_api.create_video_post`):
  `initializeUpload` → PUT binary (**pre-signed URL 3xx-redirects to
  linkedin-ei.com — `follow_redirects` required, fixed in #338**) →
  `finalizeUpload` (mandatory even when `uploadToken` is "") → poll
  until `AVAILABLE` → Posts API. Failures show as
  `WAITING_UPLOAD` forever if ETags/redirects are mishandled.

## Host/container facts

- `ffmpeg`/`ffprobe` are NOT on the host — the tool runs them inside
  `social-worker-media` (and renders inside `social-api`). Both must be up.
- PIL compose: `compose_branded_slide(None, index=i, total=n, ...)`
  inside `social-api` — `bg_img=None` gives the pure brand canvas.
- API login/upload helpers: `.devin/skills/_lib/skill_http.py`
  (`social_api()`, `request()`, `upload()`).

## Hard rules

- **Never publish without inspecting `qa_frames/`** — check every slide:
  legible at 1080², correct text, real claims only, brand footer present.
- **Verify the stored caption** before publish — LanguageTool
  auto-correct can mangle `€`, proper nouns, `urn:li:` markup, domains.
  Reference the coach via `#sofiakakkavacoach` (lowercase) only — never
  in prose.
- **Draft first, publish second** — `--upload` creates a draft;
  `--publish` is a separate explicit step.
- **No duplicates** — do not repackage an already-published carousel as
  a video of the same slides (user rule). Repurposing is fine only for a
  genuinely different cut.
- Company Page for corporate content:
  `9c4451bb-e820-489f-8676-76ddbc788ffe` — never the personal profile.
- **Real numbers only** — cloudless.gr claims (€500+/mo hook,
  €15K–50K/yr recoverable, free 30-min audit) are verified; anything else
  needs a source.
- Never commit `.env`/secrets; `SOCIAL_ADMIN_*` stays in env.

## AI video models (when photoreal motion IS the point)

- ComfyUI LTXV 2B GGUF (`tiktok-video-ltxv-gguf` workflow) — fastest real
  video model on the 8GB card; supports i2v from `image_asset_id`
- Wan2.1 1.3B — smaller but ~7.3s/step, slower in practice
- `POST /api/v1/media/generate-video` → `generate_video_asset_task`
  (media queue, 30min limit). Cold GPU loads make this minutes-to-
  timeout; prefer this PIL path for text-first content.

## Failure notes (observed 2026-10-06)

- `WAITING_UPLOAD` + no error → the #338 redirect bug (fixed, merged).
- ComfyUI lowvram mode commits FLUX to full offload when DMR models are
  resident → 300s timeouts. Keep-warm (#339) holds llama3.2+4B resident;
  for video slides just skip AI backgrounds entirely.
- LanguageTool down is non-fatal (NLP auto-correct skips); verify copy
  manually then.
