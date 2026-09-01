# cu130-slim

Self-hosted social-automation stack for Cloudless (`cloudless.gr`).

## Stack

| Service | Port | Purpose |
|---------|------|---------|
| n8n | 5678 | Workflow automation (Cloudless carousel, daily Slack digest) |
| social-api | 8083 | FastAPI backend (`/api/v1`) |
| social-frontend | 8082 | Next.js dashboard |
| social-worker-publishing | - | Celery worker — `publishing` queue (publish, scheduled posts, token refresh) |
| social-worker-media | - | Celery worker — `media` queue (batch enhance, auto-tagging) |
| social-worker-default | - | Celery worker — `default` + `celery` queues (analytics, workflows, digests) |
| celery-beat | - | Celery beat scheduler (single instance, dispatches to routed queues) |
| redis | 6379 | Queue/cache (local failover for Cloudflare KV) |
| social-postgres | 5433 | Application database (local failover for Cloudflare D1) |
| postgres | 5432 | Metabase database |
| chroma | 8001 | Vector store (local failover for Cloudflare Vectorize) |
| languagetool | 8010 | Self-hosted spell/grammar checker (Java heap capped at 256MB) |
| ollama | 11435 | Local LLM inference — 100% GPU offload, q8_0 KV cache, 2048 ctx |
| comfyui | 8000 | ComfyUI image generation (requires NVIDIA, `--gpu-only --force-fp16 --reserve-vram 1`) |
| metabase | 3000 | BI dashboards (Java heap capped at 384MB) |

### Cloudflare databases (primary, free tier)

| Service | Name | Purpose |
|---------|------|---------|
| D1 | social-automation | Primary SQL database for social-api |
| D1 | n8n | Backup database for n8n |
| D1 | metabase-analytics | Backup database for Metabase |
| KV | social-cache | Primary cache layer |
| KV | social-queue | Queue namespace |
| Vectorize | social-embeddings | Primary vector database (1024 dims, cosine) |

## Quick start

1. Generate and fill `.env`:
   ```bash
   python3 scripts/generate-env-secrets.py > .env
   # Add real CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID, LinkedIn OAuth,
   # GITHUB_PERSONAL_ACCESS_TOKEN, N8N_API_KEY, and any other platform secrets.
   ```

2. Start the stack:
   ```bash
   docker compose up -d
   ```

3. Run a carousel dry-run (no LinkedIn publish):
   ```bash
   .cursor/skills/cloudless-carousel-pipeline/scripts/run-pipeline.sh --publish false --slides 3
   ```

4. Check status:
   ```bash
   .cursor/skills/social-stack-ops/scripts/stack-status.sh
   ```

## Skills

- `.devin/skills/cloudless-carousel-pipeline/SKILL.md`
- `.devin/skills/n8n-cloudless/SKILL.md`
- `.devin/skills/social-stack-ops/SKILL.md`

## Security

- The core service images (`social-api`, `social-worker`, `env-manager-backend`, `env-manager-frontend`, `social-frontend`) are rebuilt on current base images and currently report zero CRITICAL/HIGH findings with Trivy 0.58.1 (`--ignore-unfixed`).
- Source-level CodeQL findings for path/code injection, exception and secret logging, regex, insecure randomness, and DOM XSS have been remediated in the Python backend and TypeScript/React frontends.
- The `docker-compose.override.yml` keeps the real ComfyUI image reference for security scan consistency; use `runtime: runc` and the sleep command only as a local no-GPU stub.
- CI runs `pip-audit` for the Python services and `npm audit` for the frontends in addition to Trivy image and CodeQL scans.

## Notes

- The Cloudless carousel uses **Cloudflare Workers AI only** (`@cf/meta/llama-3.2-3b-instruct` for text, `@cf/black-forest-labs/flux-1-schnell` for images). No Ollama/ComfyUI fallback.
- `CLOUDFLARE_API_TOKEN` needs **Workers AI → Read + Run**; `Edit` alone is not enough for `/ai/run`.
- LinkedIn posts target the Cloudless Company Page account `4a8d9440-47d2-4bda-bd11-3776fd9022ba` by default.
- n8n workflow `cloudless-cf-carousel-linkedin` runs every 2 days at 19:00 Europe/Athens.
- `N8N_API_KEY` must be minted in the n8n UI; there is no automatic `.env` value.

## Media library

- Upload, generate, and manage images in team-scoped collections.
- **Storage fallback chain**: Cloudflare R2 (cloud) → MinIO (local S3, ports 9000/9001) → local disk (`/app/uploads`).
- Direct browser upload via presigned R2 PUT URL (`POST /api/v1/media/upload/prepare`, client PUT, `POST /api/v1/media/upload/complete`) when `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY` are configured.
- AI auto-tagging runs automatically with Cloudflare Workers AI vision (`@cf/moondream/moondream3.1-9B-A2B`) and Ollama `llava` as fallback.
- Chroma embeddings enable semantic search and similar-asset discovery.

## Database failover & sync

- **Primary databases**: Cloudflare D1 (SQL), KV (cache), Vectorize (vectors).
- **Local failover**: PostgreSQL, Redis, ChromaDB.
- **Dual-write router**: writes go to D1 first, then replicate to Postgres. If D1 is unavailable, writes fall back to Postgres and queue for replay when D1 recovers.
- **Circuit breaker**: after 3 consecutive D1 failures, the router opens the circuit and routes all traffic to Postgres for 60 seconds before retrying D1.
- **Bidirectional sync**: `POST /api/v1/cf-db/sync` syncs all 15 tables in both directions.
- **Replay queue**: `POST /api/v1/cf-db/replay` replays queued writes to D1 after recovery.
- **Health check**: `GET /api/v1/cf-db/health` shows D1, KV, Vectorize, and router status.
- n8n and Metabase keep PostgreSQL as primary (they require native Postgres connections); their D1 databases serve as periodic backup targets.

## Celery worker architecture

Tasks are routed to dedicated queues via `task_routes` in `app/worker/celery_app.py` so time-sensitive publishing is never blocked by CPU-heavy media/AI work:

| Queue | Worker container | Concurrency | Tasks |
|-------|-----------------|-------------|-------|
| `publishing` | `social-worker-publishing` | 2 | `process_publish_queue`, `check_scheduled_posts`, `publish_post_now`, `refresh_expiring_tokens` |
| `media` | `social-worker-media` | 2 | `batch_enhance_task`, `auto_tag_asset_task` |
| `default` + `celery` | `social-worker-default` | 2 | `sync_all_analytics`, `sync_team_analytics_task`, `execute_workflow`, `deploy_workflow`, `send_daily_slack_digest`, unrouted tasks |

- `celery-beat` is a single scheduler instance that dispatches periodic tasks into the routed queues.
- Total: 6 concurrent prefork processes across 3 containers.
- Worker env/volumes/depends_on are shared via YAML anchors (`x-worker-env`, `x-worker-volumes`, `x-worker-depends`) in `docker-compose.yml`.
- Verify: `docker compose exec -T social-worker-publishing celery -A app.worker.celery_app inspect ping` — should show 3 nodes (publishing, media, default).

## GPU & VRAM optimization

The stack runs on an 8GB VRAM GPU (RTX 3070 Laptop) with 8GB system RAM. Both Ollama and ComfyUI share the GPU:

### Ollama (`ollama` container)

- **Model**: `llama3.1:8b-gpu` (custom Modelfile at `ollama/Modelfile.llama31-gpu`) — forces all 32 layers to GPU (`num_gpu=99`), 2048-token context.
- **`OLLAMA_FLASH_ATTENTION=1`** — reduces VRAM and RAM for attention layers.
- **`OLLAMA_KV_CACHE_TYPE=q8_0`** — quantizes KV cache to 8-bit, halves context memory.
- **`OLLAMA_CONTEXT_LENGTH=2048`** — caps context at 2048 tokens (social copy rarely exceeds 500).
- **`OLLAMA_GPU_OVERHEAD=2147483648`** (2GB) — reserves VRAM for ComfyUI so Ollama doesn't monopolize the card.
- **`OLLAMA_MAX_LOADED_MODELS=1`** — only one model resident at a time.
- **`OLLAMA_NUM_PARALLEL=1`** — no concurrent inference (prevents KV cache multiplication).
- **`OLLAMA_KEEP_ALIVE=-1`** — model stays in VRAM permanently (no reload latency).
- Default model in `app/core/config.py` is `llama3.1:8b-gpu`.
- `ollama ps` should show `100% GPU, 2048 ctx, Forever`.

### ComfyUI (`social-media-comfyui-gpu` container)

- **`--gpu-only`** — forces text encoders, CLIP, and models onto GPU (minimum RAM).
- **`--force-fp16`** — halves VRAM usage with minimal quality loss.
- **`--reserve-vram 1`** — keeps 1GB VRAM free so ComfyUI doesn't OOM Ollama.

### Java heap limits (RAM savings)

- **LanguageTool**: `Java_Xms=128m`, `Java_Xmx=256m` (was 512m).
- **Metabase**: `JAVA_TOOL_OPTIONS=-Xms128m -Xmx384m` (was default ~1GB). Note: 256m causes OOM; 384m is the minimum.

## User guides

Step-by-step guides live in `docs/superpowers/guides/`:

1. [Getting started](docs/superpowers/guides/01-getting-started.md)
2. [Connecting social accounts](docs/superpowers/guides/02-connecting-accounts.md)
3. [Media library](docs/superpowers/guides/03-media-library.md)
4. [Creating a post](docs/superpowers/guides/04-creating-a-post.md)
5. [LinkedIn carousel](docs/superpowers/guides/05-linkedin-carousel.md)
6. [Analytics and queue](docs/superpowers/guides/06-analytics-and-queue.md)
7. [Cloudflare database failover](docs/superpowers/guides/07-cf-database-failover.md)
