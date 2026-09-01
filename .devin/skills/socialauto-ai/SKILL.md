---
name: socialauto-ai
description: >-
  AI content generation through SocialAuto: generate post copy, hashtags,
  image prompts, SEO scores, carousels, and workflow templates using
  Cloudflare Workers AI. Covers /api/v1/ai/* endpoints. Use when generating
  social media content, suggesting hashtags, finding best posting times,
  improving content, or generating LinkedIn carousels.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# SocialAuto AI Generation

AI-powered content generation for social media posts.

## When to use

- Generate post copy for a specific platform (LinkedIn, Twitter, etc.)
- Suggest hashtags for a topic
- Find the best time to post
- Improve existing content
- Generate an image prompt
- Generate a LinkedIn carousel (text + images + branded PDF)
- Analyze content for SEO
- Generate a workflow template

## API base

```
http://127.0.0.1:8083/api/v1/ai
```

## Authentication

Bearer token from `POST /api/v1/auth/login`.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/generate-content` | Generate post copy for a platform |
| POST | `/improve-content` | Improve existing content |
| POST | `/suggest-hashtags` | Suggest hashtags for a topic |
| POST | `/best-time-to-post` | Best posting time for a platform |
| POST | `/analyze-content` | Analyze content for SEO/quality |
| POST | `/seo` | SEO score for content |
| POST | `/generate-image-prompt` | Generate an image prompt from text |
| POST | `/generate-image` | Generate an image (CF Workers AI) |
| POST | `/generate-image-pipeline` | Generate image + save to media library |
| POST | `/generate-image-flux` | Generate via FLUX schnell |
| POST | `/generate-carousel` | Generate carousel slides (text only) |
| POST | `/generate-carousel-pipeline` | Generate carousel assets (text + images, no publish) |
| POST | `/run-carousel-and-publish` | Full carousel pipeline (generate + publish) |
| POST | `/generate-workflow` | Generate a workflow template |
| POST | `/spellcheck` | Spell/grammar check text |
| POST | `/transcribe` | Transcribe audio/video (CF Whisper) |

## Cloudflare Workers AI models

| Purpose | Model | Free tier |
|---------|-------|-----------|
| Text generation | `@cf/meta/llama-3.2-3b-instruct` | 10k neurons/day |
| Image generation | `@cf/black-forest-labs/flux-1-schnell` | 10k neurons/day |
| Image segmentation | `@cf/runwayml/stable-diffusion-v1-5-img2img` | 10k neurons/day |
| Vision (alt text) | `@cf/meta/llama-4-scout-17b-16e-instruct` | 10k neurons/day |
| Audio transcription | `@cf/openai/whisper-tiny-en` | 10k neurons/day |

## Carousel pipeline

The carousel pipeline generates branded LinkedIn carousels:

1. AI generates slide copy (title, body, highlight, image_prompt per slide)
2. NLP plain-English check/fix
3. FLUX schnell generates background images
4. PIL composes branded slides (dark navy + teal Cloudless brand)
5. All slides combined into a single PDF
6. PDF saved to media library as one entry
7. Post created as draft (or published if `publish=true`)

Supports `custom_slides` to override AI copy with curated content.

## Tool scripts

Run from repo root `cu130-slim/`:

```bash
# Generate post copy for a platform
.devin/skills/socialauto-ai/scripts/generate-content.sh "topic" --platform linkedin --tone professional

# Suggest hashtags
.devin/skills/socialauto-ai/scripts/suggest-hashtags.sh "topic" [--platform linkedin]

# Find best time to post
.devin/skills/socialauto-ai/scripts/best-time.sh --platform linkedin

# Improve existing content
.devin/skills/socialauto-ai/scripts/improve-content.sh "existing text" --platform linkedin

# Spellcheck text
.devin/skills/socialauto-ai/scripts/spellcheck.sh "text to check"

# Generate a LinkedIn carousel (dry-run)
.devin/skills/socialauto-ai/scripts/generate-carousel.sh "topic" --slides 7 [--publish false]
```

## Important notes

- All AI generation uses Cloudflare Workers AI (free tier, 10k neurons/day).
- The carousel pipeline is Cloudflare-only (no Ollama/ComfyUI fallback).
- FLUX schnell: send only `prompt` + `steps` (4 steps). Do not send
  `width`/`height`/`guidance`/`num_steps`.
- NLP plain-English check runs on all generated copy before rendering.
- Custom slides bypass AI copy generation but still go through NLP fix.
