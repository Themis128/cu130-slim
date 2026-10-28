# scripts/

Operational tools for the Cloudless SocialAuto stack, grouped by concern.
Skill-specific scripts live under `.devin/skills/<skill>/scripts/` — this
directory holds stack-level tools referenced by docs, tasks, and workflows.
One-off scripts that were applied once (compose fixes, old prompt tests,
superseded variants) are in `archive/` — kept for reference, not maintained.

## Sessions & browsers

| Tool | Purpose |
|---|---|
| `session_health.py` | One-shot health check across all session types |
| `session_transplant.py` | Move a logged-in browser session between transports |
| `twitter_browser_login.py` | X/Twitter login via shared browser bridge |
| `pw_mcp_x_login.py` | X login driven through the Playwright MCP |
| `tiktok_qr_watch.py` | Watch for TikTok QR-login completion |
| `browser_contention_check.py` | Detect browser instance/bridge contention |
| `inject_prompt.py` / `.js` | Inject a prompt into a live automation browser |

Related skill: `session-ops`, `browser-ops`.

## Publishing & content

| Tool | Purpose |
|---|---|
| `publish_queue.py` | Inspect/drain the SocialAuto publish queue |
| `publish_preflight.py` | Pre-publish checks + maintenance window |
| `maintenance_window.py` | Open/close the publish maintenance window |
| `dedupe_posts.py` | Find/remove duplicate scheduled posts |
| `build_playbook_pdf.py` | Render the automation-checklist PDF |
| `publish_enhanced_cf_carousel.py` | Publish the CF carousel pipeline output |
| `deploy_n8n_cloudless_carousel.py` | Deploy the carousel n8n workflow |
| `register_cloudless_workflow.py` | Register a workflow in n8n |

Related skills: `publish-ops`, `cloudless-carousel-pipeline`, `n8n-cloudless`.

## Deploy & Docker

| Tool | Purpose |
|---|---|
| `build-tag-push-all.py` | Build, tag, push all service images |
| `deploy-api.py` | Deploy social-api |
| `update-compose-image-tags.py` | Sync compose image tags |
| `validate-compose-env.py` | Validate env vars vs compose |
| `check-compose-ports.py` | Detect host port collisions |
| `dmr/gpu-runner-recreate.py` | Recreate the DMR GPU model runner |
| `gpu_serial.py` | Serialize GPU jobs (media_gpu_lock helper) |
| `metrics-proxy-watchdog.py` | Metrics endpoint watchdog (Windows scheduled task) |

Related skills: `social-stack-ops`, `docker-model-runner`.

## Cloudflare & data

| Tool | Purpose |
|---|---|
| `cf_tokens.py` | Create/scope/rotate Cloudflare API tokens |
| `cf_access.py` | Zero Trust Access helpers |
| `d1_ops.py` | D1 database operations |
| `pg_to_d1.py` | PostgreSQL → D1 export/sync |

Related skill: `cloudflare-ops`.

## Setup & misc

| Tool | Purpose |
|---|---|
| `generate-env-secrets.py` | Generate `.env` secret values |
| `setup-github-secrets.py` | Push secrets to GitHub Actions |
| `init-n8n-api-key.py` | Bootstrap the n8n API key |
| `dodo_setup.py` | Dodo payments provider setup |
| `inject_prompt.js` | JS variant of prompt injector |
