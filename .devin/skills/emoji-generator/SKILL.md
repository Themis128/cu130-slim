# Emoji & Icon Generator

Generate custom emojis, icons, stickers, and badges for social media content using
DMR (Docker Model Runner) for prompt enhancement + local Diffusers (SD 1.5) for image
generation + DMR vision for quality scoring.

## When to use

- User wants to create custom emojis or icons for social media posts
- User wants to generate a sticker pack
- User wants branded icons for content
- User wants reaction faces or badges
- User asks for "emoji", "icon", "sticker", "badge" generation

## API Endpoints

All endpoints require authentication (`Authorization: Bearer <token>`).

### Generate single emoji

```bash
POST /api/v1/ai/emoji/generate
```

```json
{
  "concept": "happy cloud with a smile",
  "style": "kawaii",
  "size": 512,
  "background": "transparent",
  "steps": 20,
  "cfg_scale": 8.0,
  "provider": "local-diffusers",
  "enhance_prompt": true,
  "remove_bg": true
}
```

**Parameters:**
- `concept` (required): Short description of the emoji (e.g. "rocket launching with fire")
- `style`: `flat` | `3d` | `kawaii` | `pixel` | `outline` | `gradient` (default: `flat`)
- `size`: `256` | `512` | `768` (default: `512`)
- `background`: `transparent` | `white` | `colored` (default: `transparent`)
- `bg_color`: Hex color when background="colored" (e.g. "#FF6B6B")
- `steps`: 10-40 (default: `25`, lower = faster, higher = more detail)
- `cfg_scale`: 1-20 (default: `8.0`, higher = more prompt adherence)
- `provider`: `local-diffusers` | `cloudflare` (default: `local-diffusers`)
- `enhance_prompt`: Use DMR to enhance the concept (default: `true`)
- `remove_bg`: Remove white background post-generation (default: `true`)

**Response:**
```json
{
  "image_base64": "<base64 PNG>",
  "concept": "happy cloud with a smile",
  "enhanced_prompt": "happy cloud with a smile, kawaii style, ...",
  "style": "kawaii",
  "size": 512,
  "provider": "local-diffusers",
  "quality_check": {
    "score": 8,
    "clear": true,
    "suitable": true,
    "issues": "Slight pixelation..."
  }
}
```

### Generate batch (sticker pack)

```bash
POST /api/v1/ai/emoji/batch
```

```json
{
  "concepts": ["happy cloud", "fire rocket", "thumbs up", "heart eyes"],
  "style": "flat",
  "size": 512,
  "background": "transparent"
}
```

Max 10 concepts per batch. Returns `{"emojis": [{concept, image_base64, success, ...}]}`.

### List styles

```bash
GET /api/v1/ai/emoji/styles
```

Returns available styles, sizes, backgrounds, and providers.

## Style presets

| Style | Description | Best for |
|-------|-------------|----------|
| `flat` | Bold outlines, solid colors, minimalist | Modern UI, tech brands |
| `3d` | Soft lighting, rounded shapes, glossy | App icons, playful content |
| `kawaii` | Cute, big eyes, pastel, Japanese | Stickers, reactions |
| `pixel` | 8-bit retro, limited palette | Gaming, retro content |
| `outline` | Line art, thick black outlines | Minimalist, editorial |
| `gradient` | Modern gradient, glossy, app icon | Branded icons, badges |

## Pipeline

1. **DMR text enhancement** (optional): The short concept is expanded into a detailed
   SD prompt with style modifiers, color descriptions, and composition guidance.
2. **Image generation**: Local Diffusers (SD 1.5, GPU, free) or Cloudflare FLUX schnell
   generates the image. Local is primary; Cloudflare is fallback.
3. **Background removal** (optional): PIL removes near-white pixels and replaces them
   with transparency, producing a PNG with alpha channel.
4. **DMR vision QA** (optional): The qwen3-vl vision model scores the image 1-10 for
   clarity, recognizability, and social media suitability.

## Performance

- 256px: ~60-70s (15 steps, local-diffusers)
- 512px: ~90-100s (20 steps, local-diffusers)
- 768px: ~120-150s (25 steps, local-diffusers)
- Cloudflare FLUX: ~5-10s (4 steps, cloud)

## Scripts

### `scripts/generate-emoji.sh`
Generate a single emoji from the command line.

```bash
./scripts/generate-emoji.sh "happy cloud" kawaii 512 transparent
```

### `scripts/generate-batch.sh`
Generate a sticker pack from a file of concepts (one per line).

```bash
./scripts/generate-batch.sh concepts.txt flat 512
```

## Integration with SocialAuto

Generated emojis can be:
1. Uploaded to the media library via `POST /api/v1/media/upload`
2. Attached to posts as images
3. Used as profile badges or story stickers
4. Embedded in carousel slides

## Cost

- **Local Diffusers (SD 1.5)**: Free, uses local GPU (~2GB VRAM)
- **Cloudflare FLUX schnell**: Free tier (Workers AI, 10,000 neurons/day)
- **DMR text enhancement**: Free, local Docker Model Runner
- **DMR vision QA**: Free, local Docker Model Runner

No paid APIs required.
