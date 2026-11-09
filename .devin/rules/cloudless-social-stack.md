---
name: cloudless-social-stack
description: Cloudless social automation stack conventions (CF carousel, LinkedIn org, n8n)
trigger: always_on
---

# Cloudless social stack

This repo runs Docker Compose (`cu130-slim`) with social-api, social-worker, n8n, Redis, Postgres.

## Product defaults

- LinkedIn carousels for **cloudless.gr** post as **Company Page** account `9c4451bb-e820-489f-8676-76ddbc788ffe`, not personal.
- **Email campaigns** (`/campaigns`, `/api/v1/email-campaigns`) must be unique (30-day dedup gate) and written in the Sofia Kakkava Visibility Era voice — subject=hook, one point, one CTA; `PLATFORM_GUIDES["email"]` + `voice_signature.email_formula` enforce it; UI has a "Draft with AI" button.
- Carousel slide backgrounds use **ComfyUI FLUX.1-schnell GGUF** (local GPU, `media_gpu_lock`) → Cloudflare Workers AI FLUX schnell fallback; copy/NLP stays DMR (`ai/llama3.2`) + CF.
- Automate via **n8n** workflow `cloudless-cf-carousel-linkedin` when the user wants scheduling/webhooks.
- **Dev-apps → datalake → reports → per-channel publishing** — standing directive: pull live data from every connected social developer app (`devapps-sync`, 6h) → `analytics_events` → `export-datalake` (`lake/socialauto-devapps/*`, `socialauto-channel-actions/*`) → preprocessing + analytics (`datalake-insights`) → report sections → `devapp-channel-actions` → `schedule_report_actions` generates/schedules posts per channel. Never fabricate metrics; unhealthy accounts produce health/remediation actions, not posts.
- **Central brain orchestrates daily decisions** — `central-brain-daily` (06:30 Athens) collects devapp health, 7d engagement, follower snapshots and hot `trend_signals`, scores channels, journals the plan to `company_brain`, and dispatches post actions through `schedule_report_actions` (full-auto; unhealthy channels never post). Market adjustments come from `trend_signals` opportunity/early_window classifications.
- **Nextcloud always carries latest docs** — `sync-nextcloud-docs` (6h) mirrors merged documentation/architecture from GitHub master into Nextcloud Files at `SocialAuto/docs/repo/`. When docs or architecture change, they must reach Nextcloud on the next sweep — do not treat the mirror as optional.

## Social funnel (conversion → Polar on social.cloudless.gr)

- **LinkedIn CTA** — corporate value angle (server/cloud cost savings, real engineering outcomes). Point high-intent readers to the free 30-minute audit: `https://cloudless.gr/contact`. Post as the Company Page.
- **Consultation booking page** — `https://cloudless.gr/en/consultation` (canonical landing; `/consultation` 307s to it, locales en/el/fr/de). Live slot picker over `/api/calendar/*` (Cal.com → Google Calendar fallback, Athens time). Goes live with site PR #2364 merge+deploy; verify 200 before using it as a CTA target.
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
`viber-ops`, `bluesky-ops`, `slack-ops`, `monetization-ops`,
`polar-ops` — each wraps a
`backend/scripts/*_tool.py` CLI for token/credential/endpoint diagnostics.

## Safety

- Never print or commit `.env` secrets (`N8N_API_KEY`, admin passwords, Cloudflare tokens, `GITHUB_TOKEN`).
- Prefer skill scripts under `.devin/skills/*/scripts/` for deploy/trigger/status.
- Restart `social-worker` after publishing/worker code changes.

## Data-driven mandate

- **All skills are data-driven** — every decision (what/when/where to post,
  account/media/voice choice, platform health) must cite a live query:
  `posts`/`follower_snapshots`/`social_accounts`/`publish_queue`, the latest
  strategy brief, `company_brain.recall()` journals, Nextcloud Tables/Deck,
  Metabase MCP, or `scripts/*_tool.py` CLIs. Static heuristics are hints, not
  decisions. Canonical decision→surface map: `social-content-core` SKILL.md.
