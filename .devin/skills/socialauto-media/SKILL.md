---
name: socialauto-media
description: >-
  Manage the SocialAuto media library: list, upload, view, delete, and enhance
  media assets (images, videos, PDFs, audio). Use when uploading media for posts,
  viewing media in the library, generating AI images, or running AI enhancement
  (resize, upscale, remove-bg, smart-crop, alt-text). Covers /api/v1/media/*
  and /api/v1/media/enhance/* endpoints.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# SocialAuto Media Library

Manage media assets for social posts through the SocialAuto API.

## When to use

- List or search media library assets
- Upload an image/video/PDF/audio file
- View or download a media asset
- Delete a media asset
- Generate an AI image (Cloudflare Workers AI / FLUX schnell)
- AI-enhance an image (resize, upscale, remove background, smart crop, alt text)
- Get media asset details (dimensions, MIME type, tags, AI caption)

## API base

```
http://127.0.0.1:8083/api/v1/media          # core media endpoints
http://127.0.0.1:8083/api/v1/media/enhance  # AI enhancement endpoints
```

## Authentication

Bearer token from `POST /api/v1/auth/login`. Helper scripts handle login
by reading `.env` for `SOCIAL_ADMIN_EMAIL` / `SOCIAL_ADMIN_PASSWORD`.

## Core endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/media` | List media (filter by type, sort, search, paginate) |
| GET | `/media/{id}` | Get a single media asset |
| GET | `/media/view?path=<storage_path>` | Serve media for display (unauthenticated) |
| POST | `/media/upload` | Upload a file (multipart/form-data) |
| DELETE | `/media/{id}` | Delete a media asset |

## AI image generation

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/ai/generate-image` | Generate an image via Cloudflare Workers AI |
| POST | `/ai/generate-image-pipeline` | Generate + save to media library |
| POST | `/ai/generate-image-flux` | Generate via FLUX schnell |
| GET | `/ai/generate-image/{job_id}` | Check async generation status |

## AI enhancement endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/media/enhance/resize/{id}` | Resize with platform preset |
| POST | `/media/enhance/upscale/{id}` | Upscale 2x/4x |
| POST | `/media/enhance/remove-bg/{id}` | Remove background (CF segmentation) |
| POST | `/media/enhance/smart-crop/{id}` | AI subject detection crop |
| POST | `/media/enhance/alt-text/{id}` | WCAG alt text generation |
| POST | `/media/enhance/quality-score/{id}` | Blur/brightness/contrast score |
| POST | `/media/enhance/batch` | Batch process up to 50 assets |

## Supported file types

- **Images**: JPEG, PNG, WebP, AVIF, GIF
- **Videos**: MP4, WebM, QuickTime
- **Documents**: PDF (carousel slides)
- **Audio**: MP3, WAV, M4A, AAC, OGG, FLAC

## Media view URL

The `/media/view` endpoint is unauthenticated and serves files directly:

```
/api/v1/media/view?path=2026/09/01/carousel-xxx/slide.pdf
```

The frontend proxies this through Next.js rewrites so the browser can
access it at `/api/v1/media/view?path=...` on port 8082.

## Tool scripts

Run from repo root `cu130-slim/`:

```bash
# List media assets
.devin/skills/socialauto-media/scripts/list-media.sh [--type image|video|generated] [--limit 20]

# Upload a file
.devin/skills/socialauto-media/scripts/upload-media.sh <file-path> [--alt "description"] [--tags "tag1,tag2"]

# Get media asset details
.devin/skills/socialauto-media/scripts/get-media.sh <media-id>

# Delete a media asset
.devin/skills/socialauto-media/scripts/delete-media.sh <media-id>

# Generate an AI image and save to library
.devin/skills/socialauto-media/scripts/generate-image.sh "prompt text" [--model "@cf/black-forest-labs/flux-1-schnell"]

# View a media asset URL (prints the view URL)
.devin/skills/socialauto-media/scripts/media-url.sh <storage-path>
```

## Frontend media library

The media library UI is at `http://localhost:8082/media` and supports:
- Grid view with type filters and search
- Image viewer with zoom/pan (ImageViewerDialog)
- PDF viewer with page navigation (PDF.js)
- Audio player
- Video player
- AI Enhancement Studio at `/media/enhance/{id}`
