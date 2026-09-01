# Agent working notes for cu130-slim / SocialAuto

## Commit & push cadence

- After every **~15 file changes** or at the end of a **major implementation chunk**, run the test gate below, commit, and push.
- If local history has been cleaned/rewritten, use `git push --force-with-lease` and mention it in the summary.
- Do not leave the working tree dirty at the end of a session unless the user explicitly asked for uncommitted changes.
- A pre-commit or post-edit hook may auto-commit staged changes. Always check `git log --oneline -3` before committing to avoid duplicate or empty commits.

## Test gate before commit/push

Run these for any feature that touches backend, frontend, compose, or n8n:

1. `pytest tests/unit -q` inside `social-api` — must pass (currently 154 tests).
2. `pytest tests/integration -q` against a dedicated `social_automation_test` DB — must pass when media, AI, auth, or storage behavior changes.
3. `ruff check` on changed backend files — must be clean. Install ruff inside the container with `docker compose exec -T social-api pip install ruff -q` if missing.
4. `docker compose config --quiet` — must be valid.
5. `curl http://localhost:8083/health` after `social-api` restart — must return ok.
6. `docker compose exec -T social-worker celery -A app.worker.celery_app inspect ping` after worker restart — must reply.
7. Validate all Mermaid diagrams in `docs/superpowers/plans/plan-e6a92ed66b9dca4a.md` (e.g. via mermaid.ink) when they change.
8. Frontend: `tsc --noEmit --incremental false` in `social-automation/frontend` using local `./node_modules/.bin/tsc` — no TS errors. The frontend container only ships the built output, so run tsc on the host.
9. If n8n workflows changed: import, publish, and trigger a dry run.
10. `curl http://localhost:8083/api/v1/cf-db/health` — D1, KV, and Vectorize should report `true` when Cloudflare is reachable.

## Container restart rules

- `social-api` — restart after any `app/api/*.py`, `app/services/*.py`, `app/models/*.py`, or Alembic change. Copy changed files into the container with `docker compose cp` before restarting, or rebuild the image.
- `social-worker` — restart after `app/services/publishing.py`, `app/services/linkedin_api.py`, Celery tasks (`app/worker/tasks/*.py`), or compose env changes. Copy changed files into both `social-api` and `social-worker` containers.
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

# restart key containers
docker compose restart social-api social-worker

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
```

## Product defaults to preserve

- LinkedIn carousels for **cloudless.gr** post as the **Company Page** account `4a8d9440-47d2-4bda-bd11-3776fd9022ba`, not a personal profile.
- Carousel generation uses **Cloudflare Workers AI only**.
- **Cloudflare-first, free-first** for all inference, storage, and databases; prefer Cloudflare Workers AI, R2, D1, KV, and Vectorize. Use local services (Postgres, Redis, Chroma, MinIO, Ollama) as failover.
- **Database fallback chain**: D1 (Cloudflare, primary) → PostgreSQL (local, failover). The dual-write router (`app/services/db_router.py`) writes to D1 first, then Postgres. Circuit breaker opens after 3 D1 failures, routing to Postgres for 60s. Queued writes replay to D1 on recovery.
- **Cache fallback chain**: KV (Cloudflare, primary) → Redis (local, failover).
- **Vector fallback chain**: Vectorize (Cloudflare, primary) → ChromaDB (local, failover).
- **Storage fallback chain**: R2 (Cloudflare, cloud) → MinIO (local S3, ports 9000/9001) → local disk (`/app/uploads`). The `/api/v1/media/view` endpoint transparently serves assets from any backend.
- **Inference fallback chain**: Cloudflare Workers AI → Groq/Together/HF free tiers → Ollama (last resort).
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
11. `h4c5d6e7f8a9` — brand tables (current head)

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
