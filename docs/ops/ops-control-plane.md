# Ops control plane — watchers & services registries

Added 2026-11-24 (PRs #2580, #2581, #2583, #2588, #2589, #2591).

Two sibling registries give the platform one source of truth for "what is
supposed to be running" across every tier — replacing scattered crontabs,
compose healthchecks, and per-host supervisor loops with DB rows the API,
agents, and the (future) Ops dashboard all share.

```
host watchers (WSL cron, heal_supervisor, Windows tasks)      services (containers, compose, n8n,
        │                                                     systemd units, Windows tasks, OMV)
        ▼                                                            ▼
 scripts/watcher_agent.py  ──sync/report──▶  ops_watchers     ops_services  ◀──sync/report──  scripts/service_agent.py
        │                                     (222 rows)        (210 rows)         │
        ▼                                            │                               │
 runs approved commands                        watcher_scheduler            pending_action queue
 (per-watcher timeout,                        (celery beat)                   claimed by agent
  max 2 parallel)                                    │                               │
        │                                  watchdog_gate (task_prerun)       docker-cli / systemctl /
        └──────────── status/result POST ──────────┴─────────────────────── windows-task / n8n-api
                                    ▼
                    /api/v1/ops/watchers/*   /api/v1/ops/services/*
                    (toggle, run, action, dashboards — X-Service-Token)
```

## `ops_watchers` — scheduled-job registry

One row per watcher regardless of where it executes:

- **`executor="celery"`** — a Celery beat task; `enabled=False` is a live
  kill-switch via `app/worker/watchdog_gate.py` (`task_prerun` skips the
  task — no beat restart needed).
- **`executor="host-agent"`** — a shell command the host's
  `scripts/watcher_agent.py` runs (docker.exe, powershell.exe, adb, cron
  scripts) under a per-watcher timeout, max 2 parallel, executing only
  registry-approved commands.
- **`executor="external"`** — owned outside the platform (OMV cron, n8n
  schedule, Windows Task Scheduler); agent reports freshness only.

**`managed_by`** is the migration state: `external` = observed only
(nothing double-executes — legacy cron/supervisor loops stay untouched),
`platform` = due-scheduled by `watcher_scheduler` and executed by the
matching executor. `enabled` pauses platform rows; for external rows it
is advisory-only (the platform can't stop an OMV cron — shown as such).

Tiers: `platform` (celery beats), `host` (WSL crontab/heal_supervisor),
`windows`, `docker`, `infra`, `external`. Kinds: `watch`, `heal`, `sync`,
`report`, `janitor`.

Endpoints: `GET /api/v1/ops/watchers` (dashboard), `/{name}/toggle`,
`/{name}/run` (ad-hoc), `/{name}/result` (agent posts back),
`/sync` (inventory seed), `/due` (agent claims due rows).

## `ops_services` — service registry

Stateful things needing status + on-demand actions rather than cadence:

- **`executor="docker-cli"`** — agent runs docker/docker.exe
- **`executor="systemctl"`** — `systemctl --user` units
- **`executor="windows-task"`** — powershell scheduled-task cmdlets
- **`executor="n8n-api"`** — observed via n8n REST
- **`executor="external"`** — observed only (OMV, k3s)

**`meta.allowed_actions`** gates what the agent may do (default
`["status"]`); `restart`/`start`/`stop` are honored only when listed.
Critical infra — postgres, redis, n8n + task-runner, cloudflared,
browser bridges, comfyui, social-api — is seeded with
`["status","logs"]`: the platform sees everything but can't bounce it
without an explicit policy change. Enforcement is server-side on the
`/action` endpoint, so a compromised agent can't exceed policy.

Actions flow as a queue: API sets `pending_action` → agent claims →
posts result (output tail + failure counter) → row updates.

Endpoints: `GET /api/v1/ops/services`, `/{name}/action`,
`/{name}/action-result`, `/report`, `/sync`, `/actions` (recent queue).

## Agents

- `scripts/watcher_agent.py` / `scripts/service_agent.py` — host-side
  satellites; authenticate with `OPS_WATCHER_AGENT_TOKEN` (self-loaded
  from repo `.env`), poll for due runs / pending actions, post results.
- `scripts/watcher_inventory.py` / `scripts/service_inventory.py` —
  scanners that seed the registries from crontab, heal_supervisor,
  docker ps, systemctl, Windows tasks, n8n workflows.
- `ops_ctl.py` (`.devin/skills/ops-control-plane-ops/scripts/`) — the
  operator CLI: status dashboards, toggles, runs, service actions.

## Migration posture

Phase 1 (now): everything seeded as `managed_by=external` except the
celery beats — the platform observes all 432 entities, controls the ones
it already owned, and nothing double-executes. Phase 2: flip low-risk
host watchers to `managed_by=platform`, run `watcher_agent.py --once`,
verify, retire the cron line. Phase 3: persistent agent service +
`/ops` dashboard UI.
