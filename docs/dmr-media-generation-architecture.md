# SocialAuto Media Generation Architecture (Complete)

## Overview

All media that SocialAuto can produce, the model/engine used for each, and the
fallback chain. Based on real benchmarks on this hardware (WSL2, RTX 3070 8GB).

```
┌─────────────────────────────────────────────────────────────────────┐
│                     SocialAuto Media Engine                         │
│                                                                     │
│  TEXT          IMAGE          VIDEO         INFOGRAPHIC    AUDIO     │
│  ─────         ─────          ─────         ──────────     ─────     │
│  Captions      Photos         TikTok        LinkedIn       Trans-    │
│  Hashtags      Carousels      videos        carousels      cription  │
│  NLP fixes     OG images      Reels         (PIL+PDF)                │
│  SEO           Story covers   Slideshows    Charts                   │
│  Titles        Profile pics                                          │
│  Alt text      Cover photos                                          │
│  AI content    Backgrounds                                           │
│                Enhancements                                          │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
          ┌────────────────┼────────┬──────────────┐
          ▼                ▼        ▼              ▼
   ┌──────────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
   │ Cloudflare   │ │ local-   │ │ DMR      │ │ PIL      │
   │ Workers AI   │ │ diffusers│ │ (CPU)    │ │ (local)  │
   │ (cloud)      │ │ (GPU)    │ │          │ │          │
   │              │ │          │ │          │ │          │
   │ TEXT PRIMARY │ │ IMAGE    │ │ TEXT     │ │ COMPOSE  │
   │ IMAGE FALLBACK│ │ PRIMARY │ │ HELPER   │ │ RENDER   │
   │ VISION       │ │          │ │ VISION   │ │ PDF      │
   └──────────────┘ └──────────┘ └──────────┘ └──────────┘
```

## Network Endpoints

### From inside Docker containers

| Service | URL | Purpose |
|---------|-----|---------|
| DMR (llama.cpp) | `http://host.docker.internal:12434/engines/llama.cpp/v1` | Text/vision/embeddings |
| local-diffusers | `http://local-diffusers:7860` | Image generation (GPU) |
| Cloudflare AI | `https://api.cloudflare.com/client/v4/accounts/{id}/ai/run/` | Text + image (cloud) |
| social-api | `http://localhost:8083` | App API |
| LanguageTool | `http://languagetool:8010` | Spell/grammar check |

### From the host (WSL2)

| Service | URL | Notes |
|---------|-----|-------|
| social-api | `http://localhost:8083` | Port mapped |
| DMR CLI | `docker model status/list/run` | CLI only, no HTTP from host |
| local-diffusers | Not accessible | No port mapped to host |

## Media Type Catalog

### 1. LinkedIn Carousel (PDF Infographic)

**What:** Multi-slide 1080x1080 branded infographic PDF for LinkedIn Company Page.

| Step | Engine | Model | Time | Fallback |
|------|--------|-------|------|----------|
| Slide copy (JSON) | CF Workers AI | llama-3.3-70b | 0.5s | DMR ai/llama3.2 |
| NLP plain-English | DMR | ai/llama3.2 | 3-17s/call | CF Workers AI |
| Title generation | DMR | ai/llama3.2 | ~12s | CF Workers AI |
| Background image | local-diffusers | SD 1.5 (fp16) | ~5s/slide | CF FLUX schnell |
| Brand compose | PIL (local) | — | <1s | N/A |
| PDF assembly | PIL (local) | — | <1s | N/A |
| Spellcheck | LanguageTool | — | <1s | N/A |
| SEO scoring | CF Workers AI | llama-3.3-70b | 0.5s | DMR ai/llama3.2 |

**Output:** PDF (1080x1080 per page), stored in R2/MinIO.
**Endpoint:** `POST /api/v1/ai/run-carousel-and-publish`
**Slide types:** cover, content, stat, cta
**Infographic motifs:** servers, pricing, process, comparison, KPI, cover, cta

### 2. Single Image (Text-to-Image)

**What:** AI-generated photo from a text prompt, brand-enhanced.

| Step | Engine | Model | Time | Fallback |
|------|--------|-------|------|----------|
| Prompt enhancement | Brand system | DB visual identity | <1s | N/A |
| Image generation | local-diffusers | SD 1.5 (fp16) | ~5s | CF FLUX schnell |
| Dedup check | ChromaDB | — | <1s | N/A |
| Persist to library | R2/MinIO | — | <1s | local disk |

**Output:** PNG/JPEG/WebP, stored in Media Library.
**Endpoint:** `POST /api/v1/ai/generate-image` or `POST /api/v1/media/generate-image`
**Providers:** local-diffusers (primary), cloudflare (fallback), nvidia-flux (manual)

### 3. FLUX Pipeline Image (High Quality)

**What:** Two-stage FLUX generation: text-to-image → image-to-image enhancement.

| Step | Engine | Model | Time | Fallback |
|------|--------|-------|------|----------|
| Base generation | NVIDIA FLUX | flux.1-dev | ~10s | CF FLUX schnell |
| Enhancement | NVIDIA FLUX | flux.1-kontext-dev | ~10s | N/A |
| Similarity check | ChromaDB | — | <1s | N/A |
| Persist | R2/MinIO | — | <1s | local disk |

**Output:** 1024x1024 PNG, stored in Media Library.
**Endpoint:** `POST /api/v1/ai/generate-image-pipeline`

### 4. Platform-Optimized Image (Transform)

**What:** Resize/crop/convert existing image for a specific platform.

| Step | Engine | Method | Time |
|------|--------|--------|------|
| Resize/crop | PIL (local) | LANCZOS | <1s |
| Format convert | PIL (local) | JPEG/PNG/WebP/AVIF | <1s |
| Watermark | PIL (local) | Text overlay | <1s |

**Platform presets (17 total):**

| Platform | Preset | Dimensions |
|----------|--------|------------|
| Instagram | Square | 1080x1080 |
| Instagram | Portrait | 1080x1350 |
| Instagram | Landscape | 1080x566 |
| Instagram | Story/Reel | 1080x1920 |
| LinkedIn | Post | 1200x627 |
| LinkedIn | Carousel | 1080x1080 |
| LinkedIn | Cover | 1584x396 |
| Twitter/X | Post | 1200x675 |
| Twitter/X | Card | 1200x628 |
| Twitter/X | Header | 1500x500 |
| Facebook | Post | 1200x630 |
| Facebook | Cover | 820x312 |
| Facebook | Story | 1080x1920 |
| TikTok | Cover | 1080x1920 |
| Threads | Post | 1080x1080 |
| OG | Image | 1200x630 |
| Generic | Square | 1080x1080 / 2048x2048 |

**Endpoint:** `POST /api/v1/media/enhance/transform`

### 5. Image Enhancement (AI)

**What:** AI-powered image improvements.

| Enhancement | Engine | Method | Time | Fallback |
|-------------|--------|--------|------|----------|
| Upscale 2x/4x | PIL (local) | LANCZOS + sharpen | ~2s | N/A |
| Remove background | CF Workers AI | Segmentation model | ~3s | N/A |
| Smart crop | DMR vision | ai/qwen3-vl subject detection | ~10s | Center crop |
| Alt text generation | DMR vision | ai/qwen3-vl caption | ~10s | CF Workers AI |
| Quality scoring | PIL (local) | Sharpness/contrast/brightness | <1s | N/A |

**Endpoints:** `POST /api/v1/media/enhance/{type}`

### 6. TikTok Video / Slideshow

**What:** Video or photo carousel published to TikTok.

| Step | Engine | Method | Time |
|------|--------|--------|------|
| Video upload | TikTok API | FILE_UPLOAD (bytes) | ~10s |
| Photo post | TikTok API | PULL_FROM_URL (up to 35 photos) | ~5s |
| Draft → inbox | TikTok API | MEDIA_UPLOAD mode | ~2s |

**Output:** Video MP4 or photo carousel on TikTok.
**Endpoint:** Publishing via `POST /api/v1/content/posts/{id}/publish`
**Limit:** 5 pending shares per 24h (spam protection).

### 7. Instagram Photo / Carousel

**What:** Single photo or multi-photo carousel for Instagram.

| Step | Engine | Method | Time |
|------|--------|--------|------|
| Image prep | PIL (local) | Platform preset transform | <1s |
| Upload | Instagram API | Rupload photo upload | ~3s/photo |
| Carousel | Instagram API | Multi-photo sidecar (up to 10) | ~10s |
| Caption | CF Workers AI | AI-generated caption | 0.5s |

**Output:** Instagram post (single photo or carousel).
**Endpoint:** Publishing via `POST /api/v1/content/posts/{id}/publish`

### 8. Facebook Post / Photo / Cover

**What:** Text, single photo, multi-photo, or cover image for Facebook.

| Step | Engine | Method | Time |
|------|--------|--------|------|
| Image prep | PIL (local) | Platform preset transform | <1s |
| Text post | Facebook API | /feed | ~2s |
| Photo post | Facebook API | /photos | ~3s |
| Multi-photo | Facebook API | /feed with attached_media | ~5s |

**Output:** Facebook post on User account or Page.

### 9. LinkedIn Post / Image / Carousel

**What:** Text, single image, or PDF carousel for LinkedIn (personal or Company Page).

| Step | Engine | Method | Time |
|------|--------|--------|------|
| Text post | LinkedIn API | /posts | ~2s |
| Image post | LinkedIn API | /images + /posts | ~5s |
| PDF carousel | LinkedIn API | /documents + /posts | ~10s |
| Company Page | LinkedIn API | urn:li:organization:{id} | ~2s |

**Output:** LinkedIn post (personal feed or Company Page).

### 10. Twitter/X Post

**What:** Text + image post for Twitter/X.

| Step | Engine | Method | Time |
|------|--------|--------|------|
| Image upload | Twitter API | /media/upload | ~3s |
| Post | Twitter API | /tweets | ~2s |

**Output:** Tweet with optional image.

### 11. Threads Post

**What:** Text + image post for Threads.

| Step | Engine | Method | Time |
|------|--------|--------|------|
| Image upload | Threads API | /media | ~3s |
| Post | Threads API | /threads | ~2s |

**Output:** Threads post.

### 12. AI-Generated Text Content

**What:** Captions, hashtags, SEO content, improved text.

| Task | Engine | Model | Time | Fallback |
|------|--------|-------|------|----------|
| Caption generation | CF Workers AI | llama-3.3-70b | 0.5s | DMR ai/llama3.2 |
| Hashtag suggestions | CF Workers AI | llama-3.3-70b | 0.5s | DMR ai/llama3.2 |
| Content improvement | CF Workers AI | llama-3.3-70b | 0.5s | DMR ai/llama3.2 |
| SEO scoring | CF Workers AI | llama-3.3-70b | 0.5s | DMR ai/llama3.2 |
| Spellcheck | LanguageTool | — | <1s | N/A |
| Workflow templates | CF Workers AI | llama-3.3-70b | 0.5s | DMR ai/llama3.2 |

**Endpoints:**
- `POST /api/v1/ai/generate-content`
- `POST /api/v1/ai/generate-hashtags`
- `POST /api/v1/ai/improve-content`
- `POST /api/v1/ai/seo`
- `POST /api/v1/ai/spellcheck`

### 13. Audio Transcription

**What:** Transcribe audio/video files to text.

| Step | Engine | Model | Time |
|------|--------|-------|------|
| Transcription | CF Workers AI | whisper-large-v3-turbo | ~5s/min |

**Endpoint:** `POST /api/v1/ai/transcribe`

### 14. Vision / Image Analysis

**What:** Analyze images for alt text, subject detection, content moderation.

| Task | Engine | Model | Time | Fallback |
|------|--------|-------|------|----------|
| Alt text | DMR vision | ai/qwen3-vl | ~10s | CF Workers AI |
| Subject detection | DMR vision | ai/qwen3-vl | ~10s | Center crop |
| Image description | CF Workers AI | llama-3.2-3b-vision | ~2s | DMR ai/qwen3-vl |

### 15. Embeddings (Semantic Search)

**What:** Generate embeddings for media similarity search and dedup.

| Task | Engine | Model | Time |
|------|--------|-------|------|
| Text embedding | DMR | ai/qwen3-embedding | ~2s |
| Similarity search | ChromaDB / CF Vectorize | — | <1s |

## Task → Model Assignment (Master Table)

```
┌──────────────────────────────────────────────────────────────────────┐
│  Task                    │  Primary           │  Fallback            │
├─────────────────────────┼───────────────────┼──────────────────────┤
│  Carousel copy (JSON)   │  CF Workers AI    │  DMR ai/llama3.2     │
│  NLP plain-English fix  │  DMR ai/llama3.2  │  CF Workers AI       │
│  Title generation       │  DMR ai/llama3.2  │  CF Workers AI       │
│  Caption generation     │  CF Workers AI    │  DMR ai/llama3.2     │
│  Hashtag generation     │  CF Workers AI    │  DMR ai/llama3.2     │
│  SEO scoring            │  CF Workers AI    │  DMR ai/llama3.2     │
│  Content improvement    │  CF Workers AI    │  DMR ai/llama3.2     │
│  Spellcheck             │  LanguageTool     │  N/A                 │
│  Background image       │  local-diffusers  │  CF FLUX schnell     │
│  Single image gen       │  local-diffusers  │  CF FLUX schnell     │
│  FLUX pipeline image    │  NVIDIA FLUX      │  CF FLUX schnell     │
│  Image upscale          │  PIL (LANCZOS)    │  N/A                 │
│  Remove background      │  CF Workers AI    │  N/A                 │
│  Smart crop             │  DMR ai/qwen3-vl  │  Center crop         │
│  Alt text generation    │  DMR ai/qwen3-vl  │  CF Workers AI       │
│  Image transform        │  PIL (local)      │  N/A                 │
│  PDF assembly           │  PIL (local)      │  N/A                 │
│  Brand compose          │  PIL (local)      │  N/A                 │
│  Audio transcription    │  CF Workers AI    │  N/A                 │
│  Embeddings             │  DMR qwen3-embed  │  CF Workers AI       │
│  Similarity search      │  ChromaDB         │  CF Vectorize        │
└─────────────────────────┴───────────────────┴──────────────────────┘
```

## Engine Details

### Cloudflare Workers AI (Text + Image Cloud)

**Text model:** `@cf/meta/llama-3.3-70b-instruct-fp8-fast` (free, 10k neurons/day)
**Image model:** `@cf/black-forest-labs/flux-1-schnell` (free, 4 steps)
**Vision model:** `@cf/meta/llama-3.2-3b-vision` (image analysis)
**Whisper model:** `@cf/openai/whisper-large-v3-turbo` (audio transcription)
**Segmentation:** `@cf/unum/u2net-zero` (background removal)

**Why primary for text:** 70B model, 0.5s response, free tier.

### Local Diffusers (Image GPU)

**Container:** `local-diffusers` (GPU, CUDA, RTX 3070 8GB)
**Endpoint (container):** `http://local-diffusers:7860/v1/images/generations`
**Model:** `stable-diffusion-v1-5/stable-diffusion-v1-5` (fp16, ~2GB VRAM)
**Health:** `{"status":"ok","model_loaded":true,"vram":"2.03GB allocated"}`

**Why primary for images:** Local, no rate limits, GPU-accelerated, ~5s per image.

### DMR (Text + Vision Helper)

**Endpoint (container):** `http://host.docker.internal:12434/engines/llama.cpp/v1`
**CLI (host):** `docker model status/list/run/pull`
**Text model:** `ai/llama3.2` (3.21B, 1.87 GiB)
**Vision model:** `ai/qwen3-vl` (8.19B, 4.79 GiB)
**Embedding model:** `ai/qwen3-embedding`

**Timeouts:** 180s for schema/JSON, 30s for plain text, 120s for embeddings.

### PIL (Local Compositing)

All final infographic text rendered by PIL with verified fonts:
- No AI text in images — eliminates hallucinated text
- Charts, tables, KPIs drawn programmatically
- Brand colors, fonts, layout from DB or defaults
- Auto-shrink fonts, text wrapping

## DMR Models Available

| Model | Params | Size | Use Case |
|-------|--------|------|----------|
| `ai/llama3.2` | 3.21B | 1.87 GiB | **Fast text (NLP, titles)** |
| `ai/qwen2.5` | 7.62B | 4.36 GiB | Heavy JSON (slow on CPU) |
| `ai/qwen3:8b` | 8B | 4.79 GiB | Reasoning + JSON (/no_think) |
| `ai/qwen3-vl` | 8.19B | 4.79 GiB | **Vision (alt text, smart crop)** |
| `ai/qwen3-embedding` | — | — | **Embeddings** |
| `ai/gemma3` | 4B | 8.15 GiB | Reasoning (untested) |
| `ai/phi4` | 14B | 9.05 GiB | Too large for CPU |
| `ai/smollm2` | 362M | 256 MiB | Tiny tasks (poor quality) |
| `ai/stable-diffusion` | — | 6.94 GB | Image gen (NOT on WSL2) |

## Storage Backends

| Backend | Priority | Use |
|---------|----------|-----|
| R2 (Cloudflare) | Primary | All generated media (PDFs, images) |
| MinIO (local S3) | Fallback | If R2 unavailable |
| Local disk | Last resort | If both cloud + MinIO down |

## Publishing Platforms

| Platform | Content Types | Method |
|----------|--------------|--------|
| LinkedIn | Text, image, PDF carousel | API + sidecar |
| Twitter/X | Text, image | API |
| Facebook | Text, photo, multi-photo | API + sidecar |
| Instagram | Photo, carousel, story | Graph API + instagrapi + sidecar |
| Threads | Text, image | API |
| TikTok | Video, photo carousel | Content Posting API |

## Verified Pipeline Output (2026-09-06)

First successful carousel with corrected architecture:

- **Post ID:** `01228daa-6663-4ecf-a180-8e0b46f1657a`
- **Media:** `n8n-cf-pipe_91243118.pdf` (330 KB, 3 pages, 1080x1080)
- **Title:** "Ditch the Cloud, Choose Cloudless"
- **SEO Score:** 82/100
- **NLP:** 7 fields rewritten by DMR ai/llama3.2
- **Storage:** R2 (Cloudflare)
- **Status:** Draft

### AI Usage Logs (actual run)

| Provider | Model | Calls | Success | Avg Latency |
|----------|-------|-------|---------|-------------|
| cloudflare | llama-3.3-70b | 1 | YES | 186s |
| dmr | ai/llama3.2 | 8 | YES | 3-17s |

## Monitoring

```bash
# DMR status (host CLI)
docker model status

# Local diffusers health (from container)
docker compose exec -T social-api python3 -c "
import httpx; print(httpx.get('http://local-diffusers:7860/health').json())
"

# Cloudflare DB health (from host)
curl http://localhost:8083/api/v1/cf-db/health

# API health (from host)
curl http://localhost:8083/health

# AI usage logs (from container)
docker compose exec -T social-api python3 -c "
import asyncio
from sqlalchemy import text
from app.db.session import async_session_maker
async def check():
    async with async_session_maker() as db:
        result = await db.execute(text(
            'SELECT created_at, provider, model, success, latency_ms FROM ai_usage_logs ORDER BY created_at DESC LIMIT 10'))
        for row in result:
            print(f'{row.created_at:%H:%M:%S} | {row.provider:12s} | {row.model:40s} | ok={row.success} | {row.latency_ms}ms')
asyncio.run(check())
"
```
