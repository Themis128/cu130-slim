---
name: n8n-cloudless
description: >-
  Deploys, publishes, audits, and triggers all Cloudless n8n workflows
  (carousel pipeline, daily Slack digest, 13 social post workflows). Covers
  the n8n MCP server (13 tools), TOTP login for the 2FA admin account,
  n8n 2.x publish semantics, and the empty-content DMR model pitfall. Use when
  working with n8n, webhook cloudless-carousel, schedule every 2 days,
  N8N_API_KEY 401/403, import:workflow, publish:workflow, execution
  debugging, or automating social posts through n8n.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# n8n Cloudless Automation

## When to use

- Import / publish / restart workflows (single or all 15)
- Trigger a manual run via webhook (`publish:false` = safe dry-run)
- Fix `401 unauthorized` on `X-N8N-API-KEY` or `403 Forbidden` on executions
- Debug failed/silent executions (TOTP login, empty AI content)
- Change schedule or env vars passed into n8n

## MCP server: `n8n`

Registered in `.devin/mcp_config.json`; script at
`scripts/n8n-mcp-server.py` (stdio, stdlib-only). 13 tools:

| Tool | Purpose |
|------|---------|
| `n8n_health` | healthz + container status + active/inactive counts |
| `n8n_list_workflows` / `n8n_get_workflow` | list / fetch workflow JSON |
| `n8n_list_executions` / `n8n_get_execution` | recent runs; per-node errors |
| `n8n_retry_execution` | retry a failed run |
| `n8n_deploy_workflow` / `n8n_deploy_all` | docker cp → import → publish (+ restart) |
| `n8n_activate_workflow` / `n8n_deactivate_workflow` | toggle active state |
| `n8n_audit_workflows` | static audit: missing TOTP, pinned provider/model, live drift |
| `n8n_trigger_webhook` | POST JSON to a workflow webhook (supports `publish:false`) |
| `n8n_list_credentials` | names/types only — never secrets |

**API key scope**: the UI-minted `N8N_API_KEY` only covers workflows +
credentials (`/executions`, `/tags`, `/variables` → 403). The MCP server
falls back to Postgres (`social-postgres` → `n8n` DB) for execution reads
and rehydrates n8n 2.x's deduplicated `execution_data` format.

## Workflow facts

| Item | Value |
|------|--------|
| Workflow id | `cloudless-cf-carousel-linkedin` |
| Name | `Cloudless CF Carousel → LinkedIn Company` |
| JSON | `n8n-workflows/cloudless-carousel-pipeline.json` |
| Prod webhook | `POST http://localhost:5678/webhook/cloudless-carousel` |
| Schedule | every 2 days at **19:00 Europe/Athens** |
| Calls | social-api login → `/api/v1/ai/run-carousel-and-publish` (`wait_for_publish=false`) — server-side pipeline includes NLP plain-English check/fix, spellcheck (LanguageTool), and SEO scoring |

| Workflow id | `socialauto-daily-slack-digest` |
| Name | `SocialAuto Daily Digest → #socialauto` |
| JSON | `n8n-workflows/socialauto-daily-slack-digest.json` |
| Prod webhook | `POST http://localhost:5678/webhook/socialauto-daily-digest` |
| Schedule | daily **09:00 Europe/Athens** |
| Calls | social-api login → `POST /api/v1/ops/daily-digest` → Slack |

Plus 13 social post workflows (`{platform}-{text|image|carousel}-post`,
`marketing-image-generation`, `weekly-cloud-computing-post`) — each with a
`/webhook/<name>` production webhook. All accept `publish:false` for dry-runs
(posts are created as drafts, nothing is published to platforms).

n8n env (compose): `SOCIAL_API_URL`, `SOCIAL_ADMIN_EMAIL`, `SOCIAL_ADMIN_PASSWORD`,
`SOCIAL_TOTP_SECRET`, `NODE_FUNCTION_ALLOW_BUILTIN=crypto`,
`CLOUDLESS_LINKEDIN_ORG_ACCOUNT_ID`, `CLOUDLESS_CAROUSEL_TOPIC`, `CLOUDLESS_CAROUSEL_SLIDES`,
`SOCIALAUTO_DIGEST_DAYS`, `GENERIC_TIMEZONE=Europe/Athens`, `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`.

Slack posting needs `SLACK_WEBHOOK_URL` (or `SLACK_BOT_TOKEN` + `SLACK_CHANNEL_ID`) on
`social-api` / `social-worker`. Channel: `#socialauto` (`C0BT263L17U`).
Celery beat also runs the same digest at `SLACK_DIGEST_HOUR` (default 09:00 Athens).

## n8n 2.x rules (hard-won)

1. **Publish ≠ draft.** `n8n publish:workflow --id=...` then **restart n8n** so triggers register. `--all` is deprecated — loop per-ID.
2. **`import:workflow` DEACTIVATES.** Every re-import must be followed by publish + restart, or all 15 workflows go inactive silently.
3. **API keys are UI-minted.** No env var auto-creates `N8N_API_KEY`. Create in **Settings → n8n API**. Header: `X-N8N-API-KEY` (not Bearer). Path: `/api/v1/...` (not `/rest/...`).
4. Owner recreate / encryption-key change → old keys return **401**. Mint a new key and update `.env`.
5. Prefer **CLI import** when API key is stale (works without API).

## SocialAuto login from workflows (2FA)

The admin account has TOTP enabled — `/api/v1/auth/login` with only
username/password returns `401 {"detail":"two_factor_required"}`. Every
workflow that calls the API **must**:

1. Run a **Generate TOTP** Code node before the login node — RFC 6238 TOTP via
   `crypto` (requires `NODE_FUNCTION_ALLOW_BUILTIN=crypto` on the n8n
   container), secret from `SOCIAL_TOTP_SECRET` env (same as the user's
   `User.two_factor_secret` in Postgres).
2. Send `otp` as a form field alongside `username`/`password`.

Silent failure mode: workflows whose error branch swallows the login error
(e.g. `Login OK?` → graceful no-op) show **success executions while
publishing nothing**. Always check executions actually produced content —
a `success` status alone proves nothing.

## AI content nodes (DMR pitfall)

Text-generation nodes must call `/api/v1/ai/generate-content` **without** a
hardcoded `provider`/`model` — omitting them lets the app's automatic routing
pick DMR-first → Cloudflare fallback. Never pin `provider:"dmr" +
model:"ai/smollm2"`: it returns HTTP 200 with **empty content**, which then
fails downstream at `Create Post` with `422 must include text, media, or a
link` and surfaces only as a normalized `publish_failed`.

Explicit models that work if pinning is truly needed: `ai/qwen3:8b-q4_K_M`,
`hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M`.

Image-generation nodes keep `provider:"cloudflare" +
model:"@cf/black-forest-labs/flux-1-schnell"` — that is correct, do not remove.

## Tool scripts

From repo root:

```bash
# Import + publish workflow (CLI), then restart n8n
.devin/skills/n8n-cloudless/scripts/deploy-workflow.sh

# Daily analytics + issues digest → Slack #socialauto
.devin/skills/n8n-cloudless/scripts/deploy-daily-digest.sh

# Register in social app Workflows UI (template + deployed workflow)
.devin/skills/n8n-cloudless/scripts/register-workflow.py
# or: docker exec … python scripts/register_cloudless_workflow.py
# or: POST /api/v1/workflows/import-cloudless-carousel

# Refresh N8N_API_KEY into .env (uses scripts/init-n8n-api-key.py)
.devin/skills/n8n-cloudless/scripts/refresh-api-key.sh

# Manual webhook dry-run (3 slides, no LinkedIn publish)
.devin/skills/n8n-cloudless/scripts/trigger-webhook.sh --publish false --slides 3

# Manual webhook publish
.devin/skills/n8n-cloudless/scripts/trigger-webhook.sh --publish true --slides 7
```

Also: `scripts/deploy_n8n_cloudless_carousel.py` (default `--cli`).

## Do not

- Do not print API keys or admin passwords or the TOTP secret
- Do not rely on Ollama for this carousel workflow (n8n Instance AI may still point at Ollama; carousel path must hit social-api / Cloudflare)
- Do not trust `success` execution status alone — verify content was produced (post_id / draft row in `social_automation.posts`)
- Do not re-import without republishing — `import:workflow` deactivates

## More detail

See [reference.md](reference.md).

