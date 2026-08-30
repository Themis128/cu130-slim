---
name: cloudless-social-stack
description: Cloudless social automation stack conventions (CF carousel, LinkedIn org, n8n)
trigger: always_on
---

# Cloudless social stack

This repo runs Docker Compose (`cu130-slim`) with social-api, social-worker, n8n, Redis, Postgres.

## Product defaults

- LinkedIn carousels for **cloudless.gr** post as **Company Page** account `4a8d9440-47d2-4bda-bd11-3776fd9022ba`, not personal.
- Carousel generation uses **Cloudflare Workers AI only** (not Ollama/ComfyUI for this path).
- Automate via **n8n** workflow `cloudless-cf-carousel-linkedin` when the user wants scheduling/webhooks.

## Agent skills (read and follow)

- `.devin/skills/cloudless-carousel-pipeline/SKILL.md`
- `.devin/skills/n8n-cloudless/SKILL.md`
- `.devin/skills/social-stack-ops/SKILL.md`

## Safety

- Never print or commit `.env` secrets (`N8N_API_KEY`, admin passwords, Cloudflare tokens, `GITHUB_TOKEN`).
- Prefer skill scripts under `.devin/skills/*/scripts/` for deploy/trigger/status.
- Restart `social-worker` after publishing/worker code changes.
