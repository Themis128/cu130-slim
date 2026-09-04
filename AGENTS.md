# Agent working notes for cu130-slim / SocialAuto

## Commit & push cadence

- After every **~15 file changes** or at the end of a **major implementation chunk**, run the test gate below, commit, and push.
- If local history has been cleaned/rewritten, use `git push --force-with-lease` and mention it in the summary.
- Do not leave the working tree dirty at the end of a session unless the user explicitly asked for uncommitted changes.
- A pre-commit or post-edit hook may auto-commit staged changes. Always check `git log --oneline -3` before committing to avoid duplicate or empty commits.

## Test gate before commit/push

Run these for any feature that touches backend, frontend, compose, or n8n:

1. `pytest tests/unit -q` inside `social-api` — must pass (currently 336 tests).
2. `pytest tests/integration -q` against a dedicated `social_automation_test` DB — must pass when media, AI, auth, or storage behavior changes.
3. `ruff check` on changed backend files — must be clean. Install ruff inside the container with `docker compose exec -T social-api pip install ruff -q` if missing.
4. `docker compose config --quiet` — must be valid.
5. `curl http://localhost:8083/health` after `social-api` restart — must return ok.
6. `docker compose exec -T social-worker-publishing celery -A app.worker.celery_app inspect ping` after worker restart — should show 3 nodes (publishing, media, default).
7. Validate all Mermaid diagrams in `docs/superpowers/plans/plan-e6a92ed66b9dca4a.md` (e.g. via mermaid.ink) when they change.
8. Frontend: `tsc --noEmit --incremental false` in `social-automation/frontend` using local `./node_modules/.bin/tsc` — no TS errors. The frontend container only ships the built output, so run tsc on the host.
9. If n8n workflows changed: import, publish, and trigger a dry run.
10. `curl http://localhost:8083/api/v1/cf-db/health` — D1, KV, and Vectorize should report `true` when Cloudflare is reachable.

## Proxy policy (Cloudflare-first)

- **When a proxy is needed (Instagram, scraping, geo-bypass, IP rotation), always use Cloudflare WARP first.**
- The `warp-proxy` container (image `caomingjun/warp`) runs a free Cloudflare WARP SOCKS5 proxy on port 1080 inside the compose network.
- Internal proxy URL: `socks5://warp-proxy:1080`
- Host proxy URL: `socks5://127.0.0.1:1080`
- WARP gives a Cloudflare IP (not datacenter-flagged), located in Greece by default.
- Only consider paid residential proxies (SOAX, BrightData, IPRoyal, WebShare) if WARP IPs are also blocked by the target service.
- The Instagram sidecar (`INSTAGRAM_PROXY` env var) defaults to `socks5://warp-proxy:1080`.
- The `socksio` package must be installed in any container that uses SOCKS5 with httpx (`pip install httpx[socks]`).

## Container restart rules

- `social-api` — restart after any `app/api/*.py`, `app/services/*.py`, `app/models/*.py`, or Alembic change. Copy changed files into the container with `docker compose cp` before restarting, or rebuild the image.
- `social-worker-publishing`, `social-worker-media`, `social-worker-default` — restart after `app/services/publishing.py`, `app/services/linkedin_api.py`, Celery tasks (`app/worker/tasks/*.py`), `app/worker/celery_app.py` (queue routing), or compose env changes. Copy changed files into all worker containers and `social-api`. The three queue-dedicated workers share the same image and env (via `x-worker-env` YAML anchor).
- `celery-beat` — restart after `app/worker/celery_app.py` beat_schedule or queue routing changes. Single instance only (never scale beat).
- `ollama` — restart after Ollama env var changes or Modelfile updates. Recreate the custom model with `ollama create llama3.1:8b-gpu -f /tmp/Modelfile.llama31-gpu` after Modelfile changes.
- `comfyui` — restart after CLI args or env changes.
- `n8n` — restart and re-import workflows after any `n8n-workflows/` or webhook change.
- `social-frontend` — rebuild image or restart dev container after frontend source change.

## Common commands

```bash
# backend unit tests
docker compose exec -T social-api python -m pytest tests/unit -q

# lint
docker compose exec -T social-api python -m ruff check <paths>

# validate compose
docker compose config --quiet

# restart key containers (3 queue-dedicated workers + beat)
docker compose restart social-api social-worker-publishing social-worker-media social-worker-default celery-beat

# health
curl http://localhost:8083/health

# Cloudflare database health
curl http://localhost:8083/api/v1/cf-db/health

# trigger bidirectional D1 ↔ Postgres sync
curl -X POST http://localhost:8083/api/v1/cf-db/sync

# replay queued writes to D1 after failover
curl -X POST http://localhost:8083/api/v1/cf-db/replay

# D1 table row counts
curl http://localhost:8083/api/v1/cf-db/tables

# frontend typecheck (run on host, not in container)
cd social-automation/frontend && ./node_modules/.bin/tsc --noEmit --incremental false

# copy a changed backend file into the running container
docker compose cp social-automation/backend/app/services/foo.py social-api:/app/app/services/foo.py

# check all 3 Celery worker nodes are online
docker compose exec -T social-worker-publishing celery -A app.worker.celery_app inspect ping

# check which queues each worker is consuming
docker compose exec -T social-worker-publishing celery -A app.worker.celery_app inspect active_queues

# check Ollama GPU offload status (should show 100% GPU, 2048 ctx, Forever)
docker compose exec -T ollama ollama ps

# check GPU VRAM usage
nvidia-smi --query-gpu=memory.used,memory.free,memory.total --format=csv

# recreate the GPU-optimized Ollama model after Modelfile changes
docker compose cp ollama/Modelfile.llama31-gpu ollama:/tmp/Modelfile.llama31-gpu
docker compose exec -T ollama ollama create llama3.1:8b-gpu -f /tmp/Modelfile.llama31-gpu
```

## Product defaults to preserve

- LinkedIn carousels for **cloudless.gr** post as the **Company Page** account `4a8d9440-47d2-4bda-bd11-3776fd9022ba`, not a personal profile.
- Threads account for **cloudless.gr** uses the Threads/Instagram username **`cloudless.gr`** (with a dot, not underscore). This is the brand account, not the personal `t_baltzakis` account.
- Carousel generation uses **Cloudflare Workers AI only**.
- **Cloudflare-first, free-first** for all inference, storage, and databases; prefer Cloudflare Workers AI, R2, D1, KV, and Vectorize. Use local services (Postgres, Redis, Chroma, MinIO, Ollama) as failover.
- **Database fallback chain**: D1 (Cloudflare, primary) → PostgreSQL (local, failover). The dual-write router (`app/services/db_router.py`) writes to D1 first, then Postgres. Circuit breaker opens after 3 D1 failures, routing to Postgres for 60s. Queued writes replay to D1 on recovery.
- **Cache fallback chain**: KV (Cloudflare, primary) → Redis (local, failover).
- **Vector fallback chain**: Vectorize (Cloudflare, primary) → ChromaDB (local, failover).
- **Storage fallback chain**: R2 (Cloudflare, cloud) → MinIO (local S3, ports 9000/9001) → local disk (`/app/uploads`). The `/api/v1/media/view` endpoint transparently serves assets from any backend.
- **Inference fallback chain (text)**: DMR (local Docker Model Runner) → Cloudflare Workers AI (the ONLY cloud fallback). Other cloud providers (Groq, Gemini, Mistral, Cohere, OpenRouter, NVIDIA, HuggingFace, OpenAI, SambaNova) are kept in PROVIDER_CATALOG for manual selection but are NOT in the automatic fallback chain.
- **Inference fallback chain (images)**: Local Diffusers (SD 1.5, GPU) → Cloudflare Workers AI (the ONLY cloud fallback). Other image providers (Pixazo, Together, HuggingFace, NVIDIA FLUX) are manual-selection-only.
- **Docker Model Runner (DMR)**: Primary local text inference. API at `http://localhost:12434` (host) or `http://host.docker.internal:12434` (containers). OpenAI-compatible (`/engines/v1/chat/completions`), Anthropic-compatible (`/anthropic/v1/messages`), and Ollama-compatible (`/api/chat`) APIs. Models: `ai/qwen3:8b-q4_K_M` (text), `ai/qwen3-vl` (vision), `ai/qwen3-embedding` (embeddings), `ai/smollm2` (tiny), `ai/stable-diffusion` (SDXL image, pulled but Diffusers engine not available on WSL2). Config via `docker model configure --context-size N`. Skill: `.devin/skills/docker-model-runner/`. MCP server: `dmr` in `.devin/mcp_config.json` (8 tools: status, list, chat, embed, pull, inspect, configure, generate_image).
  - **DMR Diffusers limitation**: The Diffusers engine (for SDXL image generation) requires native Linux x86_64 with NVIDIA CUDA. It is **not available on Docker Desktop/WSL2** — `docker model status` shows `diffusers: Not Installed`. The `ai/stable-diffusion` model (6.94 GB DDUF) is pulled and cached but cannot run. For local GPU image generation on WSL2, use the `local-diffusers` container (SD 1.5) instead.
- **Ollama default model**: `llama3.1:8b-gpu` (custom Modelfile with `num_gpu=99`, `num_ctx=2048`). All layers on GPU, q8_0 KV cache, 2048-token context. See GPU & VRAM section below.
- n8n and Metabase keep PostgreSQL as primary (they require native Postgres connections). Their D1 databases are backup targets only.
- Every media text field (`alt_text`, `tags`, `ai_caption`, `generation_prompt`, `filename`) is spell/grammar-corrected via LanguageTool before storage.
- Never commit secrets (`.env`, `N8N_API_KEY`, Cloudflare tokens, admin password, `GITHUB_TOKEN`).
- Do not change the public Docker Compose port mappings (e.g. `social-api:8083`, `social-frontend:8082`, `n8n:5678`, `chroma:8001`, `languagetool:8010`, `ollama:11435`, `comfyui:8000`, `metabase:3000`). New internal services may use unmapped ports only after confirming no conflicts.

## Platform coverage

SocialAuto supports six social platforms across OAuth, publishing, analytics, token refresh, account validation, and SEO scoring:

- LinkedIn
- Twitter / X
- Facebook
- Instagram
- Threads
- TikTok

### Auto token refresh

A Celery beat task `app.worker.tasks.token_refresh.refresh_expiring_tokens` runs every hour at :15 past the hour. It refreshes any active account token expiring within the next 4 hours:

- **TikTok**: 24-hour tokens — refreshed daily (TikTok doesn't return `expires_in` on refresh, so 24h is assumed).
- **Twitter/X**: 2-hour tokens — refreshed every hour (requires `offline.access` scope for refresh token). Uses `https://x.com/i/oauth2/authorize` and `https://api.x.com/2/oauth2/token` (not `twitter.com`/`api.twitter.com`).
- **Meta (Facebook/Instagram/Threads)**: ~60-day long-lived tokens — refreshed when within 4 hours of expiry.
- **LinkedIn**: tokens don't expire (no `expires_in` returned).

If a refresh fails, the account is marked as `expired` and requires manual reconnect from the Accounts page. The task is registered in `celery_app.py` beat_schedule as `refresh-expiring-tokens`.

### Facebook account model

Facebook stores two types of accounts:

1. **User account** (type=`user`) — the Facebook user who authorized, with a long-lived user token. This is the main account used by `Sync Business` to call `/me/accounts`.
2. **Page accounts** (type=`page`, `is_business=True`) — one per managed Page, with permanent Page tokens. Created automatically during OAuth and when Sync Business is clicked.

If `Sync Business` fails with `(#100) Tried accessing nonexisting field (accounts)`, the stored token is a Page token instead of a User token — disconnect and reconnect Facebook.

### TikTok OAuth specifics

TikTok Login Kit has several non-standard OAuth requirements that differ from other platforms:

- **`client_key` parameter**: TikTok requires `client_key` (not `client_id`) in both the authorize URL and the token exchange. The custom `TikTokOAuth2` class in `app/api/auth.py` overrides `get_access_token` and `refresh_token` to send `client_key`. The authorize URL also includes `client_key` via `extras_params`.
- **PKCE required**: TikTok mandates `code_challenge` + `code_challenge_method=S256` in the authorize URL. SocialAuto generates a PKCE pair for every TikTok request and encodes the `code_verifier` in the base64-JSON state parameter.
- **Comma-separated scopes**: TikTok requires scopes as a comma-separated string (e.g. `user.info.basic,video.publish,video.upload`), not space-separated like other OAuth providers. SocialAuto passes scopes via `extras_params["scope"]` with `",".join(scopes)` and sets the library `scope` to `None`.
- **HTTPS-only redirect URIs**: `TIKTOK_REDIRECT_URI` must use `https://`. The production callback `https://social.cloudless.gr/api/v1/auth/oauth/tiktok/callback` is routed through the Cloudflare named tunnel to the local `social-api` container.
- **Env vars**: `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REDIRECT_URI`.
- **Connected account**: `cloudless.gr` TikTok account (sandbox: `cloudless-dev`, target user `user3113682023385`).

### TikTok Content Posting API

- **Publish modes**: `MEDIA_UPLOAD` (sends to TikTok inbox for manual posting) and `DIRECT_POST` (posts directly — requires app audit). Use `MEDIA_UPLOAD` until the app passes TikTok's audit review.
- **Media transfer**: `FILE_UPLOAD` (upload video bytes directly — no domain verification needed) and `PULL_FROM_URL` (TikTok downloads from URL — requires domain verification in dev console). SocialAuto's `_publish_tiktok` in `app/services/publishing.py` automatically uses `FILE_UPLOAD` when a local video file is available.
- **Photo posts**: Only support `PULL_FROM_URL` — domain verification is mandatory for photo carousels.
- **Spam protection**: TikTok limits API uploads to **5 pending shares per 24-hour period**. Error: `spam_risk_too_many_pending_share`. Clear pending uploads from the TikTok mobile app or via the cancel API (`/v2/post/publish/cancel/`).
- **Publish ID format**: FILE_UPLOAD IDs use `v_inbox_file~v2.<numeric_id>` (includes `~` and `.`). The `_ID_RE` regex in `app/services/tiktok_api.py` accepts these.
- **Upload URL hosts**: TikTok returns regional hosts (e.g. `open-upload-i18n.tiktokapis.com`). The `upload_video_file` method accepts any `*.tiktokapis.com` host.
- **App details**: App name "Cloudless", App ID `7630494700880906241`, currently under Individual ownership (needs transfer to organization `cloudless.gr` / `7630331010873377809`).
- **Domain verification**: Verify `cloudless.gr` in the TikTok dev console (URL properties) by adding a DNS TXT record in Cloudflare. Covers all subdomains including `social.cloudless.gr`.
- **Skills**: `.devin/skills/tiktok-publish/` (publishing, spam management, slideshow builder) and `.devin/skills/tiktok-dev-console/` (domain verification, app/org transfer, audit).

## Brand identity system

- One Brand per team, stored in the `brands` table (migration `h4c5d6e7f8a9`).
- Brand DNA: name, industry, positioning, mission, values, target audience, competitors, tagline, website.
- Brand Voice: tone dimensions (1-5 sliders), messaging pillars, banned phrases, preferred phrases, example content, voice signature.
- Brand Visual: primary/accent/neutral colors, heading/body fonts, type scale, logo URL, logo variants, image style, photography direction.
- Brand Guidelines: compiled JSON document with shareable token and version number.
- Brand Assets: logos, templates, OG images, favicons linked to the media library.
- API router: `/api/v1/brand` with sub-routes for `/voice`, `/visual`, `/guidelines`, `/assets`.
- Frontend: `/brand` dashboard, `/brand/identity`, `/brand/voice`, `/brand/visual`, `/brand/guidelines`.
- **Phase 2 (planned)**: Brand Kit Extractor (URL → brand profile) and Voice Analyzer (content → voice signature).
- **Phase 3 (planned)**: Brand Context Engine — inject brand voice into every AI generation, compliance scoring before publish.
- See `docs/superpowers/plans/ai-branding-expansion-plan.md` for the full 6-phase roadmap.

## Media AI enhancement system

- **Image transformation** (`app/services/image_transform.py`): 17 platform presets (Instagram, LinkedIn, Twitter, Facebook, TikTok, Threads, OG), resize with cover/contain fit, crop, format conversion (JPEG/PNG/WebP/AVIF), compress to target size, text watermark. All local Pillow — no AI needed.
- **AI image enhancement** (`app/services/image_enhance.py`): background removal (CF Workers AI segmentation), upscaling 2x/4x (local LANCZOS + sharpening), smart crop with AI subject detection (CF llama-4-scout vision), quality scoring (blur/brightness/contrast — local, no AI), WCAG-compliant alt text generation (CF vision model).
- **API router**: `/api/v1/media/enhance` with 13 endpoints for single-asset and batch operations.
- **Batch processing**: Celery task `batch_enhance_task` in `app/worker/tasks/media_enhance.py` — supports resize, convert, compress, upscale, remove_bg, smart_crop, alt_text on up to 50 assets.
- **Frontend**: AI Enhancement Studio at `/media/enhance/[id]` with before/after preview, platform preset selector, quality score display, and all operation buttons. Wand icon on media library cards links to the studio.
- See `docs/superpowers/plans/media-ai-enhancement-plan.md` for the full plan.

## Image generation pipeline

- **Endpoint**: `POST /api/v1/media/generate-image` — generates an image from a text prompt and stores it in the media library.
- **Fallback chain (first success wins)**:
  1. **Local Diffusers (SD 1.5, GPU)** — primary. Uses the `local-diffusers` container's OpenAI-compatible `/v1/images/generations` endpoint. Free, local, no quota. Supports `prompt`, `negative_prompt`, `width`, `height`, `steps`, `cfg_scale`, `seed`.
  2. **Cloudflare Workers AI (FLUX schnell)** — the ONLY cloud fallback. Used when Local Diffusers is unavailable or fails. Model: `@cf/black-forest-labs/flux-1-schnell`.
- **Removed from automatic path**: Pixazo, Together AI, HuggingFace, NVIDIA FLUX — these are manual-selection-only via the AI Providers settings page.
- **Provider provenance**: Each generated image records `meta_data.inference_provider` (`local-diffusers` or `cloudflare`) and `meta_data.inference_model` for tracking which path produced the asset.
- **DMR Diffusers (SDXL)**: The `ai/stable-diffusion` model (SDXL, 6.94 GB DDUF) is pulled into Docker Model Runner, but the Diffusers engine is **not available on Docker Desktop/WSL2** — it requires native Linux with NVIDIA CUDA. On WSL2, Local Diffusers (SD 1.5) is the working local GPU path. See `.devin/skills/docker-model-runner/SKILL.md` for DMR details.

## Image quality gate

- **Automatic scoring**: Every image uploaded via `POST /api/v1/media/upload` or generated via `POST /api/v1/media/generate-image` is automatically quality-scored using `score_image_quality()` in `app/services/image_enhance.py`. The score is stored in `meta_data.quality_score`.
- **Score breakdown**: `overall` (0-100, weighted), `sharpness` (Laplacian variance, 0-100), `brightness` (mean histogram, 0-100), `contrast` (stddev, 0-100), `blur_detected` (bool), `too_dark` (bool), `too_bright` (bool), `issues` (list of actionable strings).
- **Thresholds** (configurable in `app/core/config.py`):
  - `MIN_IMAGE_QUALITY_SCORE = 60` — images below this overall score get `meta_data.quality_failed = True`.
  - `MIN_IMAGE_SHARPNESS = 20` — blurry/broken images (sharpness below this) also get flagged.
  - Set either to `0` to disable that gate.
- **Soft flag**: Flagged images are still stored — `quality_failed` is informational so the UI can show a warning badge. No uploads are blocked.
- **API access**: `GET /api/v1/media/enhance/assets/{asset_id}/quality` returns the score for any image. `MediaAssetResponse` now includes `meta_data` so quality scores and provider info are visible in list/detail responses.
- **Dark-theme note**: The scoring uses mean brightness for `too_dark` detection (threshold < 50/255). Brand designs with intentional dark navy backgrounds (#0b1220) will trigger `too_dark=True` — this is expected and does not mean the image is bad. Use `sharpness` and `contrast` as the primary quality indicators for dark-themed content.

## Music / audio track support

- Posts can have an optional `music_asset_id` field (FK to `media_assets.id`, `ON DELETE SET NULL`).
- Audio uploads are supported in the media library: `.mp3`, `.wav`, `.m4a`, `.aac`, `.ogg`, `.flac`.
- The post editor has a **Music Track** card with a `MusicPickerDialog` for selecting one audio asset.
- During publishing (`app/services/publishing.py`), if a music asset is attached and the post includes a video, ffmpeg mixes the audio into the video before dispatching to social platforms. The original video is not mutated — a temporary mixed file is produced and cleaned up after publishing.
- The `MusicPickerDialog` component (`frontend/src/components/ui/MusicPickerDialog.tsx`) filters the media library by audio MIME type or audio file extension and returns a single `MediaAsset`.
- Migration: `i5d6e7f8a9b0` (chained after merge migration `5d35f29495b9`).

## LinkedIn carousel pipeline

- **Cloudflare Workers AI only** for carousel generation (text + image). No Ollama/ComfyUI fallback for this path.
- The pipeline (`app/services/carousel_pipeline.py`) generates slide copy, runs NLP plain-English check/fix, spellchecks each slide's title/body/highlight via LanguageTool, generates background images via FLUX schnell, composes branded slides (dark navy + teal Cloudless brand), spellchecks the final caption, runs SEO scoring on the caption+hashtags, and combines all slides into a **single PDF** — one media library entry.
- An AI-generated title is produced for each carousel and stored in `MediaAsset.ai_caption`. The target platform and account are stored in `MediaAsset.tags` (e.g. `['carousel', 'linkedin', 'slides:7', 'cloudless.gr']`).
- The `/api/v1/ai/run-carousel-and-publish` endpoint supports `custom_slides`, `custom_caption`, and `custom_hashtags` in the request body to override AI-generated copy with curated content. When custom slides are provided, AI copy generation and NLP dedup are skipped.
- The `/api/v1/media/view` endpoint serves PDFs and audio files directly (browsers render them natively). The frontend `ImageViewerDialog` renders PDFs in an `<iframe>` and audio in an `<audio>` player.
- Media library cards show a file icon for PDFs (with AI title and platform tags) and a music icon for audio files.
- Post as the **cloudless.gr Company Page** account (`4a8d9440-47d2-4bda-bd11-3776fd9022ba`), not a personal profile.
- Automate via n8n workflow `cloudless-cf-carousel-linkedin` (schedule or webhook).

## Quality pipeline (NLP + spellcheck + SEO)

All content-generating endpoints enforce a three-step quality pipeline before returning results. The shared helper is `app/services/quality_pipeline.py` (`apply_quality_pipeline`).

### Quality steps

1. **Spellcheck** — `auto_correct` via LanguageTool (grammar + spelling corrections).
2. **NLP** — `run_nlp_check_and_fix` to flag jargon/hard sentences and rewrite to plain English.
3. **SEO** — `analyze_seo` to score content against platform best practices (length, hashtags, readability, keywords, links, plain English).
4. **Auto-improve** — if the SEO overall score is below the target (default 90), the pipeline feeds recommendations back to the LLM, regenerates the content, and re-checks. Up to 2 iterations.

### Endpoint coverage

| Endpoint | NLP | Spellcheck | SEO | Auto-improve |
|----------|-----|------------|-----|--------------|
| `POST /api/v1/ai/generate-content` | ✅ | ✅ | ✅ | ✅ (target 90) |
| `POST /api/v1/ai/improve-content` | ✅ | ✅ | ✅ | ✅ (target 90) |
| `POST /api/v1/ai/generate-carousel` | ✅ | ✅ | ✅ | — |
| `POST /api/v1/ai/generate-carousel-pipeline` | ✅ | ✅ | ✅ | — |
| `POST /api/v1/ai/run-carousel-and-publish` | ✅ | ✅ | ✅ | — |
| `POST /api/v1/ai/analyze-content` | ✅ | ✅ | ✅ | — |
| Publishing worker (`publish_to_platform`) | — | ✅ | — | — |

### Response fields

All content-generating endpoints now return additional quality metadata:
- `seo_score` — the full SEO score breakdown (overall, readability, keywords, hashtags, links, plain_english, length, recommendations).
- `nlp_report` — the NLP plain-English check report (issues found, fields rewritten).
- `quality` — full quality pipeline result (only present if auto-improvement ran).

### Decorator

`@with_quality(target_score=90)` can be applied to any endpoint that returns a Pydantic model with `content` and `hashtags` fields. It automatically extracts, quality-checks, and patches the response.

### Publishing-time spellcheck

In addition to generation-time quality checks, `publish_to_platform` in `app/services/publishing.py` spellchecks the final assembled post text (including platform-specific overrides, hashtags, and link URLs) via `auto_correct` before dispatching to social platforms. This is advisory — spellcheck failures never block publishing.

### Ollama fallback for text quality steps

All text-based quality pipeline steps (NLP check/fix, SEO auto-improve, carousel copy generation, AI title generation) use `allow_fallback=True`, so if Cloudflare Workers AI is unavailable or quota-exhausted, the inference chain falls back through Groq → Together → HF → **Ollama** (last resort). This ensures the quality pipeline never silently skips NLP/SEO improvement when CF is down.

**What falls back to Ollama:**
- `apply_quality_pipeline` NLP check/fix and auto-improve iterations.
- `run_cloudless_carousel_pipeline` slide copy generation, NLP check/fix, and AI title generation.
- `generate-carousel-pipeline` endpoint NLP check/fix.
- `generate-content` and `improve-content` via `apply_quality_pipeline`.

**What does NOT fall back (stays Cloudflare-only):**
- Carousel **image** generation (`_call_cf_image_pipeline`, `_cf_generate_background`) — images must use Cloudflare Workers AI (FLUX schnell / SD img2img) per the product default. Image generation has no Ollama fallback.
- `generate-carousel-pipeline` image pipeline calls (`allow_fallback=False` for image requests).

**Ollama model**: `llama3.1:8b-gpu` (custom Modelfile, 100% GPU, 2048 ctx, q8_0 KV cache, `KEEP_ALIVE=-1` so it stays resident in VRAM permanently). Verify with `docker compose exec -T ollama ollama ps` — should show `llama3.1:8b-gpu, 4.9 GB, 100% GPU, 2048, Forever`.

**Container tuning notes:**
- Ollama and ComfyUI share the 8GB RTX 3070 Laptop GPU. Ollama uses ~5GB VRAM (model weights + KV cache); ComfyUI gets ~2GB free (enough for SD 1.5 fp16).
- `OLLAMA_GPU_OVERHEAD=2147483648` (2GB) reserves VRAM for ComfyUI so Ollama doesn't monopolize the card.
- After Ollama container restart, the model must be reloaded: `docker compose exec -T ollama ollama run llama3.1:8b-gpu "Say OK"` (forces VRAM load), or send any inference request.
- LanguageTool (`languagetool` container, port 8010) handles spellcheck — `Java_Xmx=256m` heap limit. No GPU needed.

### Container data-flow matrix

Verified connectivity from `social-api` to all dependent services:

| Service | Container | Port | Path | Status |
|---------|-----------|------|------|--------|
| LanguageTool (spellcheck) | `languagetool` | 8010 | `/v2/check` | OK (200) |
| Chroma (dedup/vector) | `chroma` | 8000 | `/api/v2/` | OK (v2 API; v1 deprecated) |
| Redis (celery/cache) | `redis` | 6379 | TCP | OK |
| Postgres (primary DB) | `social-postgres` | 5432 | TCP | OK |
| MinIO (storage) | `minio` | 9000 | `/minio/health/live` | OK (200) |
| n8n (workflows) | `n8n` | 5678 | `/healthz` | OK (200) |
| Ollama (fallback inference) | `ollama` | 11434 | `/api/tags` | OK (200, model in VRAM) |
| Instagram Private API | `instagram-private-api` | 8000 | `/` | OK (307 redirect to /docs) |
| LinkedIn Browser Sidecar | `linkedin-browser-sidecar` | 9225 | `/health` | OK (200) |
| Facebook Browser Sidecar | `facebook-browser-sidecar` | 9226 | `/health` | OK (200) |
| TikTok Browser Sidecar | `tiktok-browser-sidecar` | 9224 | `/health` | OK (200) |
| Browser Bridge (noVNC) | `browser-novnc` | 9223 | `/` | OK (404 on root, server up) |
| WARP Proxy | `warp-proxy` | 1080 | TCP (SOCKS5) | OK |
| Cloudflared tunnel | `social-cloudflared` | — | Outbound tunnel (no HTTP health) | OK (container healthy) |

### Known infrastructure issues

- **Cloudflare API token expired**: `CLOUDFLARE_API_TOKEN` returns 401 "Invalid API Token" from `api.cloudflare.com`. D1, KV, and Vectorize are all in `postgres_only` fallback mode. The env vars (`D1_SOCIAL_AUTOMATION_ID`, `KV_CACHE_NAMESPACE`, `VECTORIZE_INDEX_NAME`) are correctly configured — only the token needs refreshing in `.env`.
  - **Token fallback**: D1/KV/Vectorize clients now try multiple tokens in order: `CLOUDFLARE_API_TOKEN` → `CLOUDFLARE_AI_API_TOKEN` → `CLOUDFLARE_EMAIL_API_TOKEN`. The AI token works for Workers AI but lacks D1/KV/Vectorize scopes, so a new API token is still needed.
  - **Required token scopes**: Create a new Cloudflare API token at https://dash.cloudflare.com/profile/api-tokens with these permissions:
    - D1: Edit (`Account > D1 > Edit`)
    - Workers KV: Edit (`Account > Workers KV Storage > Edit`)
    - Vectorize: Edit (`Account > Vectorize AI > Edit`)
    - Workers AI: Edit (`Account > Workers AI > Edit`) — optional if using separate AI token
  - **After refreshing**: Update `CLOUDFLARE_API_TOKEN` in `.env`, restart `social-api` + workers, then verify with `curl http://localhost:8083/api/v1/cf-db/health` — `d1`, `kv`, `vectorize` should all return `true`, and `tokens_available` should show the count.
  - **Health endpoint diagnostics**: `/api/v1/cf-db/health` now returns `tokens_available`, `account_id_configured`, `d1_db_id_configured`, `kv_namespace_configured`, and `vectorize_index_configured` to help diagnose config vs token issues.
- **linkedin-mcp-server healthcheck**: Fixed — was using `curl` (not available in the container image). Now uses Python `socket` TCP check on port 9227. Container reports healthy.
- **social-worker-default CPU**: Normal — runs `sync_all_analytics` Celery beat tasks which are CPU-intensive during analytics sync. Concurrency=2, max-tasks-per-child=200.
- **Chroma API v1 deprecated**: Chroma client uses v2 API (`/api/v2/tenants/default_tenant/databases/default_database`). The v1 endpoint returns 410 Gone — this is expected and not a problem.
- **Cloudflared tunnel EOF during restarts**: Expected — when `social-api` restarts, cloudflared logs `Unable to reach the origin service: EOF`. It auto-reconnects within seconds (4 connections to Sofia edge). No action needed.

### Instagram-specific rules

- Instagram captions do **not** include `link_url` (removed from `_LINK_IN_BODY` in `content_renderer.py`). Instagram has no clickable caption links, and the SEO scorer penalizes links in IG captions.
- `_resolve_ig_user_token` in `publishing.py` resolves the correct Facebook **user** token (not Page token) for Instagram Graph API publishing. It uses `getattr()` for `parent_account_id`/`team_id` (test-safe), uses `.limit(1).first()` for the fallback query (avoids `MultipleResultsFound`), and logs all failure paths.

### Known lower-priority stubs (not blocking)

- `_estimate_cost` in `inference.py` returns `0.0` (Phase 4.2 cost reporting — not wired up).
- NVIDIA FLUX Kontext `example_id=0` placeholder (NVIDIA-specific enhancement path, not used by Cloudflare carousel).
- Competitor snapshot in `brand_monitoring.py` stores zeroed metrics (competitor monitoring feature not wired up yet).

## Alembic migration chain

The migration chain is linear. Always set `down_revision` to the current head before creating a new migration. Run `grep -h "^revision\|^down_revision" alembic/versions/*.py` to verify there is only one head.

Current chain (oldest → newest):
1. `d90da9214372` — initial migration
2. `d897700d7a90` — scheduled posts partial index
3. `a1b2c3d4e5f6` — AI providers table
4. `e1f2a3b4c5d6` — default timezone Europe/Athens
5. `f2a3b4c5d6e7` — post analytics snapshots
6. `g3b4c5d6e7f8` — nullable snapshot post_id
7. `d9a5a234d4d7` — AI usage logs
8. `9c222774bd04` — media collections and R2 storage
9. `21e4c2d4daf5` — MinIO storage backend enum
10. `b2c3d4e5f6a7` — platform event index
11. `h4c5d6e7f8a9` — brand tables
12. `e7f8a9b1c2d3` — account type column
13. `5d35f29495b9` — merge brand + account-type heads
14. `i5d6e7f8a9b0` — music_asset_id on posts (current head)

## CodeQL alert history

- **#7043** (High) — Polynomial regex in `plain_english.py:137`: fixed by replacing `\s` with `[ \t]` in `_REWRITTEN_MARKERS` and `_ORIGINAL_MARKERS` to eliminate overlapping whitespace quantifiers.
- **#7052** (Medium) — Log injection in `linkedin_api.py:141`: fixed by applying `_sanitize_log_text()` to the `url` parameter before logging in `_log_api_error` and `_raise_for_status`.
- When fixing CodeQL alerts, always sanitize any user-controlled or external data passed to `logger` calls (URLs, response text, error messages).
- Do not claim CodeQL alerts are closed until GitHub scan results confirm closure.

## Documentation & diagrams

- If the scope, architecture, or behavior changes, update `docs/superpowers/plans/plan-e6a92ed66b9dca4a.md` (and its Devin copy at `~/.devin/plans/plan-e6a92ed66b9dca4a.md`) and any affected `AGENTS.md` / `README` notes.
- Keep the Mermaid diagrams in the roadmap in sync with the actual flows (inference fallback, media lifecycle, publishing, etc.).
- Every new feature or user-facing workflow must have a step-by-step guide in `docs/superpowers/guides/`. Add a link in the guides `README.md` and, if relevant, in the frontend empty-state or help text.

## Current guides

1. [Getting started](01-getting-started.md)
2. [Connecting social accounts](02-connecting-accounts.md)
3. [Media library](03-media-library.md)
4. [Creating a post](04-creating-a-post.md)
5. [LinkedIn carousel](05-linkedin-carousel.md)
6. [Analytics and queue](06-analytics-and-queue.md)
7. [Cloudflare database failover](07-cf-database-failover.md)
8. [Brand identity setup](08-brand-identity-setup.md)
9. [AI media enhancement](09-ai-media-enhancement.md)
10. [TikTok content posting](10-tiktok-content-posting.md)

## Current plans

- `docs/superpowers/plans/plan-e6a92ed66b9dca4a.md` — original platform roadmap.
- `docs/superpowers/plans/ai-branding-expansion-plan.md` — 6-phase AI branding roadmap (Phase 1 complete, Phases 2-6 planned).
- `docs/superpowers/plans/media-ai-enhancement-plan.md` — media AI enhancement plan (implemented).

## Celery worker architecture

Tasks are routed to dedicated queues via `task_routes` in `app/worker/celery_app.py` so time-sensitive publishing is never blocked by CPU-heavy media/AI work:

| Queue | Worker container | Concurrency | Max tasks/child | Tasks |
|-------|-----------------|-------------|-----------------|-------|
| `publishing` | `social-worker-publishing` | 3 | 200 | `process_publish_queue`, `check_scheduled_posts`, `publish_post_now`, `refresh_expiring_tokens` |
| `media` | `social-worker-media` | 2 | 50 | `batch_enhance_task`, `auto_tag_asset_task` |
| `default` + `celery` | `social-worker-default` | 2 | 200 | `sync_all_analytics`, `sync_team_analytics_task`, `execute_workflow`, `deploy_workflow`, `send_daily_slack_digest`, unrouted tasks |

- `celery-beat` is a single scheduler instance that dispatches periodic tasks into the routed queues. Never scale beat to multiple instances.
- Total: 7 concurrent prefork processes across 3 containers (was 4 in a single container before).
- Publishing gets 3 slots (I/O-bound, ~120MB/process) so "publish now" is never blocked by a long `process_publish_queue` run.
- Media gets 2 slots with `max-tasks-per-child=50` to recycle Pillow/AI memory frequently on this 8GB-RAM host.
- Default gets 2 slots with `max-tasks-per-child=200` (light I/O tasks, recycle infrequently).
- `task_acks_late=True` + `task_reject_on_worker_lost=True`: tasks are acked after completion — a worker crash triggers redelivery instead of silent loss.
- `result_expires=3600`: Redis result backend auto-cleans after 1 hour.
- Per-task time limits via `task_annotations` in `celery_app.py`:
  - Publishing: 10–15 min (fail fast on hung API calls)
  - Media: 5–40 min (CPU-heavy Pillow/AI work)
  - Analytics: 10–20 min
  - Workflows: 2–7 min (n8n API calls)
  - Digest: 5–10 min
- Worker env/volumes/depends_on are shared via YAML anchors (`x-worker-env`, `x-worker-volumes`, `x-worker-depends`) in `docker-compose.yml`.
- The `social-worker-default` container consumes both `default` and `celery` queues — the `celery` queue catches any task that wasn't explicitly routed.
- Health checks use worker-specific hostnames: `publishing@%h`, `media@%h`, `default@%h`.
- Verify: `docker compose exec -T social-worker-publishing celery -A app.worker.celery_app inspect ping` — should show 3 nodes online.
- Verify queue routing: `docker compose exec -T social-worker-publishing celery -A app.worker.celery_app inspect active_queues`.
- Verify per-worker concurrency: `docker compose exec -T social-worker-publishing celery -A app.worker.celery_app inspect stats | grep max-concurrency`.

## GPU & VRAM optimization

The stack runs on an 8GB VRAM GPU (RTX 3070 Laptop) with 8GB system RAM. Ollama and ComfyUI share the GPU. The configuration maximizes VRAM usage and minimizes system RAM.

### Ollama (`ollama` container)

- **Model**: `llama3.1:8b-gpu` — custom Modelfile at `ollama/Modelfile.llama31-gpu` with `num_gpu=99` (forces all 32 layers to GPU) and `num_ctx=2048`.
- **`OLLAMA_FLASH_ATTENTION=1`** — reduces VRAM and RAM for attention layers.
- **`OLLAMA_KV_CACHE_TYPE=q8_0`** — quantizes KV cache to 8-bit, halves context memory.
- **`OLLAMA_CONTEXT_LENGTH=2048`** — caps context at 2048 tokens (social copy rarely exceeds 500).
- **`OLLAMA_GPU_OVERHEAD=2147483648`** (2GB) — reserves VRAM for ComfyUI so Ollama doesn't monopolize the 8GB card.
- **`OLLAMA_MAX_LOADED_MODELS=1`** — only one model resident at a time.
- **`OLLAMA_NUM_PARALLEL=1`** — no concurrent inference (prevents KV cache multiplication).
- **`OLLAMA_KEEP_ALIVE=-1`** — model stays in VRAM permanently (no reload latency, no RAM swap-out).
- Default model in `app/core/config.py` is `llama3.1:8b-gpu`.
- `ollama ps` should show `100% GPU, 2048 ctx, Forever`.
- After Modelfile changes: copy to container and recreate with `ollama create llama3.1:8b-gpu -f /tmp/Modelfile.llama31-gpu`.
- Known issue: Ollama can freeze when unloading models if ComfyUI holds VRAM. The `OLLAMA_GPU_OVERHEAD` + `--reserve-vram` combination mitigates this by preventing either service from starving the other.

### ComfyUI (`social-media-comfyui-gpu` container)

- **`--gpu-only`** — forces text encoders, CLIP, and models onto GPU (minimum RAM usage).
- **`--force-fp16`** — halves VRAM usage with minimal quality loss.
- **`--reserve-vram 1`** — keeps 1GB VRAM free so ComfyUI doesn't OOM Ollama.
- Note: `--gpu-only` and `--highvram` are mutually exclusive. `--gpu-only` is the stronger flag (forces everything onto GPU).

### Java heap limits (RAM savings)

- **LanguageTool**: `Java_Xms=128m`, `Java_Xmx=256m` (was 512m). Saves ~180MB RAM.
- **Metabase**: `JAVA_TOOL_OPTIONS=-Xms128m -Xmx384m` (was default ~1GB). Saves ~600MB RAM. Note: 256m causes OOM; 384m is the minimum for Metabase's Liquibase + Quartz scheduler.

### VRAM budget (8GB RTX 3070 Laptop)

| Component | VRAM | Notes |
|-----------|------|-------|
| CUDA/display driver | ~0.5GB | System overhead |
| Ollama model weights | ~4.9GB | llama3.1:8b Q4, all 32 layers on GPU |
| Ollama KV cache | ~0.1GB | q8_0 at 2048 ctx |
| ComfyUI (idle) | ~0.5GB | PyTorch + CUDA context |
| Local Diffusers (SD 1.5) | ~2.0GB | Allocated when generating; unloaded when idle |
| **Free for models** | **~2GB** | Enough for SD 1.5 at fp16; SDXL needs careful management |

- **DMR SDXL on disk**: The `ai/stable-diffusion` model (6.94 GB DDUF) is cached locally but cannot load into VRAM on WSL2 (Diffusers engine not available). It would require ~6GB VRAM if it could run.
- **Local Diffusers (SD 1.5)**: Uses ~2.0GB VRAM when active, ~3.4GB reserved. Unloads when idle so Ollama/ComfyUI can use the VRAM.
