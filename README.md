# cu130-slim

Self-hosted social-automation stack for Cloudless (`cloudless.gr`).

## Stack

| Service | Port | Purpose |
|---------|------|---------|
| n8n | 5678 | Workflow automation (Cloudless carousel, daily Slack digest) |
| social-api | 8083 | FastAPI backend (`/api/v1`) |
| social-frontend | 8082 | Next.js dashboard |
| social-worker | - | Celery worker for publishing + digests |
| redis | 6379 | Queue/cache |
| social-postgres | 5433 | Application database |
| postgres | 5432 | Metabase database |
| chroma | 8001 | Vector store for duplicate detection |
| languagetool | 8010 | Self-hosted spell/grammar checker |
| ollama | 11435 | Local LLM inference |
| comfyui | 8000 | ComfyUI image generation (requires NVIDIA) |
| metabase | 3000 | BI dashboards |

## Quick start

1. Copy and fill `.env`:
   ```bash
   cp .env.example .env
   # Add real CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID, and platform OAuth secrets
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

- `.cursor/skills/cloudless-carousel-pipeline/SKILL.md`
- `.cursor/skills/n8n-cloudless/SKILL.md`
- `.cursor/skills/social-stack-ops/SKILL.md`

## Notes

- The Cloudless carousel uses **Cloudflare Workers AI only** (`@cf/meta/llama-3.2-3b-instruct` for text, `@cf/black-forest-labs/flux-1-schnell` for images). No Ollama/ComfyUI fallback.
- LinkedIn posts target the Cloudless Company Page account `4a8d9440-47d2-4bda-bd11-3776fd9022ba` by default.
- n8n workflow `cloudless-cf-carousel-linkedin` runs every 2 days at 19:00 Europe/Athens.
- `N8N_API_KEY` must be minted in the n8n UI; there is no automatic `.env` value.
