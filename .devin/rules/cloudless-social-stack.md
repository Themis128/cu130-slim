---
name: cloudless-social-stack
description: Cloudless social automation stack conventions (CF carousel, LinkedIn org, n8n)
trigger: always_on
---

# Cloudless social stack

This repo runs Docker Compose (`cu130-slim`) with social-api, social-worker, n8n, Redis, Postgres.

## Product defaults

- LinkedIn carousels for **cloudless.gr** post as **Company Page** account `9c4451bb-e820-489f-8676-76ddbc788ffe`, not personal.
- Carousel slide backgrounds use **ComfyUI FLUX.1-schnell GGUF** (local GPU, `media_gpu_lock`) → Cloudflare Workers AI FLUX schnell fallback; copy/NLP stays DMR (`ai/llama3.2`) + CF.
- Automate via **n8n** workflow `cloudless-cf-carousel-linkedin` when the user wants scheduling/webhooks.

## Social funnel (conversion → Polar on social.cloudless.gr)

- **LinkedIn CTA** — corporate value angle (server/cloud cost savings, real engineering outcomes). Point high-intent readers to the free 30-minute audit: `https://cloudless.gr/contact`. Post as the Company Page.
- **X CTA** — developer value angle (raw code improvements, p95 latencies, implementation wins — real numbers only). Point to the Polar-backed plans/starter kits: `https://social.cloudless.gr/pricing` (`BILLING_PROVIDER=polar`). `@TBaltzakis` profile website link already routes there (t.co shortcode — resolves to /pricing).
- Claims in posts must be real outcomes from this stack (e.g. 200s→10ms session polls) — never invented metrics.

## Agent skills (read and follow)

- `.devin/skills/cloudless-carousel-pipeline/SKILL.md`
- `.devin/skills/n8n-cloudless/SKILL.md`
- `.devin/skills/social-stack-ops/SKILL.md`

Per-channel content skills — `social-content-core` (shared rules, account map,
funnel, quality gate) plus `linkedin-content-ops`, `facebook-content-ops`,
`instagram-content-ops`, `threads-content-ops`, `x-content-ops`,
`tiktok-content-ops`, `messaging-channel-ops`. Read core before any channel.

Platform API ops skills — `meta-graph-ops`, `linkedin-graph-ops`,
`instagram-graph-ops`, `tiktok-api-ops`, `x-api-ops`, `whatsapp-ops`,
`viber-ops`, `bluesky-ops`, `slack-ops`, `monetization-ops` — each wraps a
`backend/scripts/*_tool.py` CLI for token/credential/endpoint diagnostics.

## Safety

- Never print or commit `.env` secrets (`N8N_API_KEY`, admin passwords, Cloudflare tokens, `GITHUB_TOKEN`).
- Prefer skill scripts under `.devin/skills/*/scripts/` for deploy/trigger/status.
- Restart `social-worker` after publishing/worker code changes.
