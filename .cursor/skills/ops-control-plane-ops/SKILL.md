---
name: ops-control-plane-ops
description: >
  Operate the SocialAuto ops control plane — the ops_watchers registry
  (every watchdog/healer/cron across celery, host cron, heal_supervisor,
  compose, Windows, OMV/n8n) and the ops_services registry (docker,
  systemd, windows-task, n8n services with allowlisted actions). Covers
  status dashboards, watcher toggle/run, service action queueing,
  agent-token endpoints, and the watchdog_gate that skips disabled
  celery watchers. Use when asking "is watcher X running", "disable
  healer Y", "restart service Z safely", "why is task T not firing",
  or triaging stale/error watcher statuses. Tool:
  .devin/skills/ops-control-plane-ops/scripts/ops_ctl.py.
---

# Ops control plane — watchers & services registries

Two Postgres-backed registries are the single control plane for every
scheduled watcher and stateful service in the stack (merged in #2580,
agents in `scripts/watcher_agent.py` / `service_agent.py`, cron-wired).

- **`ops_watchers`** — one row per watchdog/healer: tier
  (platform/host/windows/docker/infra/external), kind
  (watch/heal/sync/report/janitor), executor (celery/host-agent/external),
  managed_by (platform/external), cadence, status projection
  (ok/stale/error/disabled/running/unknown), failure counters.
- **`ops_services`** — one row per service: tier
  (docker/systemd/windows/n8n/infra/external), executor, target,
  health, `meta.allowed_actions` policy, `pending_action` queue.

## CLI

```bash
cd ~/cu130-slim

# dashboard — counts by executor/tier/status, pending actions
python3 .devin/skills/ops-control-plane-ops/scripts/ops_ctl.py status
python3 .devin/skills/ops-control-plane-ops/scripts/ops_ctl.py status --json

# watchers (admin JWT)
ops_ctl.py watchers list --tier host --executor host-agent
ops_ctl.py watchers list --enabled off          # everything disabled
ops_ctl.py watchers show cron-botfleet-py
ops_ctl.py watchers toggle <name> off|on        # disable a healer safely
ops_ctl.py watchers run <name>                  # celery→send_task, host-agent→next_due_at=now

# services (admin JWT)
ops_ctl.py services list --tier docker --status unhealthy
ops_ctl.py services show docker-social-api
ops_ctl.py services action docker-social-api restart --reason "wedged worker"
# actions allowed: restart|start|stop|logs|status — gated by meta.allowed_actions
# (default policy is status-only; CRITICAL infra is observe-only, never restartable)

# agent lane (X-Service-Token = OPS_WATCHER_AGENT_TOKEN) — what the satellites call
ops_ctl.py watchers due                          # due host-agent work + observe-only set
ops_ctl.py watchers result <name> --exit-code 0 --output-tail "..." [--observed]
ops_ctl.py watchers sync --file payload.json     # idempotent upsert (what *_inventory.py sends)
ops_ctl.py services pending                      # queued actions awaiting execution
ops_ctl.py services action-result <name> --ok|--fail --output "..."
ops_ctl.py services report --file reports.json   # {"reports":[{name,status,health,detail}]}
ops_ctl.py services sync --file payload.json
```

All commands accept `--json` where it matters; scripts load
`SOCIAL_API_URL` / `OPS_WATCHER_AGENT_TOKEN` / admin creds from env or
`~/cu130-slim/.env` automatically (skill_http).

**MCP:** the same surface is exposed as the `ops-plane` MCP server
(`scripts/ops-plane-mcp-server.py`, registered in `.devin/mcp_config.json`)
— 15 tools: `ops_status`, `ops_watchers_list/show/toggle/run`,
`ops_services_list/show/action`, and the agent lane
(`ops_watchers_due`, `ops_watcher_result`, `ops_watchers_sync`,
`ops_services_pending`, `ops_services_report`, `ops_service_action_result`,
`ops_services_sync`).

## How the loop works

1. **Inventory** (`scripts/*_inventory.py --apply`, cron 6h) scans the
   host (crontab, heal_supervisor, celery beat, docker ps, systemctl,
   powershell Get-ScheduledTask, n8n) and upserts registry rows.
2. **watcher_agent** (daemon, cron keepalive) polls `/ops/watchers/due`,
   executes host-agent commands under timeout, posts `/result`;
   observes external watchers via log-mtime freshness (`--observed`).
3. **service_agent** (daemon) collects docker/systemd/windows status →
   `/ops/services/report`, polls `/ops/services/actions`, executes
   allowlisted actions → `/action-result`.
4. **watchdog_gate** (`task_prerun` on every celery worker/beat) reads
   the registry (30s cache, fail-open) and raises `Ignore` for
   `executor=celery` watchers with `enabled=False` — so
   `ops_ctl.py watchers toggle <celery-task> off` genuinely stops the beat
   task before it runs.

## Reading statuses

- `stale` — `last_finished_at` older than ~2× interval (platform watchers)
  or observed log mtime beyond `stale_after` (external watchers).
- `error` — last execution exit_code != 0 (`consecutive_failures` counts up).
- `disabled` — `enabled=False`; celery ones are gate-skipped, others just
  sit idle. Re-enable with toggle on (platform watchers get a fresh
  `next_due_at`).
- Services `health`: healthy/unhealthy/unknown/degraded — `unknown`
  usually means the agent hasn't reported that tier recently (check
  `last_seen_at` / `last_reported_by`).

## Rules

- **Never restart observe-only/critical services** — docker-social-postgres,
  redis, n8n etc. carry `allowed_actions: ["status","logs"]` at most; the
  API enforces it (403). If a service needs lifecycle action, fix its
  `meta.allowed_actions` via `services sync` deliberately, not casually.
- `watchers run` on `executor=external` → 400 by design; external watchers
  report via `watchers result --observed`, they are never executed.
- Agent token is shared-secret only — do not print it; ops_ctl reads it
  from env/.env.
- Registry is the scheduler for host-agent watchers: `next_due_at` is the
  claim — `/due` pushes it forward atomically, so double-runs can't happen.
- After adding a new cron/systemd/docker service, run the matching
  `*_inventory.py --apply` (or wait for the 6h cron) before expecting it
  in `ops_ctl` listings.
