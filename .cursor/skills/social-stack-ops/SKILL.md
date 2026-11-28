---
name: social-stack-ops
description: >-
  Operates the cu130-slim Docker Compose stack (ComfyUI, n8n, social-api,
  social-worker-publishing/media/default/messenger, celery-beat, Redis, Postgres,
  Chroma, Portainer). Use when checking container health, restarting workers
  after publishing fixes, DNS issues, ports, or day-to-day ops for the Cloudless
  social automation stack.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# Social Stack Ops

## Ports (host)

| Service | Port |
|---------|------|
| social-frontend | 8082 |
| social-api | 8083 |
| n8n | 5678 |
| env-manager | 8080 |
| ComfyUI | 8000 |
| social-postgres | 5433 |
| metabase | 3000 |

## Critical containers

- `social-api` — FastAPI, `--reload` on `./social-automation/backend/app`
- `social-worker-publishing` — Celery, `publishing` queue; **restart after publishing.py / worker task / celery_app.py changes**
- `social-worker-media` — Celery, `media` queue; **restart after media task / celery_app.py changes**
- `social-worker-default` — Celery, `default` + `celery` queues; **restart after analytics/workflow/digest task / celery_app.py changes**
- `social-worker-reports` — Celery, `reports` queue (notebook reports, digests, funnel, promo, linkedin-ads email tasks); **restart after report task / celery_app.py changes**
- `celery-beat` — single scheduler instance; **restart after beat_schedule or queue routing changes**
- `social-worker-messenger` — Celery, `messenger` queue; **restart after messenger task / celery_app.py changes**
- `comfyui` — GPU image+video generation (`COMFYUI_PROFILE=flux` → lowvram + fp8 text-enc). Serves FLUX.1-schnell GGUF (images), Wan2.2 TI2V-5B GGUF (video, t2v+i2v — only video model). Custom-node deps (gguf, opencv, colour-science…) are pinned in the Dockerfile — nodes are bind-mounted so their own requirements.txt never install.
- `local-diffusers` — retired (SD 1.5). Stopped, `LOAD_ON_STARTUP=false`.
- `n8n` + `n8n-sandbox`
- `redis`, `social-postgres`

## Tool script

```bash
.devin/skills/social-stack-ops/scripts/stack-status.py
```

## MCP coverage (registered in `.devin/mcp_config.json` + `.cursor/mcp.json`)

| Server | Transport | Surface |
|--------|-----------|---------|
| `platform` | stdio — `scripts/platform-mcp-server.py` | read-only infra: `stack_ps`, `service_health` (14 endpoints), `pg_query` (SELECT-only on social-postgres 5433: `social_automation`/`n8n` DBs, 15s timeout, LIMIT 500), `redis_inspect` (info/get/ttl/scan/slowlog), `celery_status` (Flower 5555, auth from `ENV_MANAGER_USER`/`ENV_MANAGER_PASS`), `chroma_stats` (8001), `comfyui_status` (8000 — VRAM under-reports on WSL2), `metrics_digest` (9390) |
| `linkedin` | `url: http://127.0.0.1:9227/mcp` (streamable-http) | `linkedin-mcp-server` container — 19 LinkedIn read tools (profiles, company posts, search) sharing the sidecar cookie session |
| `airbyte` | `url: http://127.0.0.1:9228/mcp` (streamable-http) | `airbyte-mcp-server` container — connector catalog (`list_connectors`, `list_env_vars`); creds in env |

HTTP MCPs use native `url` config (Devin CLI + Cursor support Streamable HTTP directly — no mcp-remote proxy needed for unauthenticated local servers). The `platform` server is read-only by construction; credentials are read from `.env` and never returned.

## Common ops

```bash
# docker cp does NOT reload modules in a running Celery worker — always
# restart the affected workers + beat after hot-syncing worker/task code:
docker compose restart social-worker-publishing social-worker-media social-worker-default celery-beat

# Worker async model (2026-10-09): each prefork child runs ONE asyncio loop
# on a daemon thread (started by worker_process_init in celery_app.py).
# Tasks submit coroutines via app.worker._async.run_async and get DB
# sessions from task_session() — a real pooled engine bound to that loop.
# Never call asyncio.run() + async_session_maker() together in a task —
# that is the 'Future attached to a different loop' crash.
```

```bash
docker compose ps
docker compose restart social-worker-publishing social-worker-media social-worker-default celery-beat

# NEVER `compose up -d <svc>` without --no-deps for a single-service image
# swap: the dependency chain recreates n8n + social-api too → public 502s on
# n8n.cloudless.gr during the image pull (learned 2026-10-16). Correct:
docker compose up -d --no-deps --force-recreate social-frontend
docker compose logs -f social-api social-worker-publishing social-worker-media social-worker-default n8n --tail=100
docker compose up -d n8n social-api social-worker-publishing social-worker-media social-worker-default celery-beat

# check all 4 Celery worker nodes
docker compose exec -T social-worker-publishing celery -A app.worker.celery_app inspect ping

# check GPU VRAM
nvidia-smi --query-gpu=memory.used,memory.free,memory.total --format=csv

# GPU arbitration (one-model-in-VRAM, 8GB card):
# media tasks hold Redis lock gpu:media_lock + flag gpu:media_busy
# (app/services/gpu_arbiter.py). DMR calls wait on the flag; on insufficient
# VRAM or a timeout/disconnect, dmr.py frees ComfyUI cache + evicts DMR
# models, then retries once. Host-side equivalent:
python3 scripts/gpu_serial.py status   # resident DMR models
python3 scripts/gpu_serial.py unload   # evict all (HTTP, not docker CLI)

# DMR wedge recovery: /engines/v1/models answers 200 even when the llama.cpp
# scheduler is deadlocked — probe /api/ps instead (hangs when wedged).
# The dmr-watchdog restarts docker-model-runner after 3 failed deep probes.
# Manual: docker restart docker-model-runner

# All DMR requests take dmr_slot() (Redis FIFO lock gpu:dmr_lock) — serializes
# DMR cluster-wide; concurrent loads of different models wedge the scheduler.
# Eviction uses native POST /engines/unload — never /api/chat keep_alive:0
# (that queues a load to serve the unload).

# Cloudflare tunnel: social-cloudless serves social.cloudless.gr ONLY;
# omv hostnames run on the separate omv-cloudless tunnel on omv.
# Never join a replica to a tunnel that can't resolve every origin in its
# ingress — shared config → that replica 502s its traffic share.
# Replica count: GET /accounts/$CLOUDFLARE_ACCOUNT_ID/cfd_tunnel/<id>/connections

# Prometheus shows up{job="socialauto"}=0 but container is "healthy"?
# Docker Desktop restart breaks the Windows port-proxy for 192.168.1.23:9390
# (TCP accepts, HTTP resets — Pi-side curl gets 000). Fix:
docker restart social-metrics
# verify from omv: curl -m 10 http://192.168.1.23:9390/metrics -> 200
```

## Related skills

- `cloudless-carousel-pipeline` — CF carousel + LinkedIn org
- `n8n-cloudless` — automate via n8n webhook/schedule (includes daily Slack digest → `#socialauto`)
- `monitoring-ops` — Prometheus metrics, Grafana dashboards, k3s EspoCRM exporter

## Slack daily digest (#socialauto)

- Channel: `#socialauto` (`C0BT263L17U`)
- Slack: `SLACK_BOT_TOKEN` must be `xoxb-…` (not Slack CLI `xoxe-…`)
- Email: reports + warnings/errors to `DIGEST_EMAIL_TO`=`tbaltzakis@cloudless.gr`
  (dedicated mail client → omv-ha dovecot).
  - **Free path:** `EMAIL_PROVIDER=smtp` → `smtp.resend.com:587` (same Resend
    relay as omv-ha). Inbound: CF Email Routing → `mail-ingest` → Maildir.
  - Do **not** use paid Cloudflare Email Sending for this.
- Recreate `social-api` / `social-worker-*` after changing email/Slack env.
- Manual: login as admin → `POST /api/v1/ops/daily-digest?post_to_slack=true&post_to_email=true`

## Idle-sleep / wake-on-connect (stack-ops)

The `stack-ops` container (built from `stack-ops/`) fronts every
idle-tolerant service with a TCP proxy that wakes it on connect and stops
it after an idle timeout. **A container showing `Exited` may be asleep by
design — check `curl http://127.0.0.1:8787/status` before calling it down.**

- Wake: `POST http://127.0.0.1:8787/wake/<name>` (or `python3 scripts/stackctl.py wake <name>`)
- Sleep now: `POST /sleep/<name>` · All: `POST /sleep-all` · Status: `GET /status`
  · Keep awake: `POST /keepawake/<name>?ttl=<s>`
- Managed set + timeouts live in `stack-ops/services.json` (mounted — edit +
  `docker compose up -d --force-recreate stack-ops`) — sidecars 20min, UIs 45-90min.
- Backends reach managed services via `http://stack-ops:<orig-port>` (env URLs in compose).
- Host ports for managed services publish on stack-ops, so `localhost:PORT` tools work unchanged.
- Probing a managed port wakes it — use `/status` for health checks instead of hitting `/health`.
  `/status` is keyed by *service* name; each entry also carries `container` for docker-name lookups.
- `wait_page` services (comfyui, metabase, jupyter, flower, portainer, env-manager-ui,
  noVNC) serve an auto-reloading 503 splash only to *browser* GETs (Accept: text/html);
  API calls block through the wake and get the real response (~5-30s cold start).
- No `depends_on` on managed services — it force-starts them on every `compose up`.
- Open-but-idle proxy connections don't pin a service awake (4 extra low-CPU polls).
- Tunables via `.env`: STACK_OPS_POLL_SECONDS, _CPU_MAX, _WAKE_TIMEOUT, _CONN_STREAK, _SLEEPER.
- Never-sleep core: social-api, social-frontend, api-gateway, workers, celery-beat,
  redis, postgres ×2, minio, chroma, n8n, cloudflared, social-metrics, dmr-watchdog, stack-ops.
- ComfyUI IS sleep-managed now (30min idle, `localhost:8000` wait-page for browsers);
  `restart: no` still applies — compose up doesn't force it, proxy wakes it.
- **Long GPU jobs need `keepawake`** — the sleeper only sees proxied conns + CPU%;
  a GPU-bound render can dip below CPU_PCT_MAX and get `docker stop`ped mid-job.
  `comfyui_video.generate_video` / `comfyui_image.generate_image` already call
  `stack_ops.keepawake("comfyui", ttl)`; do the same for any new long ComfyUI path.
- `/status` uses ONE batched `docker inspect` (was N subprocesses ≈ 40s → 0.4s).
  If it ever regresses past ~5s, ops-console probes fall back to proxied /health
  → sleeping services show "offline (ReadTimeout)". Running-but-slow containers
  report `busy (ReadTimeout)` online, not offline.

## Env-drift check (learned 2026-10-25)

**Undeclared env vars vanish on `compose up` recreate** — `docker restart`
preserves old container env; only recreate injects compose env. Vars present
in the container but absent from the service's `environment:` block silently
disappear (NEXTCLOUD_* IntraVox auth broke exactly this way).

Run `python -X utf8 .devin/skills/social-stack-ops/scripts/env_drift_check.py`
— flags (a) live-container keys not declared in compose, (b) declared `${VAR}`
mappings with empty `.env` values. Currently real drift on: workers +
social-api + celery-beat (`DMR_MEDIA_MODEL`, `ESPOCRM_API_KEY/BASE_URL`,
`SLACK_PUBLISHING_CHANNEL_ID`, `SLACK_TELEGRAM_CHANNEL_ID`,
`SLACK_TELEGRAM_WEBHOOK_URL`, `TELEGRAM_NOTIFY_EMAIL`,
`INTRANET_FEED_TEAM_ID/TOKEN`, `NEWSLETTER_SERVICE_TOKEN`) — same class as
the NEXTCLOUD incident; declare them in compose or a shared anchor before
the next `compose up`.

## Healthcheck restart-loop audit

`python3 scripts/healthcheck_loop_audit.py` — flags autoheal-managed
services whose healthcheck grace (start_period + interval×retries) is
shorter than realistic cold-boot time. The 2026-11-13 celery-worker
incident: start_period=30s vs ~120s boot → autoheal killed workers
right as they became ready → publish tasks died mid-flight. Fix pattern
= raise start_period past boot time (PR #1830). celery-beat still
flags RISK (grace=105s) — same fix applies if it flap-loops.

## Publish queue shepherd (automated)

`scripts/publish_queue_shepherd.py` lives at
`social-automation/backend/scripts/` (bind-mounted `/app/scripts` in
workers). compose-watchdog runs it every 30min: resets stale
processing locks, dispatches process_publish_queue, alerts on targets
pending >60min.

## Deploy pipeline & image drift (the ONE compose that matters)

The running fleet is compose project **`socialauto-standalone`** — file
`deploy/socialauto-standalone/docker-compose.yml`, env from that dir's
`.env`. The ROOT `docker-compose.yml` is a superset that re-declares the
same `container_name`s — **`compose up` from root adopts live containers
under the wrong project + wrong env** (wiped INSTAGRAM_BROWSER_CDP_URL /
INSTAGRAM2_* / rotated IG password on 2026-11-22). Always
`cd deploy/socialauto-standalone` for compose ops.

Chain: PR merge → CI `Docker CI` build-and-push → `:latest` tag moves →
`stack-image-sync` cron (*/30, `home-ops/scripts/stack-image-sync.py`,
symlinked at `~/scripts`) digests-compares, pulls serially, recreates
drifted services via the standalone compose → containers run new code.
Merge storms cancel queued builds (concurrency group) — the first build
that escapes the queue wins; lag is normal during storms.

Verify real state, never infer:
```bash
python3 .devin/skills/social-stack-ops/scripts/deploy_lag.py
# CURRENT / PULL-OK-RECREATE-PENDING / LAGGING per container

# prove a code feature exists in the RUNNING image (not a docker-cp
# patch that the next recreate wipes — that's not durable):
python3 .devin/skills/social-stack-ops/scripts/deploy_lag.py \
  --probe social-worker-meta app/services/publishing.py 'create_video_container'
```

Env parity between the two .env files (root vs standalone) drifts
silently — rotation lanes write root `.env` only:
```bash
python3 .devin/skills/social-stack-ops/scripts/env_parity_check.py
# DIVERGED keys + ROOT-ONLY keys the standalone stack consumes
# (values never printed)
```

Rules:
- `docker restart` reloads code only if bind-mounted; `env_file` changes
  need `compose up -d` (recreate), never restart.
- `docker cp` into containers is transient — the next recreate drops it.
  Durable = merge to master → CI image → sync pull.
- `docker pull`s are SERIALIZED (parallel pulls crashed Docker Desktop
  twice — wsl-docker-doctor); comfyui pull can block ~15min.
- Hash-prefixed `<sha>_svc` `Created` containers are recreate-race
  orphans — remove them when the real service is Up; never remove an
  `Up` one (38209cd8f1f6_tiktok-browser-sidecar is the LIVE sidecar).
