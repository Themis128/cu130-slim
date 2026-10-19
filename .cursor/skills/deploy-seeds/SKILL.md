---
name: deploy-seeds
description: >-
  Version-controlled DB seeds for SocialAuto — deploy/seeds/*.json applied
  idempotently on every social-api boot via
  social-automation/backend/scripts/seed_content_templates.py. Covers the
  Oxford AIE content_prompt_templates set (with production UUIDs), the
  upsert-by-(team,name) contract, team-scoped pillar resolution, and how to
  add new seeded rows. Use on deploys, fresh installs, when a template/seed
  row is missing in prod, or when adding new reference data that must exist
  in every environment.
---

# Deploy Seeds

Reference/seed data must live in **version control**, not only in the live
DB — a fresh install must be reproducible (Devin Review PR #740).

## Layout

```
deploy/seeds/
  content_prompt_templates.json   — canonical template set (prod UUIDs kept)

social-automation/backend/scripts/
  seed_content_templates.py       — idempotent upsert runner
```

## The tool

`seed_content_templates.py`:

- Matches rows by **(team_id, name)** — existing → UPDATE to seed content,
  missing → INSERT preserving the JSON `id` (prod reseed = no-op update,
  verified live 2026-10-19).
- Resolves `pillar_name` → team-scoped `pillars.id` (created if absent) —
  pillar ids differ per install, never hardcode them.
- `--team-id <uuid>` targets a team; default = first team found.
- `--file <path>` overrides the seed JSON (needed outside the repo/container
  mount, e.g. `docker exec social-api python scripts/seed_content_templates.py
  --file /tmp/x.json`).

Run it:

```bash
# container (bind-mounted, no rebuild needed)
docker exec -w /app social-api python scripts/seed_content_templates.py
# local venv
cd social-automation/backend && ./.venv-test/bin/python scripts/seed_content_templates.py
```

## Deploy wiring (always-on)

- `docker-compose.yml` social-api command runs
  `alembic upgrade head && (python scripts/seed_content_templates.py || warn)
  && uvicorn` — seeds apply on **every container start**, fail-soft so a bad
  seed never blocks the API.
- `deploy/seeds` is bind-mounted at `/app/deploy/seeds:ro` in the compose
  service; the standalone Dockerfile CMD also calls the seeder (warns if the
  seed file isn't in the image context).
- **Rule (memory): when deploying social-api, the seed runs automatically —
  for manual ops, always run `seed_content_templates.py` after
  `alembic upgrade head` on any fresh environment.**

## Adding a new seeded template

1. Create it in prod or locally, then export:
   `docker exec social-api python -c "...SELECT ... WHERE name='X'..."` →
   append the row to `deploy/seeds/content_prompt_templates.json` (keep the
   prod UUID in `id`, set `pillar_name` not `pillar_id`).
2. Run the seeder — expect `~ updated`/`= unchanged` on prod,
   `+ created` on fresh installs.
3. Commit the JSON — it IS the source of truth now.

## Oxford AIE seed set (production UUIDs)

| Name | id | pillar_name |
|---|---|---|
| `Oxford AIE — AI failure modes` | `5405168b-0d73-4373-a1ed-81285641cd8b` | Proof |
| `Oxford AIE — Auditing AI systems` | `f1d00394-5b6c-438a-a5ca-2824387423bf` | Proof |
| `Oxford AIE — EU AI Act` | `474a19ee-5a85-4fb9-a9a9-788a89cb6b6f` | Proof |
| `Oxford AIE — Global regulation divergence` | `a3e8f620-7759-4874-9438-91aa996fbb80` | Perspective |

Usage: `POST /api/v1/ai/generate-content` with `template_id` set to the UUID
(variables: `topic`, `platform`, `tone`, `brand`).
