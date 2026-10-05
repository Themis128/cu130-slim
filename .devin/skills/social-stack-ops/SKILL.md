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
- `celery-beat` — single scheduler instance; **restart after beat_schedule or queue routing changes**
- `social-worker-messenger` — Celery, `messenger` queue; **restart after messenger task / celery_app.py changes**
- `comfyui` — GPU image+video generation (`COMFYUI_PROFILE=flux` → lowvram + fp8 text-enc). Serves FLUX.1-schnell GGUF (images), LTX-Video Q8 + Wan2.1 1.3B (video, t2v+i2v). Custom-node deps (gguf, opencv, colour-science…) are pinned in the Dockerfile — nodes are bind-mounted so their own requirements.txt never install.
- `local-diffusers` — retired (SD 1.5). Stopped, `LOAD_ON_STARTUP=false`.
- `n8n` + `n8n-sandbox`
- `redis`, `social-postgres`

## Tool script

```bash
.devin/skills/social-stack-ops/scripts/stack-status.py
```

## Common ops

```bash
docker compose ps
docker compose restart social-worker-publishing social-worker-media social-worker-default celery-beat
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

# Prometheus shows up{job="socialauto"}=0 but container is "healthy"?
# Docker Desktop restart breaks the Windows port-proxy for 192.168.1.23:9390
# (TCP accepts, HTTP resets — Pi-side curl gets 000). Fix:
docker restart social-metrics
# verify from omv: curl -m 10 http://192.168.1.23:9390/metrics -> 200
```

## Related skills

- `cloudless-carousel-pipeline` — CF carousel + LinkedIn org
- `n8n-cloudless` — automate via n8n webhook/schedule (includes daily Slack digest → `#socialauto`)

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
