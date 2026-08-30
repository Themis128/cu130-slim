---
name: cloudless-carousel-pipeline
description: >-
  Runs the Cloudflare-only LinkedIn carousel pipeline for cloudless.gr (copy,
  plain-English NLP check/fix, free-tier FLUX schnell → SD img2img, brand compose,
  post as Company Page). Use when generating or publishing LinkedIn carousels,
  fixing CF image models, NLP plain-English, or calling
  /api/v1/ai/run-carousel-and-publish / generate-carousel-pipeline.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# Cloudless CF Carousel Pipeline

## When to use

- Publish or dry-run a LinkedIn carousel for **cloudless.gr**
- Change txt2img / img2img / text models
- Debug NLP plain-English check/fix
- Wire social-api endpoints used by n8n

## Stack facts

| Item | Value |
|------|--------|
| Org LinkedIn account UUID | `4a8d9440-47d2-4bda-bd11-3776fd9022ba` |
| Author | Company Page (`meta_data.account_type == organization`) → `urn:li:organization:{id}` |
| Text | `@cf/meta/llama-3.2-3b-instruct` (free-tier low cost) |
| txt2img | `@cf/black-forest-labs/flux-1-schnell` (payload: `prompt` + `steps` only; 4 steps) |
| img2img | `@cf/runwayml/stable-diffusion-v1-5-img2img` (8 steps; draft-only if enhance fails) |
| API (full) | `POST /api/v1/ai/run-carousel-and-publish` |
| API (assets only) | `POST /api/v1/ai/generate-carousel-pipeline` |
| Code | `app/services/carousel_pipeline.py`, `app/services/cf_models.py`, `app/services/plain_english.py` |
| Media `source` column | `String(20)` — keep short (e.g. `n8n-cf-pipe`) |

## Preferred path

1. Prefer **n8n** schedule/webhook (see skill `n8n-cloudless`) so ops stay automated.
2. For one-off API runs, use the tool script below (login → pipeline).
3. After worker/publishing code changes: **restart `social-worker`** (no auto-reload).
4. `social-api` has uvicorn `--reload` for `app/` mounts.

## Tool scripts

Run from repo root `cu130-slim/`:

```bash
# Dry-run: generate + draft post, do not publish to LinkedIn
.devin/skills/cloudless-carousel-pipeline/scripts/run-pipeline.sh --publish false --slides 3

# Publish as cloudless.gr Company Page
.devin/skills/cloudless-carousel-pipeline/scripts/run-pipeline.sh --publish true --slides 7
```

Env (from `.env`, never print secrets):
- `SOCIAL_ADMIN_EMAIL` / `SOCIAL_ADMIN_PASSWORD`
- `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID`
- optional `CLOUDLESS_LINKEDIN_ORG_ACCOUNT_ID`

## Hard rules

- **Cloudflare Workers AI only** for this pipeline — do not route carousel gen through Ollama/ComfyUI.
- Defaults use **free-tier eligible, low-neuron** models (`cf_models.py`). All models share the same 10k neurons/day.
- Post as **organization** account, not personal profile.
- FLUX schnell: do not send `width` / `height` / `guidance` / `num_steps`.
- LinkedIn multi-image → PDF document via Documents API; document URN must be URL-encoded on status GET.
- Never commit `.env` or API keys.

## Key files

- `social-automation/backend/app/services/carousel_pipeline.py`
- `social-automation/backend/app/services/cf_models.py`
- `social-automation/backend/app/services/plain_english.py`
- `social-automation/backend/app/api/ai.py` (`run-carousel-and-publish`)
- `social-automation/backend/app/services/publishing.py`
- `scripts/publish_enhanced_cf_carousel.py` (legacy one-shot)

## More detail

See [reference.md](reference.md).
