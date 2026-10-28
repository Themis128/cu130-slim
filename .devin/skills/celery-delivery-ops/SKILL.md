---
name: celery-delivery-ops
description: >
  End-to-end Celery delivery verification for the SocialAuto stack — probe a
  task through the full path (send_task → queue → worker pickup → result),
  detect the 2026-10-27 "task id but never consumed" anomaly, bypass stuck
  sends via the service layer directly, and check .env-vs-container
  credential drift and Docker Desktop bind-mount staleness. Use when a
  Celery task/beat job/campaign send queues but never runs, after .env
  rotation, or when container code looks older than the checkout.
  Tool: scripts/celery_probe.py.
---

# Celery Delivery Ops — e2e task pipeline health

Origin: 2026-10-27 incident — `send_campaign` enqueued via
`celery_app.send_task` returned a valid id but never reached workers
(`reserved`/`scheduled` empty, `AsyncResult` PENDING forever) while other
tasks on the same queue flowed. Unresolved root cause — this tool exists to
prove delivery, isolate the hop that drops the message, and apply the
workaround.

## Tool — `scripts/celery_probe.py`

Run **inside** `social-api` or any worker container (`docker compose exec -T
social-api python /fix/celery_probe.py ...` — copy it in first, or run from
host with `SOCIAL_API_URL`):

```bash
# 1. e2e probe: send a real task, watch it get picked up (or not)
python3 celery_probe.py probe app.worker.tasks.email_campaigns.send_campaign \
    --args '["<campaign_id>"]'

# 2. queue topology: who consumes what + llen per queue
python3 celery_probe.py topology

# 3. drift: .env vs running-container env (REDIS_URL etc.)
python3 celery_probe.py drift

# 4. mount staleness: hash a file on host vs inside the container
python3 celery_probe.py mount-check social-automation/backend/app/api/__init__.py
```

Exit code 1 on any failure — usable in CI/cron.

## What the anomaly looked like (signature to match)

- `send_task()`/`apply_async()` returns a task id — producer side fine.
- `celery -A app.worker.celery_app inspect {reserved,scheduled,active}`
  → empty for that task; other tasks on the same queue flow normally.
- `AsyncResult(id)` stays `PENDING` (worker never wrote the result key).
- Worker logs show `Task <name> [id] received` for other tasks — the
  pipeline works; only that task's messages vanish between publish and BRPOP.
- Next debugging step (per backlog note): delivery-tag / routing
  instrumentation — `redis-cli MONITOR` during a send shows the LPUSH target;
  compare against `inspect active_queues`.

## The bypass (proven)

```bash
docker compose exec -T social-api python -c "
import asyncio, uuid
from app.services.email_campaigns import send_campaign
print(asyncio.run(send_campaign(uuid.UUID('<campaign_id>'))))"
```

Runs the real send synchronously — returned `{'sent': 1, 'failed': 0}` live.

## Drift + staleness rules

- **`.env` rotated ≠ running containers updated** — containers keep the env
  they were started with. `probe drift` diffs `.env` vs `docker exec …
  printenv`. Restart is required for new values (and a password flip can
  split producers/consumers onto different brokers — prime suspect for
  non-delivery).
- **Docker Desktop bind-mount staleness** — the container can serve an
  older file version than `/mnt/c` shows. Verify with
  `probe mount-check`; fix by pushing the file through the mount
  (`docker compose cp file svc:/path`) then restart.
