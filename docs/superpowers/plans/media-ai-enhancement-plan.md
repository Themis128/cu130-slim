# Media AI Enhancement Plan — SocialAuto

> **Goal**: Upgrade the media library with commercial-grade AI image processing: background removal, upscaling, smart crop, platform-specific resizing, quality scoring, accessibility alt-text, and batch processing — all Cloudflare-first and free-first.

---

## Market Research — What Commercial Media AI Apps Do

### Category 1: AI Image Enhancement
**Products**: EnhanceCraft, Imagic AI, Perfectly Clear, Upsampler, IterationLayer

| Feature | What they do |
|---------|-------------|
| AI Upscaling | 2x/4x/8x upscaling with Real-ESRGAN, GFPGAN for faces |
| Background Removal | RMBG-1.4, rembg models for clean cutouts |
| Smart Crop | AI detects faces/people/subjects and centers crop |
| Face Restoration | GFPGAN/CodeFormer for blurry/damaged faces |
| Denoise/Deblur | Remove JPEG artifacts, motion blur, focus blur |
| Dehaze | Remove atmospheric haze/fog |
| Old Photo Restore | Face repair, colorization, damage repair |
| Background Replace | Replace with color, image, or AI-generated scene |
| Blur Background | Keep subject sharp, blur background (portrait mode) |
| Batch Processing | Process 50+ images at once, download as ZIP |

### Category 2: AI Media Organization
**Products**: Captionator.AI, Uplifted, Coactive, ImageKit DAM, CKBox

| Feature | What they do |
|---------|-------------|
| Auto-tagging | AI assigns tags automatically on upload (already in SocialAuto) |
| Auto-caption | One-sentence description per image (already in SocialAuto) |
| Semantic search | Find assets by describing content in natural language (already via Chroma) |
| Object detection | Detect and label objects, scenes, faces |
| Blur scoring | Detect blurry images automatically |
| Quality scoring | Score image quality (brightness, contrast, sharpness) |
| Custom tags | Learn brand-specific tags (e.g. product names, personas) |
| Video tagging | Tag video content scene-by-scene |
| Audio transcription | Transcribe audio/video content |
| Approval workflows | Multi-stage review with SLA timers |

### Category 3: Social Media Image Optimization
**Products**: Upsampler, SnapResize AI, IterationLayer

| Feature | What they do |
|---------|-------------|
| Platform presets | Auto-resize for Instagram (1:1, 4:5, 9:16), LinkedIn (1200x627), Twitter (1200x675), TikTok (9:16), Facebook (1200x630) |
| Smart crop per platform | AI detects subject and crops differently for each platform |
| High-DPI optimization | Generate 2x/3x versions for retina displays |
| Watermark | Add customizable watermarks |
| Format conversion | JPEG, PNG, WebP, AVIF, HEIF |
| Compress to target | Specify target file size, auto-compress |
| Story/Reel safe zones | Respect platform safe areas for text/CTAs |

---

## What SocialAuto Already Has

| Capability | Status | Location |
|-----------|--------|----------|
| AI auto-tagging (CF llama-4-scout + moondream + Ollama) | ✅ Done | `app/services/media_ai.py` |
| AI captioning (CF vision models) | ✅ Done | `app/services/media_ai.py` |
| Spell/grammar correction for media text fields | ✅ Done | `app/services/media_spellcheck.py` |
| Semantic search via ChromaDB | ✅ Done | `app/services/chroma_client.py` |
| Similar asset discovery | ✅ Done | `app/services/media_ai.py:get_similar_assets` |
| AI image generation (FLUX schnell) | ✅ Done | `app/api/media.py:generate_image` |
| Image downscaling on upload | ✅ Done | `app/services/media_storage.py:downscale_image_bytes` |
| R2 → MinIO → local disk storage fallback | ✅ Done | `app/services/media_storage.py` |
| Media collections | ✅ Done | `app/models/content.py:MediaCollection` |
| Celery auto-tag task | ✅ Done | `app/worker/tasks/media.py` |
| Image format conversion (HEIC/AVIF → PNG) | ✅ Done | `app/api/media.py:view_media` |

### What's Missing (the gaps this plan fills)

1. **No background removal** — can't remove backgrounds for product shots or portraits
2. **No upscaling** — can't enlarge small images for high-DPI displays
3. **No smart crop** — can't auto-crop to platform-specific aspect ratios
4. **No platform presets** — no Instagram/LinkedIn/Twitter/TikTok resize presets
5. **No face detection** — can't detect faces for smart crop or privacy blurring
6. **No quality scoring** — can't detect blurry/dark/overexposed images
7. **No AI alt text** — captions exist but aren't accessibility-focused
8. **No batch processing** — can't process multiple images at once
9. **No format conversion API** — can't convert between JPEG/PNG/WebP/AVIF
10. **No watermark** — can't add brand watermarks
11. **No image enhancement** — no denoise, deblur, dehaze, face restoration
12. **No before/after preview** — can't compare original vs enhanced

---

## Implementation Plan

### Phase 1 — Image Transformation Service (platform presets, resize, crop, format)
**New file**: `app/services/image_transform.py`

```python
# Platform-specific presets
PLATFORM_PRESETS = {
    "instagram_square": {"width": 1080, "height": 1080, "ratio": "1:1"},
    "instagram_portrait": {"width": 1080, "height": 1350, "ratio": "4:5"},
    "instagram_story": {"width": 1080, "height": 1920, "ratio": "9:16"},
    "linkedin_post": {"width": 1200, "height": 627, "ratio": "1.91:1"},
    "linkedin_carousel": {"width": 1080, "height": 1080, "ratio": "1:1"},
    "twitter_post": {"width": 1200, "height": 675, "ratio": "16:9"},
    "twitter_card": {"width": 1200, "height": 628, "ratio": "1.91:1"},
    "facebook_post": {"width": 1200, "height": 630, "ratio": "1.91:1"},
    "tiktok_cover": {"width": 1080, "height": 1920, "ratio": "9:16"},
    "threads_post": {"width": 1080, "height": 1080, "ratio": "1:1"},
    "og_image": {"width": 1200, "height": 630, "ratio": "1.91:1"},
}

async def transform_image(
    image_bytes: bytes,
    preset: str | None = None,
    width: int | None = None,
    height: int | None = None,
    format: str = "jpeg",  # jpeg, png, webp, avif
    quality: int = 85,
    fit: str = "cover",  # cover, contain, crop
) -> tuple[bytes, str]:
    """Transform image with platform presets or custom dimensions."""
```

### Phase 2 — AI Background Removal
**New file**: `app/services/image_enhance.py`

Uses Cloudflare Workers AI `@cf/baai/baseten-lightning-iris` or rembg model.
Fallback: local Pillow with `rembg` package if installed.

```python
async def remove_background(image_bytes: bytes) -> tuple[bytes, str]:
    """Remove image background using Cloudflare Workers AI."""
    # Primary: CF Workers AI rembg model
    # Fallback: Ollama or local rembg package
```

### Phase 3 — AI Upscaling
Uses Cloudflare Workers AI Real-ESRGAN model.

```python
async def upscale_image(image_bytes: bytes, scale: int = 2) -> tuple[bytes, str]:
    """Upscale image 2x/4x using Cloudflare Workers AI Real-ESRGAN."""
```

### Phase 4 — Smart Crop with Face Detection
Uses Pillow's built-in face detection or Cloudflare vision model to find subjects.

```python
async def smart_crop(image_bytes: bytes, target_width: int, target_height: int) -> bytes:
    """Crop image focusing on the most important subject."""
    # 1. Detect faces/subjects using CF vision model
    # 2. Calculate crop region that includes the subject
    # 3. Crop and resize to target dimensions
```

### Phase 5 — Image Quality Scoring
Local computation using Pillow (no AI needed).

```python
def score_image_quality(image_bytes: bytes) -> ImageQualityScore:
    """Score image quality: blur, brightness, contrast, sharpness."""
    # Uses Laplacian variance for blur detection
    # Uses histogram analysis for brightness/contrast
    # Returns 0-100 score with breakdown
```

### Phase 6 — AI Alt Text Generation
Uses existing CF vision models but with accessibility-focused prompt.

```python
async def generate_alt_text(image_bytes: bytes) -> str:
    """Generate accessibility-focused alt text for screen readers."""
    # Prompt: "Describe this image for a screen reader in under 125 characters.
    #          Focus on what the image conveys, not what it looks like."
```

### Phase 7 — Batch Processing
Celery task that processes multiple assets.

```python
@shared_task
def batch_enhance_task(asset_ids: list[str], operations: list[dict]) -> None:
    """Apply multiple operations to multiple assets."""
```

### Phase 8 — Frontend Enhancement Studio
New page: `frontend/app/(dashboard)/media/enhance/[id]/page.tsx`

- Before/after preview slider
- Operation buttons: Remove BG, Upscale 2x, Smart Crop, Format Convert
- Platform preset selector for resize
- Quality score display
- Batch select from media library → apply operation
```

---

## Cloudflare-First Strategy

| Capability | CF Service | Model | Free Tier | Fallback |
|-----------|-----------|-------|-----------|----------|
| Background removal | Workers AI | `@cf/baai/baseten-lightning-iris` | 10K req/day | Local `rembg` |
| Upscaling | Workers AI | `@cf/jcmonnier/real-esrgan` | 10K req/day | Pillow LANCZOS |
| Smart crop (face detect) | Workers AI | llama-4-scout vision | 10K req/day | Pillow face detect |
| Alt text | Workers AI | llama-4-scout vision | 10K req/day | Ollama llava |
| Quality scoring | Local | Pillow (no AI) | Unlimited | N/A |
| Resize/crop/format | Local | Pillow (no AI) | Unlimited | N/A |
| Batch processing | Celery | Local workers | Unlimited | N/A |

---

## New Files Summary

### Backend
| File | Purpose |
|------|---------|
| `app/services/image_transform.py` | Platform presets, resize, crop, format conversion |
| `app/services/image_enhance.py` | Background removal, upscaling, smart crop, quality scoring, alt text |
| `app/api/media_enhance.py` | Enhancement API endpoints |
| `app/worker/tasks/media_enhance.py` | Batch processing Celery tasks |

### Frontend
| Path | Purpose |
|------|---------|
| `app/(dashboard)/media/enhance/[id]/page.tsx` | Enhancement studio with before/after preview |
| `src/components/media/EnhancePanel.tsx` | Reusable enhancement panel |
| `src/components/media/QualityScore.tsx` | Quality score badge |
| `src/components/media/PlatformPresetSelector.tsx` | Platform resize preset picker |
