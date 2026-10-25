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

- Import / publish / restart workflows (single or all 16)
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

**API key scope**: the current `N8N_API_KEY` (label `devin-cli-exec`) carries
24 scopes — all workflow/credential ops plus `execution:{read,list,retry,stop,delete}`,
`tag:read/list`, `variable:list`. `/executions` and `/tags` are 200; `GET
/variables` still 403s (n8n grants `variable:list` but not `variable:read`).
If execution calls ever 403 again, the key was rotated without scopes —
re-mint via `scripts/init-n8n-api-key.py` (n8n 2.x: session login →
`POST /rest/api-keys`; the 1.x `/api/v1/user/api-keys` endpoint is gone).
The MCP server still falls back to Postgres (`social-postgres` → `n8n` DB)
for execution *detail* reads and rehydrates n8n 2.x's deduplicated
`execution_data` format. The public API DOES return full `runData` for
recent executions with `GET /api/v1/executions/{id}?includeData=true`
(header `X-N8N-API-KEY`) — verified 2026-10-17; use that first, Postgres
only for older/stripped rows (the raw `execution_data` column is a
dedup-indexed array, not plain JSON — resolving it by hand is a trap).

**Retry caveat**: `n8n_retry_execution` resumes a failed run with its
original node outputs — SocialAuto login-node data can be stale/missing, so
retried executions may fail with "Authorization failed" at the first
authenticated node. For workflows that log in per-run, prefer re-firing the
webhook (`n8n_trigger_webhook`) over retry.

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

| Failure signature | Digest shows `success` but nothing reached Slack — read the
  `Summarize Result` node output (via `?includeData=true`): `posted_to_slack`,
  `slack_error`, `teams_reported`. `slack_error: "Slack API error: channel_not_found"`
  = dead-webhook fallback hit a stale `SLACK_CHANNEL_ID` in a container running
  old env (recreate ALL token consumers — see slack-ops). `slack_error:
  "skipped empty team"` + `teams_reported: 7` = CI/E2E teams legitimately skip;
  the real team's report is elsewhere in `reports[]` (seen 2026-10-15/16). |

| Workflow id | `linkedin-newsletter-weekly` |
| Name | LinkedIn Newsletter — The Cloudless Build Log |
| JSON | `n8n-workflows/linkedin-newsletter-weekly.json` |
| Prod webhook | `POST http://localhost:5678/webhook/linkedin-newsletter-weekly` |
| Schedule | weekly Monday **11:00 Europe/Athens** |
| Calls | `POST /api/v1/newsletter/linkedin/issue` (`X-Service-Token: NEWSLETTER_SERVICE_TOKEN`) → Slack notify |
| Slack channel | `SLACK_NEWSLETTER_CHANNEL_ID` → `#socialauto-publishing` `C0C1B8PMTUN`. **The old default `C0BBDKY6Q9E` is a stale foreign-workspace ID — any node still pointing at it fails `channel_not_found`** |
| Failure signature | `httpx.ReadTimeout` at the sidecar call = editor hydration flake → run `linkedin-newsletter-ops/scripts/probe_editor.py`, fall back to `publish_issue.py` (bridge path) |

Plus 13 social post workflows (`{platform}-{text|image|carousel}-post`,
`marketing-image-generation`, `weekly-cloud-computing-post`) — each with a
`/webhook/<name>` production webhook. All accept `publish:false` for dry-runs
(posts are created as drafts, nothing is published to platforms).

n8n env (compose): `SOCIAL_API_URL`, `SOCIAL_ADMIN_EMAIL`, `SOCIAL_ADMIN_PASSWORD`,
`SOCIAL_TOTP_SECRET`,
`CLOUDLESS_LINKEDIN_ORG_ACCOUNT_ID`, `CLOUDLESS_CAROUSEL_TOPIC`, `CLOUDLESS_CAROUSEL_SLIDES`,
`SOCIALAUTO_DIGEST_DAYS`, `GENERIC_TIMEZONE=Europe/Athens`, `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`.
(`NODE_FUNCTION_ALLOW_BUILTIN` no longer lives on the n8n env — with external
task runners the allowlist is the sidecar's `/etc/task-runners.json`; the stock
`n8nio/runners` image already allows `crypto`+`moment`.)

Slack posting needs `SLACK_WEBHOOK_URL` (or `SLACK_BOT_TOKEN` + `SLACK_CHANNEL_ID`) on
`social-api` / `social-worker`. Channel: `#socialauto` (`C0C1F1K3DDF`).
Celery beat also runs the same digest at `SLACK_DIGEST_HOUR` (default 09:00 Athens).

## Base configuration (tuned 2026-10-20)

`n8nio/n8n:2.43.4` (newest release ≥7d old at upgrade time; 2.44.x was <7d).

Production env knobs set in compose (grouped block after `EXECUTIONS_MODE`):

| Knob | Value | Why |
|---|---|---|
| `N8N_SCHEDULER_ENABLED` | `true` | Durable scheduler — `scheduled_job`/`scheduled_task` DB rows; restarts no longer drop pending schedule runs, misfires within `misfireGraceSeconds` still fire. |
| `EXECUTIONS_TIMEOUT` / `_MAX` | `3600` / `86400` | Bounds runaway executions; `linkedin-dm-deferred-reply` overrides to 86400 via `settings.executionTimeout` (Wait-node resume time). |
| `N8N_CONCURRENCY_PRODUCTION_LIMIT` | `10` | Caps concurrent prod executions (default unlimited). |
| `EXECUTIONS_DATA_MAX_AGE` / `PRUNE_MAX_COUNT` | `168` / `2000` | ~100 execs/day → ~700 rows. Slack alerts + `post_targets` are the audit trail, not n8n history. |
| `EXECUTIONS_DATA_SAVE_ON_PROGRESS` | `false` | Per-node progress writes not needed for post-hoc debugging. |
| `N8N_METRICS` + `*_SCHEDULER_METRICS` + `*_WORKFLOW_ID_LABEL` + `*_API_ENDPOINTS` | `true` | `/metrics` on :5678; exposed to omv Prometheus via the `n8n-metrics` nginx proxy on LAN :9392 (metrics-only, rest 404 — same pattern as `social-metrics`:9390). |
| `N8N_RUNNERS_MODE` | `external` | Code-node JS runs in the `n8n-task-runner` sidecar (`n8nio/runners:2.43.4`, broker `n8n:5679`, shared `N8N_RUNNERS_AUTH_TOKEN`). Internal mode is deprecated/insecure. |
| `N8N_UNVERIFIED_PACKAGES_ENABLED` / `COMPRESSION_NODE_*` | pinned | Silences startup deprecation warnings; matches future defaults. |

Sidecars added: `n8n-task-runner` (backend net only, `depends_on n8n healthy`)
and `n8n-metrics` (LAN :9392). When n8n is recreated (e.g. slack_token_sync
drain), runners reconnect to the broker automatically — no action needed.

**Follow-up**: n8n warns `Postgres 16 is outside the supported range` —
social-postgres is PG16; a PG17 upgrade is a separate tracked task (shared DB,
needs dump/restore planning).

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
.devin/skills/n8n-cloudless/scripts/deploy-workflow.py

# Daily analytics + issues digest → Slack #socialauto
.devin/skills/n8n-cloudless/scripts/deploy-daily-digest.py
# Validate workflow JSON before import/publish (naming, error handling,
# security, performance, docs). Exit 1 on issues — gate pre-deploy.
node .devin/skills/n8n-cloudless/scripts/validate-n8n-workflow.js \
    --dir n8n-workflows        # all local workflow files
node .devin/skills/n8n-cloudless/scripts/validate-n8n-workflow.js \
    --live cloudless-cf-carousel-linkedin   # or export from the container


# Register in social app Workflows UI (template + deployed workflow)
.devin/skills/n8n-cloudless/scripts/register-workflow.py
# or: docker exec … python scripts/register_cloudless_workflow.py
# or: POST /api/v1/workflows/import-cloudless-carousel

# Refresh N8N_API_KEY into .env (uses scripts/init-n8n-api-key.py)
.devin/skills/n8n-cloudless/scripts/refresh-api-key.py

# Manual webhook dry-run (3 slides, no LinkedIn publish)
.devin/skills/n8n-cloudless/scripts/trigger-webhook.py --publish false --slides 3

# Manual webhook publish
.devin/skills/n8n-cloudless/scripts/trigger-webhook.py --publish true --slides 7

# Authenticated API call inside social-api (admin login + TOTP from DB,
# no hand-rolled auth): trigger/inspect any /api/v1 endpoint
docker exec -i social-api python3 /app/scripts/ops_api.py POST \
    "/api/v1/ops/daily-digest?days=1&post_to_slack=false&post_to_email=false"
```

Also: `scripts/deploy_n8n_cloudless_carousel.py` (default `--cli`).

## Data-driven automation (automation-plan)

- `GET /api/v1/ops/automation-plan` returns the strategy brief's playbook as
  JSON: per-action `platform`, `format`, `time_local` (Athens), resolved
  `schedule_at` (UTC ISO), `media_required`, and the n8n `webhook` that can
  execute it. `use_llm=false` forces the deterministic rule fallback.
- Orchestrator `socialauto-automation-plan` (cron `0 7 * * *` Athens, webhook
  `socialauto-automation-plan`) fetches the plan and dispatches each action
  to its platform webhook with `{topic: instruction, scheduled_at,
  publish: false}` — posts land as SCHEDULED at the measured best window
  instead of firing immediately.
- All `*-post` workflows accept `scheduled_at` in the webhook body; when
  present `publish` flips to false automatically and `create-post` stores it
  on the post. Never dispatch with a `schedule_at` in the past — the
  Explode Actions node drops those.

## Workflow JSON authoring pitfalls (verified 2026-10-04)

1. **`webhookId` is required on Webhook nodes.** The n8n UI assigns it on canvas
   creation; workflows authored as JSON/imported via API or CLI must set
   `"webhookId": "<slug>"` explicitly or the webhook never registers
   (`404 "webhook is not registered"` while the workflow shows active).
2. **`import:workflow` requires an `id` field** in the JSON — no id, no import.
   Reuse stable slug ids across redeploys (e.g. `nextcloud-drop-to-draft`).
3. **HTTP Request `jsonBody`**: fields beginning with `=` evaluate the whole
   value as an expression producing a JS object literal; embedded
   `{{ expr }}` interpolations run first. Keep each interpolated value a
   single `JSON.stringify(...)` of a precomputed field — build complex
   strings in a preceding Code node (`gen_prompt` pattern). A malformed
   interpolation surfaces as "The value in the JSON Body field is not
   valid JSON" at runtime.
4. **`.item` pairing**: `$('Node').item.json` resolves via paired items —
   works through If/Switch nodes but fails if the referenced node produced
   zero items (e.g. empty inbox). Guard or default early.
5. **NextCloud node is broken for modern NC** — see `omv-nextcloud-ops`
   skill (issue n8n-io/n8n#8802). Use raw HTTP PROPFIND/GET/MOVE instead.
6. **Debug loop that works**: trigger webhook → `n8n_list_executions` →
   `n8n_get_execution` (per-node errors via Postgres fallback — the API key
   403s on /executions). The last executed node in `lastNodeExecuted` is
   the failing one.
7. **`contentType:'json'` is dead config on httpRequest ≥4.2** — the body
   ships EMPTY silently; remote APIs just 400. The only working contract
   is `sendBody:true` + `specifyBody:'json'` + `jsonBody` (proven in
   `facebook-text-post.json` etc.). `contentType:'form-urlencoded'` +
   `bodyParameters` is the valid keypair equivalent (login node).
8. **`httpBasicAuth` credentials can silently ship bad auth in prod**
   (2026-10-15: node received NC guest-page HTML while identical
   in-container requests with the same creds succeeded; the credential
   store/wiring is opaque to debugging). Some workflows do work with them
   (`nextcloud-drop-to-draft`), but the reliable pattern is an explicit
   `Authorization` header computed in a Code node from `$env` —
   `Basic base64($env.NEXTCLOUD_USERNAME + ':' + $env.NEXTCLOUD_APP_PASSWORD)`
   — same `$env` mechanism as `SOCIAL_ADMIN_*` on login nodes.
   `NEXTCLOUD_USERNAME`/`NEXTCLOUD_APP_PASSWORD` are in the n8n service
   env (docker-compose) specifically for this.
9. **Audit before deploy**: `python3
   .cursor/skills/n8n-cloudless/scripts/audit-http-nodes.py n8n-workflows/`
   catches both patterns (exit 1 on findings).

## Do not

- Do not print API keys or admin passwords or the TOTP secret
- Do not rely on Ollama for this carousel workflow (n8n Instance AI may still point at Ollama; carousel path must hit social-api / Cloudflare)
- Do not trust `success` execution status alone — verify content was produced (post_id / draft row in `social_automation.posts`)
- Do not re-import without republishing — `import:workflow` deactivates

## More detail

See [reference.md](reference.md).

