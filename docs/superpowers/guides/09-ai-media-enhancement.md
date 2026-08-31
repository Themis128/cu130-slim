# AI Media Enhancement Studio

Enhance your media assets with AI-powered background removal, upscaling, smart crop, quality scoring, and platform-specific resizing.

## Prerequisites

- You have uploaded images to the Media Library.
- Cloudflare Workers AI is configured for AI operations (background removal, smart crop, alt text).
- Non-AI operations (resize, crop, format conversion, quality scoring, watermark) work without any AI service.

## Accessing the Enhancement Studio

1. Go to **Media Library** in the sidebar.
2. Hover over any image and click the **wand icon** (AI Enhance).
3. You will be taken to the AI Enhancement Studio for that asset.

## Available Operations

### Platform Resize (no AI needed)
Resize images to exact dimensions required by each social media platform:
- Instagram: Square (1080×1080), Portrait (1080×1350), Story/Reel (1080×1920)
- LinkedIn: Post (1200×627), Carousel (1080×1080), Cover (1584×396)
- Twitter/X: Post (1200×675), Card (1200×628), Header (1500×500)
- Facebook: Post (1200×630), Cover (820×312), Story (1080×1920)
- TikTok: Cover (1080×1920)
- Threads: Post (1080×1080)
- Open Graph: (1200×630)

1. Select a platform preset from the dropdown.
2. Click **Resize**.
3. Preview the result and click **Download** to save.

### AI Background Removal
Remove image backgrounds using Cloudflare Workers AI segmentation models.

1. Click **Remove Background**.
2. The AI processes the image and returns a transparent PNG.
3. Download the result.

### AI Upscale
Enlarge images 2x or 4x with LANCZOS resampling and sharpening.

1. Click **2x** or **4x**.
2. The enhanced image appears in the preview.
3. Download the result.

### AI Smart Crop
Crop to target dimensions with AI subject detection — the crop centers on the most important part of the image.

1. Enter target **Width** and **Height**.
2. Click **Smart Crop**.
3. The AI detects the subject and crops accordingly.

### Format Conversion
Convert between JPEG, PNG, WebP, and AVIF.

1. Click any format button (WEBP, JPEG, PNG, AVIF).
2. The converted image appears in the preview.

### Compress to Target Size
Reduce file size to a specific target (in KB).

### Watermark
Add a text watermark to protect your images.

1. Enter watermark text (e.g. `© Cloudless`).
2. Click **Add Watermark**.

### AI Alt Text Generation
Generate WCAG-compliant alt text for accessibility.

1. Click **Generate Alt Text**.
2. The AI produces a concise description (under 125 characters) suitable for screen readers.

### Quality Score
Automatically displayed when you open the Enhancement Studio. Shows:
- **Overall** score (0-100)
- **Sharpness** — detects blurry images
- **Brightness** — detects too dark/bright images
- **Contrast** — detects flat/low-contrast images
- **Issues** — actionable warnings

## Batch Processing

You can process multiple images at once via the API:

```bash
curl -X POST http://localhost:8083/api/v1/media/enhance/batch \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "asset_ids": ["uuid1", "uuid2", "uuid3"],
    "operation": "resize",
    "params": {"preset": "instagram_square"}
  }'
```

Supported batch operations: `resize`, `convert`, `compress`, `upscale`, `remove_bg`, `smart_crop`, `alt_text`.

## API Reference

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/api/v1/media/enhance/presets` | List platform resize presets |
| `GET` | `/api/v1/media/enhance/assets/{id}/info` | Get image dimensions and format |
| `GET` | `/api/v1/media/enhance/assets/{id}/quality` | Score image quality |
| `POST` | `/api/v1/media/enhance/assets/{id}/resize` | Resize to preset or custom dimensions |
| `POST` | `/api/v1/media/enhance/assets/{id}/crop` | Crop to specific region |
| `POST` | `/api/v1/media/enhance/assets/{id}/convert` | Convert format |
| `POST` | `/api/v1/media/enhance/assets/{id}/compress` | Compress to target size |
| `POST` | `/api/v1/media/enhance/assets/{id}/watermark` | Add text watermark |
| `POST` | `/api/v1/media/enhance/assets/{id}/upscale` | Upscale 2x or 4x |
| `POST` | `/api/v1/media/enhance/assets/{id}/remove-background` | AI background removal |
| `POST` | `/api/v1/media/enhance/assets/{id}/smart-crop` | AI smart crop |
| `POST` | `/api/v1/media/enhance/assets/{id}/alt-text` | AI alt text generation |
| `POST` | `/api/v1/media/enhance/batch` | Batch process multiple assets |
