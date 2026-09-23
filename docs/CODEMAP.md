# SocialAuto (social.cloudless.gr) — Codemap

Architecture map for the `cu130-slim` repo. Companion to `AGENTS.md` (rules) — this file is the "where things live and how they connect" reference. Last verified: 2026-09-22 (hardware + compose ports re-checked on OFFICE/WSL).

## What it is

SocialAuto: multi-platform social media automation — compose/publish posts, unified DM inbox with AI auto-reply, brand system, media generation, analytics. FastAPI backend + Next.js frontend + 4 queue-dedicated Celery workers + per-platform browser sidecars, all on one Docker Compose stack on the WSL workstation.

## Request path

```
browser → social.cloudless.gr → Cloudflare Tunnel (social-cloudflared)
        → social-frontend:8083 (Next.js UI; host port 8082→8083)
        → /api/* → social-api:8000 (FastAPI, host port 8083→8000)
```

Public edge is **Cloudflare Access-gated** (app `socialauto-app`, admin emails only). Public Access bypass apps exist for paths that third parties must reach unauthenticated:

| Access app | Path prefix | Why public |
|---|---|---|
| `socialauto-public` | `/api/v1/health` | monitoring |
| `socialauto-oauth-callbacks` | `/api/v1/auth/oauth/` | OAuth redirects from Meta/TikTok/X/LinkedIn |
| `socialauto-meta-data-deletion` | `/api/v1/auth/data-deletion` | Meta POSTs signed_request server-to-server |
| (pre-existing bypasses) | `/api/v1/messenger/webhook`, `/api/v1/whatsapp/webhook`, `/api/v1/telegram/webhook/`, `/api/v1/billing/*` webhooks | Meta/Telegram/Polar/Dodo webhooks |

**Gotcha:** OAuth callback URLs registered in the Meta app must be reachable without Access — verify with `curl -o /dev/null -w "%{http_code}" https://social.cloudless.gr/api/v1/auth/oauth/facebook/callback` → `422` is good (reached origin), `302` to cloudflareaccess.com is bad.

## Compose services & host ports

| Service | Port(s) | Role |
|---|---|---|
| social-api | 8083→8000 | FastAPI backend |
| social-frontend | 8082→8083 | Next.js UI |
| social-postgres | 5433→5432 | app DB `social_automation` |
| redis | 6379 | Celery broker + cache failover |
| celery-beat | — | scheduler (single instance) |
| social-worker-{publishing,media,default,messenger} | — | 4 queue-dedicated Celery workers, same image/env |
| cloudflared | — | named tunnel → social.cloudless.gr |
| browser-novnc | 9223 (bridge), 6080 (noVNC), 5900 (VNC) | shared Chromium for DM/profile ops |
| tiktok-browser-sidecar | 9224 | TikTok browser session |
| linkedin-browser-sidecar | 9225 | LinkedIn browser session |
| facebook-browser-sidecar | 9226 | Facebook browser session |
| messenger-sidecar | 9230 | personal Messenger browser |
| instagram-private-api | 8011 | aiograpi REST wrapper (mobile private API) |
| warp-proxy | 1080 | free Cloudflare WARP SOCKS5 (first-choice proxy) |
| n8n | 5678 | workflow automation |
| comfyui | 8000→8000 | GPU image gen (shares RTX 3070 with DMR / local-diffusers) |
| local-diffusers | (no host publish; :7860 internal) | SD 1.5 GPU image gen |
| chroma | 8001→8000 | vector failover |
| minio | 9100→9000, 9101→9001 | S3 failover (host 9100/9101 — not 9000) |
| languagetool | 8010→8010 | spellcheck |
| env-manager-frontend / backend | 8080, 8081 | .env UI/API |
| postgres (metabase) / metabase | 5432 (internal), 3000 | analytics warehouse — separate from social-postgres |
| flower | 5555 | Celery UI |
| social-metrics | 9390→80 | Prometheus/nginx metrics |
| dmr-watchdog | — | restarts docker-model-runner when wedged |

Not in Compose: **DMR** (Docker Model Runner, host engine on `localhost:12435` / `host.docker.internal:12435`). Port **12434 is unused** on this workstation.

## Workstation (OFFICE / WSL)

Measured on this machine (do not invent different HW — re-measure if the host changes). Full DMR/GPU detail: [`dmr-architecture.md`](dmr-architecture.md). Image pins: [`docker-image-registry.md`](docker-image-registry.md).

| Fact | Value (2026-09-22) |
|---|---|
| Host OS | Windows 11 Enterprise Insider Preview (build 26220) |
| WSL distro | Ubuntu 26.04.1 LTS (`Ubuntu-26.04`), kernel 6.18.x WSL2 |
| CPU (Windows) | 12th Gen Intel Core i9-12900H — 14 cores / 20 threads |
| CPU visible to Docker/WSL | 8 vCPUs (Docker Desktop allocation) |
| RAM (Windows physical) | ~32 GiB |
| RAM visible to Docker/WSL | ~15.6 GiB total (~7 GiB available under load when measured) |
| GPU | NVIDIA GeForce RTX 3070 Laptop GPU, **8192 MiB** VRAM, driver **616.56** |
| WSL GPU passthrough | `/dev/dxg` present; `nvidia-smi` works inside WSL |
| Disk | WSL root `~1007G` (~913G free); Windows `C:` ~733G (~168G free) |
| Compose project | `cu130-slim` — Docker Desktop 29.x, Linux engine |
| App image pin (`docker-compose.yml`) | `ghcr.io/themis128/cu130-slim:<service>-sha-32d08e2` |
| Local override | `docker-compose.override.yml` may retarget some services to `:latest` / Hub tags for local iteration |
| DMR | Host engine `docker-model-runner` on **`127.0.0.1:12435` only** (port **12434 is not used** / not listening). Compose services reach it via `host.docker.internal:12435`. Watchdog: `dmr-watchdog`. |
| GPU coexistence | ComfyUI (`:8000`), `local-diffusers` (internal `:7860`, no host publish), and DMR share the same 8 GB card. Prefer one heavy consumer at a time; DMR auto-unloads when idle. With ComfyUI resident, free VRAM can drop below 1 GB. |

**Agent guidance:** Prefer 4B Instruct / smollm3 for concurrent chatbot load; reserve `ai/qwen3:8b-q4_K_M` and `ai/qwen3-vl` (~5 GB each) for single-flight work. Never assume port 12434.

## Backend layout (`social-automation/backend/app/`)

```
main.py            FastAPI app, mounts api_router at /api/v1, init_db() on startup
api/               35 routers — auth (OAuth + data-deletion), accounts, publishing,
                   messenger, whatsapp, telegram, instagram, inbox, media, ai,
                   ai_providers, brand, billing, leads, secrets, teams, cf_db, mcp…
services/          platform clients + infra — facebook_api, instagram_api
                   (InstagramAPIClient + InstagramWebDMClient), threads_api,
                   tiktok_api, twitter_api, linkedin_api, messenger_api,
                   whatsapp_cloud_client, telegram_api, browser_bridge
                   (BrowserBridgeClient), browser_orchestrator, *_sidecar clients,
                   dmr (DMR client), inference (provider routing), media_*,
                   db_router (D1→Postgres dual-write), d1/kv/vectorize/chroma/minio
                   clients, content_*, brand_*, lead_capture, *_chatbot
models/            SQLAlchemy — social_account (access_token_enc, scopes, meta_data),
                   social_secret, user, content, media, lead, billing,
                   whatsapp_message (unified-inbox persistence)…
worker/celery_app.py   queues + beat_schedule
worker/tasks/      publishing, *_messenger pollers, session checks/refreshes,
                   analytics, digest, recurring, media, workflows, dmr_health,
                   datalake_export (→ cloudless.gr R2 datalake)
mcp/server.py      MCP server exposing social tools
```

## Session layers (three independent ones)

1. **OAuth tokens** — `social_accounts.access_token_enc` (encrypted), `scopes`, `meta_data`. Refreshed by `token_refresh` (hourly :15) + `instagram_token_refresh`/`linkedin_session_refresh` (weekly). X tokens live 2h (`expires_in:7200`); refresh tokens are single-use. `token_refresh._skip_for_recent_update` skips recently-touched accounts **only** when the token survives the next hourly run — a token expiring inside the window refreshes immediately (2026-09-23 fix: a 1.2s-valid token was skipped on an unrelated `updated_at` bump → ~1h of 401s).
2. **Shared bridge** `browser-novnc:9223` — one Chromium for all web-session work (IG DMs fallback, personal Messenger, Threads, X). Owner-hold: requests carry `X-Platform`; owner holds browser ~180s past last touch; `POST /session/start {platform, force:true}` claims it; `POST /session/cookies` injects; `POST /session/extract` persists storage state.
3. **Dedicated sidecars** — TikTok 9224, LinkedIn 9225, Facebook 9226, Messenger 9230. Each keeps its own Playwright profile + storage state under its data volume. `/login`, `/session/validate`, `/debug/all-cookies` (cookie export for transplants). TikTok sidecar `/session` fast-paths to `logged_in:false, reason:"no_session"` when no session cookies are injected — no 15s anonymous profile navigation on every health probe.

Session healing: `.devin/skills/session-transplant/` + `scripts/session_transplant.py` — export cookies from sidecar/MCP browser → inject into bridge → verify → persist.

## Messenger/DM data flow

| Platform | Read/send path |
|---|---|
| Instagram | Graph first: `InstagramAPIClient` on `graph.instagram.com` when `meta_data.login_type == "business_login"` (IGSID ≠ app-scoped `account_id` — resolve via `get_me().user_id`); fallback = bridge web session |
| FB Page Messenger | `MessengerAPIClient` Graph `/{page-id}/conversations` (needs `pages_messaging`) |
| FB personal | messenger-sidecar (9230) + bridge |
| LinkedIn | linkedin-sidecar (9225), polled 6h (rate limits) |
| Threads / X / TikTok | bridge (9223) sessions |

Webhook ingest: `/api/v1/messenger/webhook` + `/api/v1/whatsapp/webhook` (GET verify + POST events → chatbot auto-reply via DMR→CF AI→static chain).

## Unified inbox (`GET /api/v1/inbox/inbox`)

Aggregates one conversation list across FB Page Messenger, personal Messenger,
Instagram DMs, and WhatsApp. Implementation notes (added 2026-09):

- **Per-platform fetches are bounded** (`asyncio.wait_for`, 30s) and partial —
  one wedged platform can't stall the endpoint (was 2m33s before the cap).
- **60s in-process cache** — the frontend polls every 30s; cached responses
  serve in ~30ms instead of re-hitting external APIs.
- **Account ids are `str(account.id)`** — Pydantic `str` fields reject `UUID`,
  which silently dropped every IG/personal-Messenger row pre-fix.
- **IG sender = the other participant** — `participants[0]` is the business
  account itself; filter it out by id + username.
- **WhatsApp has no list-conversations API** — `whatsapp_messages` table
  persists every inbound webhook message + outbound send/template/auto-reply;
  the inbox aggregates one thread per peer from it. History starts at
  migration time (no backfill possible).
- Frontend type: `UnifiedConversation` in `frontend/src/services/api.ts`.

## Analytics read pattern — snapshot-first

`/analytics/overview` and `/analytics/followers` read the latest
`follower_snapshots` row per account (the analytics-sync beat writes them
~30min) — never a sequential live platform call per account (that made
`/overview` ~14s and `/followers` ~22s). Live `_follower_count` calls only
run for accounts with no snapshot at all, via `asyncio.gather` in parallel.

Snapshot `notes` carry sync outcomes; `analytics_sync` codes X free-tier
402/429 as `quota_exhausted`. `slack_digest` treats `quota_exhausted`,
`needs paid tier`, LinkedIn `activityids`, and `stats_unavailable` notes as
expected states — they are skipped, not surfaced as warnings.

## Fallback chains (see AGENTS.md for detail)

- DB: D1 → Postgres · Cache: KV → Redis · Vector: Vectorize → Chroma
- Storage: R2 → MinIO → disk · Text inference: DMR → Workers AI · Images: local Diffusers → Workers AI

## Beat schedule highlights

publish queue 30s · scheduled posts 60s · analytics sync 30min · token refresh hourly :15 · personal messenger 2min · threads/IG DMs 3min · twitter/tiktok DMs 5min · linkedin DMs 6h · IG session check 6h :30 · LinkedIn session check 12h :45 · WhatsApp verify 30min · DMR health 5min · datalake export 6h :10

## cloudless.gr datalake export (`datalake_export.export_datalake`)

Every 6h, snapshot-overwrites JSON tables in R2 `datalake-bucket` (`lake/socialauto-*`: accounts, posts, post-metrics history, followers, account-insight events, per-team insights-engine output, leads [sha256 email + domain only], 90d web events [UTM only, no IP/UA]). The site's `materialize-datalake-snapshots` ETL turns them into gold sections `socialauto_ops`, `social_engagement`, `social_outliers`, `social_recommendations`, `social_leads`, `social_attribution` for `/admin/analytics/datalake`. Uses `DATALAKE_R2_BUCKET` + the same `CLOUDFLARE_API_TOKEN` (verified cross-bucket write). Real-time leads still push via `CLOUDLESS_LEADS_WEBHOOK_URL` → EspoCRM.

## cloudless.gr → SocialAuto admin bridge (added 2026-09)

The site's `/admin/postiz` console is backed by this API (not the retired
Postiz instance): `src/lib/socialauto.ts` in cloudless.gr logs in with
`POST /api/v1/auth/login` (admin creds from the site's `SOCIALAUTO_*`
app_config), caches the JWT, and proxies channels/posts/media/analytics for
`/api/admin/postiz/*` routes. Cloudflare Access on `social.cloudless.gr`
accepts a **service token** (`any_valid_service_token` include on
`socialauto-app`) — the site sends `Cf-Access-Client-Id/Secret` when
`SOCIALAUTO_SERVICE_TOKEN` is configured. `/api/v1/` itself is NOT
Access-bypassed; auth stays JWT + Access.

## Meta app facts (app `1936126137016578`, business `1558125105019725`)

- Redirect URIs: `/api/v1/auth/oauth/{facebook,instagram,threads}/callback`; data-deletion/deauthorize → `/api/v1/auth/data-deletion`
- Two IG token flavors: **Instagram Business Login** (`instagram_business_*` scopes, host `graph.instagram.com`) vs **FB-Login-linked** (`instagram_*`, host `graph.facebook.com`). Tokens are NOT interchangeable — debug_token on the wrong host returns "Cannot parse access token".
- Business verification blocked by 2021 ad-account restriction → Advanced Access unavailable; own-account standard access covers current needs.

## Content strategy (Visibility Era — adopted 2026-09)

Sofia Kakkava's "Visibility Era" framework is encoded in the brand voice so every generated post follows it — no per-workflow prompt forks.

- **DAY 1 — Creator Type**: the owner is an **Expert-led blend** (Expert + Storyteller + Energizer). Stored in `brand_voices.voice_signature` keys `creator_type` / `post_formula` / `style_notes`. `POST /api/v1/ai/generate-content` injects `voice_signature` into the system prompt via `build_brand_system_prompt` (`app/services/brand_compliance.py`) — every n8n workflow + the UI generator picks it up from one place.
- **DAY 2 — 2-Platform Rule** (8-week commitment): `voice_signature.platform_focus` —
  - **MAIN = LinkedIn** — original content, carousels (Company Page `9c4451bb-…`), Educator posts.
  - **SECONDARY = Meta** — Instagram `38ddbd44-…`, Facebook Page, Threads `1071dcd5-…` — adapted/cross-posted versions.
  - **LAST = Twitter/X + TikTok** — opportunistic only (X free-tier quota ~1.5k posts/mo is chronically exhausted; don't schedule into it).
- **Tools/skills**: `.devin/skills/creator-type-voice/scripts/creator_type.py` (`show`/`quiz`/`apply`/`platforms`/`verify`); `profile-5sec-test` (DAY 4 audit); `publish-alert-triage` + `scripts/alert_triage.py` (classifies digest alerts → platform-limit/session/config/app-bug).

## n8n workflows (`n8n-workflows/`, 15 total)

All workflows authenticate to social-api via admin **TOTP login**; text generation uses **automatic model routing** (no hardcoded provider/model — `ai/smollm2` was removed, it returned empty content). Re-importing a workflow **deactivates it** in n8n 2.x — republish by ID, then restart n8n.

| Workflow | Trigger | Targets | Tier |
|---|---|---|---|
| `cloudless-carousel-pipeline` | every 2 days 19:00 EET + webhook `cloudless-carousel` | LinkedIn Company Page | main |
| `weekly-cloud-computing-post` | Mon 09:00 | LinkedIn (org account) | main |
| `marketing-image-generation` | every 24h + webhook `marketing-trigger` | Instagram `cloudless.gr` (CF image + caption → draft/post) | secondary — retargeted from Twitter 2026-09-21 |
| `socialauto-daily-slack-digest` | daily 09:00 | Slack digest | reporting |
| `{facebook,instagram,linkedin,threads,tiktok,twitter}-{text,image,carousel}-post` | webhook only | per-platform | on-demand |

**n8n MCP server**: `.devin/skills/n8n-cloudless/scripts/n8n-mcp-server.py` — 13 tools (`n8n_list_workflows`, `n8n_deploy_workflow`, `n8n_trigger_webhook`, `n8n_audit_workflows`, …). API key covers workflow/credential endpoints; `/executions` returns 403 so execution tools fall back to reading Postgres `execution_data` (rehydrates n8n 2.x deduplicated format). Registered in `.devin/mcp_config.json`.

## Agent skills (`.devin/skills/`, mirrored to `.cursor/skills/`)

Operable runbooks with scripts: `n8n-cloudless` (incl. MCP server), `creator-type-voice`, `publish-alert-triage`, `session-transplant`, `session-health-ops`, `profile-5sec-test`, `linkedin-sidecar-ops`, `tiktok-console-ops`, `messenger-management`, `instagram-dm`, `instagram-account-config`, `social-accounts-manager`, `socialauto-{accounts,brand,profile}`, `social-oauth-ops`, `meta-{oauth-setup,app-review}`, `twitter-oauth-setup`, `whatsapp-{platform,phone-verify}`, `browser-daemon-mode`, `novnc-login-helper`, `playwright-e2e`, `docker-model-runner`, `omv-ha-mail`, `cloudflare-access-paths`, `cloudflare-token-ops` (incl. `cloudflare` MCP server — token/service-token management via `scripts/cf_tokens.py`), `content-scoring`, `social-media-tools-research`, `emoji-generator`, `cloudless-carousel-pipeline`, `social-stack-ops`.
