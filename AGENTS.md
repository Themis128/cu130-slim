# Agent working notes for cu130-slim / SocialAuto

## Commit & push cadence

- After every **~15 file changes** or at the end of a **major implementation chunk**, run the test gate below, commit, and push.
- If local history has been cleaned/rewritten, use `git push --force-with-lease` and mention it in the summary.
- Do not leave the working tree dirty at the end of a session unless the user explicitly asked for uncommitted changes.

## Test gate before commit/push

Run these for any feature that touches backend, frontend, compose, or n8n:

1. `pytest tests/unit -q` inside `social-api` — must pass.
2. `ruff check` on changed backend files — must be clean.
3. `docker compose config --quiet` — must be valid.
4. `curl http://localhost:8083/health` after `social-api` restart — must return ok.
5. `docker compose exec -T social-worker celery -A app.worker.celery_app inspect ping` after worker restart — must reply.
6. Frontend: `tsc --noEmit` or `next build` in a Node container with the source mounted — no TS errors.
7. If n8n workflows changed: import, publish, and trigger a dry run.

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
```

## Product defaults to preserve

- LinkedIn carousels for **cloudless.gr** post as the **Company Page** account `4a8d9440-47d2-4bda-bd11-3776fd9022ba`, not a personal profile.
- Carousel generation uses **Cloudflare Workers AI only**.
- **Cloudflare-first, free-first** for all inference and object storage; prefer Cloudflare Workers AI and R2. Use Ollama as the last-resort fallback.
- Every media text field (`alt_text`, `tags`, `ai_caption`, `generation_prompt`, `filename`) is spell/grammar-corrected via LanguageTool before storage.
- Never commit secrets (`.env`, `N8N_API_KEY`, Cloudflare tokens, admin password).

## Documentation & diagrams

- If the scope, architecture, or behavior changes, update `docs/superpowers/plans/plan-e6a92ed66b9dca4a.md` (and its Devin copy at `~/.devin/plans/plan-e6a92ed66b9dca4a.md`) and any affected `AGENTS.md` / `README` notes.
- Keep the Mermaid diagrams in the roadmap in sync with the actual flows (inference fallback, media lifecycle, publishing, etc.).
- Every new feature or user-facing workflow must have a step-by-step guide in `docs/superpowers/guides/`. Add a link in the guides `README.md` and, if relevant, in the frontend empty-state or help text.
