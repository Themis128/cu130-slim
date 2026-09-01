---
name: social-stack-ops
description: >-
  Operates the cu130-slim Docker Compose stack (ComfyUI, n8n, social-api,
  social-worker-publishing/media/default, celery-beat, Redis, Postgres, Ollama,
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
| ollama | 11435 |
| social-postgres | 5433 |
| metabase | 3000 |

## Critical containers

- `social-api` — FastAPI, `--reload` on `./social-automation/backend/app`
- `social-worker-publishing` — Celery, `publishing` queue; **restart after publishing.py / worker task / celery_app.py changes**
- `social-worker-media` — Celery, `media` queue; **restart after media task / celery_app.py changes**
- `social-worker-default` — Celery, `default` + `celery` queues; **restart after analytics/workflow/digest task / celery_app.py changes**
- `celery-beat` — single scheduler instance; **restart after beat_schedule or queue routing changes**
- `ollama` — GPU-only LLM (`llama3.1:8b-gpu`, 100% GPU, 2048 ctx)
- `comfyui` — GPU image generation (`--gpu-only --force-fp16 --reserve-vram 1`)
- `n8n` + `n8n-sandbox`
- `redis`, `social-postgres`

## Tool script

```bash
.devin/skills/social-stack-ops/scripts/stack-status.sh
```

## Common ops

```bash
docker compose ps
docker compose restart social-worker-publishing social-worker-media social-worker-default celery-beat
docker compose logs -f social-api social-worker-publishing social-worker-media social-worker-default n8n --tail=100
docker compose up -d n8n social-api social-worker-publishing social-worker-media social-worker-default celery-beat

# check all 3 Celery worker nodes
docker compose exec -T social-worker-publishing celery -A app.worker.celery_app inspect ping

# check Ollama GPU offload
docker compose exec -T ollama ollama ps

# check GPU VRAM
nvidia-smi --query-gpu=memory.used,memory.free,memory.total --format=csv
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
