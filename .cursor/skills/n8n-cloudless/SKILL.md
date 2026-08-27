---
name: n8n-cloudless
description: >-
  Deploys, publishes, and triggers the Cloudless n8n workflow that runs the CF
  LinkedIn carousel pipeline via social-api. Use when working with n8n,
  webhook cloudless-carousel, schedule every 2 days, N8N_API_KEY 401,
  import:workflow, publish:workflow, or automating social posts through n8n.
---

# n8n Cloudless Automation

## When to use

- Import / publish / restart the Cloudless carousel workflow
- Trigger a manual run via webhook
- Fix `401 unauthorized` on `X-N8N-API-KEY`
- Change schedule or env vars passed into n8n

## Workflow facts

| Item | Value |
|------|--------|
| Workflow id | `cloudless-cf-carousel-linkedin` |
| Name | `Cloudless CF Carousel → LinkedIn Company` |
| JSON | `n8n-workflows/cloudless-carousel-pipeline.json` |
| Prod webhook | `POST http://localhost:5678/webhook/cloudless-carousel` |
| Schedule | every 2 days at **19:00 Europe/Athens** |
| Calls | social-api login → `/api/v1/ai/run-carousel-and-publish` (`wait_for_publish=false`) |

n8n env (compose): `SOCIAL_API_URL`, `SOCIAL_ADMIN_EMAIL`, `SOCIAL_ADMIN_PASSWORD`,
`CLOUDLESS_LINKEDIN_ORG_ACCOUNT_ID`, `CLOUDLESS_CAROUSEL_TOPIC`, `CLOUDLESS_CAROUSEL_SLIDES`,
`GENERIC_TIMEZONE=Europe/Athens`, `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`.

## n8n 2.x rules (from docs)

1. **Publish ≠ draft.** Use `n8n publish:workflow --id=...` then **restart n8n** so triggers register.
2. **API keys are UI-minted.** There is no env var that auto-creates `N8N_API_KEY`. Create in **Settings → n8n API**. Header: `X-N8N-API-KEY` (not Bearer). Path: `/api/v1/...` (not `/rest/...`).
3. Owner recreate / encryption-key change → old keys return **401**. Mint a new key and update `.env`.
4. Prefer **CLI import** when API key is stale (works without API).

## Tool scripts

From repo root:

```bash
# Import + publish workflow (CLI), then restart n8n
.cursor/skills/n8n-cloudless/scripts/deploy-workflow.sh

# Register in social app Workflows UI (template + deployed workflow)
.cursor/skills/n8n-cloudless/scripts/register-workflow.py
# or: docker exec … python scripts/register_cloudless_workflow.py
# or: POST /api/v1/workflows/import-cloudless-carousel

# Refresh N8N_API_KEY into .env (uses scripts/init-n8n-api-key.py)
.cursor/skills/n8n-cloudless/scripts/refresh-api-key.sh

# Manual webhook dry-run (3 slides, no LinkedIn publish)
.cursor/skills/n8n-cloudless/scripts/trigger-webhook.sh --publish false --slides 3

# Manual webhook publish
.cursor/skills/n8n-cloudless/scripts/trigger-webhook.sh --publish true --slides 7
```

Also: `scripts/deploy_n8n_cloudless_carousel.py` (default `--cli`).

## Do not

- Do not print API keys or admin passwords
- Do not rely on Ollama for this carousel workflow (n8n Instance AI may still point at Ollama; carousel path must hit social-api / Cloudflare)

## More detail

See [reference.md](reference.md).
