# SocialAuto (social.cloudless.gr) — Codemap

Architecture map for the `cu130-slim` repo. Companion to `AGENTS.md` (rules) — this file is the "where things live and how they connect" reference. Last verified: 2026-09-20.

## What it is

SocialAuto: multi-platform social media automation — compose/publish posts, unified DM inbox with AI auto-reply, brand system, media generation, analytics. FastAPI backend + Next.js frontend + 4 queue-dedicated Celery workers + per-platform browser sidecars, all on one Docker Compose stack on the WSL workstation.

## Request path

```
browser → social.cloudless.gr → Cloudflare Tunnel (social-cloudflared)
        → social-frontend:3000 (Next.js UI)
        → /api/* → social-api:8000 (FastAPI, host port 8083)
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
| social-frontend | 8082→3000 | Next.js UI |
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
| comfyui / local-diffusers | 8000/8010, 8081 | image gen |
| chroma / minio / languagetool | 8001, 9000-9001, 8080 | vector failover / S3 failover / spellcheck |
| postgres (metabase) / metabase | 5432, 3000 | analytics warehouse — separate from social-postgres |
| flower | 5555 | Celery UI |
| social-metrics | 9100 | Prometheus exporter |
| dmr-watchdog | — | restarts docker-model-runner when wedged |

Not in Compose: **DMR** (Docker Model Runner, host engine on `localhost:12435` / `host.docker.internal:12435`).

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
                   social_secret, user, content, media, lead, billing…
worker/celery_app.py   queues + beat_schedule
worker/tasks/      publishing, *_messenger pollers, session checks/refreshes,
                   analytics, digest, recurring, media, workflows, dmr_health
mcp/server.py      MCP server exposing social tools
```

## Session layers (three independent ones)

1. **OAuth tokens** — `social_accounts.access_token_enc` (encrypted), `scopes`, `meta_data`. Refreshed by `token_refresh` (hourly :15) + `instagram_token_refresh`/`linkedin_session_refresh` (weekly).
2. **Shared bridge** `browser-novnc:9223` — one Chromium for all web-session work (IG DMs fallback, personal Messenger, Threads, X). Owner-hold: requests carry `X-Platform`; owner holds browser ~180s past last touch; `POST /session/start {platform, force:true}` claims it; `POST /session/cookies` injects; `POST /session/extract` persists storage state.
3. **Dedicated sidecars** — TikTok 9224, LinkedIn 9225, Facebook 9226, Messenger 9230. Each keeps its own Playwright profile + storage state under its data volume. `/login`, `/session/validate`, `/debug/all-cookies` (cookie export for transplants).

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

## Fallback chains (see AGENTS.md for detail)

- DB: D1 → Postgres · Cache: KV → Redis · Vector: Vectorize → Chroma
- Storage: R2 → MinIO → disk · Text inference: DMR → Workers AI · Images: local Diffusers → Workers AI

## Beat schedule highlights

publish queue 30s · scheduled posts 60s · analytics sync 30min · token refresh hourly :15 · personal messenger 2min · threads/IG DMs 3min · twitter/tiktok DMs 5min · linkedin DMs 6h · IG session check 6h :30 · LinkedIn session check 12h :45 · WhatsApp verify 30min · DMR health 5min

## Meta app facts (app `1936126137016578`, business `1558125105019725`)

- Redirect URIs: `/api/v1/auth/oauth/{facebook,instagram,threads}/callback`; data-deletion/deauthorize → `/api/v1/auth/data-deletion`
- Two IG token flavors: **Instagram Business Login** (`instagram_business_*` scopes, host `graph.instagram.com`) vs **FB-Login-linked** (`instagram_*`, host `graph.facebook.com`). Tokens are NOT interchangeable — debug_token on the wrong host returns "Cannot parse access token".
- Business verification blocked by 2021 ad-account restriction → Advanced Access unavailable; own-account standard access covers current needs.
