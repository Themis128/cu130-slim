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
- LinkedIn posts target the Cloudless Company Page account `4a8d9440-47d2-4bda-bd11-3776fd9022ba` by default.
- n8n workflow `cloudless-cf-carousel-linkedin` runs every 2 days at 19:00 Europe/Athens.
- `N8N_API_KEY` must be minted in the n8n UI; there is no automatic `.env` value.
