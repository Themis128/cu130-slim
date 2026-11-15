---
name: stack-rescue-ops
description: >-
  Keep SocialAuto usable while the Docker Desktop WSL fsbridge is poisoned:
  run API/workers from the baked backend image (no bind mounts), publish
  through the normal platform path, then hand back to the managed stack.
  Use when containers exit 127/255 in waves, `docker start` fails with
  "not a directory" on docker-desktop-bind-mounts, or `\\wsl.localhost`
  reads empty. Diagnosis itself lives in wsl-fsbridge-ops.
---

# Stack rescue ops — running mounted-free while the fsbridge is poisoned

## When

`wsl-fsbridge-ops` diagnoses the failure (dd-user-distro-proxy died → every
`\\wsl.localhost`-sourced bind mount materializes EMPTY). This skill is the
interim workaround for **whole-app operation**, complementing the skill's
named-volume transplant (which only covers single-file mounts).

Signals you are in this state:

- `docker start <svc>` fails with
  `error mounting "/run/desktop/mnt/host/wsl/docker-desktop-bind-mounts/...": not a directory`
- `docker run --rm -v /tmp/probe:/p alpine cat /p` → `No such file or directory`
- App containers (`social-api`, workers, `celery-beat`, `compose-healer`)
  exit **127** in waves; postgres/redis/minio/frontend stay healthy
  (named volumes + `C:\` mounts are unaffected).
- Fresh `wsl.exe` exec may also flap with `WSAETIMEDOUT` — retry, don't
  conclude failure.

## Why this works

The published backend images (`ghcr.io/themis128/cu130-slim:social-backend-api-latest`,
`…:social-backend-latest`) carry a full code copy at `/app` — the compose
bind mounts only exist for dev hot-reload. A container launched with **no
bind mounts** runs the image's baked code and never touches the fsbridge.

## Procedure

1. **Take the deploy lock** — `echo "$(date -u +%FT%TZ) <agent> stack-rescue" > ~/cu130-slim/.deploy.lock`.
   The watchdog honors it; compose-healer does **not** (it will keep
   converging dead managed services — harmless but noisy; see BACKLOG).
2. **Pause the restart-looping managed containers** you are replacing —
   `docker pause social-api social-worker-publishing` frees the port and
   stops the churn. (Or leave them dead — but the port must be free.)
3. **Launch rescue containers from the baked image**, same compose
   networks, no volumes:

   ```bash
   # env: reuse the live container's env minus secrets it got from /app/.env
   docker inspect social-api --format '{{range .Config.Env}}{{println .}}{{end}}' > /tmp/api.env
   docker create --name social-api-rescue --network cu130-slim_backend \
     -p 127.0.0.1:8083:8000 --env-file /tmp/api.env \
     ghcr.io/themis128/cu130-slim:social-backend-api-latest
   # .env is not in the env list — copy it without printing:
   docker cp social-api:/app/.env /tmp/api.envfile   # if social-api is merely exited
   docker cp /tmp/api.envfile social-api-rescue:/app/.env
   docker start social-api-rescue
   ```

   Worker identical, image `social-backend-latest`, container command
   `celery -A app.worker.celery_app worker -Q publishing --loglevel=info
   --hostname=rescue-publishing@%h`, attached to `cu130-slim_backend` AND
   `cu130-slim_db` (Redis lives on db).
4. **Operate normally** — the rescue API serves all endpoints; the rescue
   worker consumes the real `publishing` queue alongside nothing else
   (managed workers are paused/dead), so queued work is not double-run.
5. **Hand back**: when the fsbridge heals (probe succeeds), `docker unpause`
   or `docker start` the managed services, then `docker rm` the rescue
   containers and release the lock.

## Gotchas proven 2026-11-15

- `docker create` then `docker cp` BEFORE `docker start` — the .env must
  land before boot (JWT_SECRET_KEY etc. live there, not in env-file).
- Attach BOTH networks to workers; Redis is only on `db`.
- Rescue containers die with the same exit-137/255 wave when the whole
  engine bounces — they survive ordinary healer churn but not a
  Docker Desktop/VM restart. Their /tmp contents persist across
  `docker stop`/`start` — resumable, just re-`docker start`.
- Check for pending `publish_queue` rows before starting a rescue worker —
  it will consume whatever is queued, not just your task.
- The managed stack prefers port 8083 — rescue and managed API cannot both
  bind it; pause the managed one first.

## Full heal (when allowed)

`wsl --shutdown` is the only durable fix for the poisoned bridge — the
owner may forbid it during active operations; report the need instead of
running it silently.
