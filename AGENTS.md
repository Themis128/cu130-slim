# Agent working notes for cu130-slim / SocialAuto

## Commit & push cadence

- After every **~15 file changes** or at the end of a **major implementation chunk**, run the test gate below, commit, and push.
- If local history has been cleaned/rewritten, use `git push --force-with-lease` and mention it in the summary.
- Do not leave the working tree dirty at the end of a session unless the user explicitly asked for uncommitted changes.

## Test gate before commit/push

Run these for any feature that touches backend, frontend, compose, or n8n:

1. `pytest tests/unit -q` inside `social-api` — must pass.
2. `pytest tests/integration -q` against a dedicated `social_automation_test` DB — must pass when media, AI, auth, or storage behavior changes.
3. `ruff check` on changed backend files — must be clean.
4. `docker compose config --quiet` — must be valid.
5. `curl http://localhost:8083/health` after `social-api` restart — must return ok.
6. `docker compose exec -T social-worker celery -A app.worker.celery_app inspect ping` after worker restart — must reply.
7. Validate all Mermaid diagrams in `docs/superpowers/plans/plan-e6a92ed66b9dca4a.md` (e.g. via mermaid.ink) when they change.
8. Frontend: `tsc --noEmit` or `next build` in a Node container with the source mounted — no TS errors.
9. If n8n workflows changed: import, publish, and trigger a dry run.
10. `curl http://localhost:8083/api/v1/cf-db/health` — D1, KV, and Vectorize should report `true` when Cloudflare is reachable.

## Container restart rules

- `social-api` — restart after any `app/api/*.py`, `app/services/*.py`, `app/models/*.py`, or Alembic change.
- `social-worker` — restart after `app/services/publishing.py`, `app/services/linkedin_api.py`, Celery tasks, or compose env changes.
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
- Never commit secrets (`.env`, `N8N_API_KEY`, Cloudflare tokens, admin password).
- Do not change the public Docker Compose port mappings (e.g. `social-api:8083`, `social-frontend:8082`, `n8n:5678`, `chroma:8001`, `languagetool:8010`, `ollama:11435`, `comfyui:8000`, `metabase:3000`). New internal services may use unmapped ports only after confirming no conflicts.

## Documentation & diagrams

- If the scope, architecture, or behavior changes, update `docs/superpowers/plans/plan-e6a92ed66b9dca4a.md` (and its Devin copy at `~/.devin/plans/plan-e6a92ed66b9dca4a.md`) and any affected `AGENTS.md` / `README` notes.
- Keep the Mermaid diagrams in the roadmap in sync with the actual flows (inference fallback, media lifecycle, publishing, etc.).
- Every new feature or user-facing workflow must have a step-by-step guide in `docs/superpowers/guides/`. Add a link in the guides `README.md` and, if relevant, in the frontend empty-state or help text.
