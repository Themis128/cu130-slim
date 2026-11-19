# incident-resolution-ops

The automated resolution loop: every Telegram channel message lands in
the platform archive, alert-shaped posts become incidents, and the
ops-agent triages them with runbook-grounded LLM RCA — whitelisted
fixes applied automatically, everything else escalated to Telegram.

## The loop (all lanes live)

```
bot post (tg_alert send-time archive)
external post (channel_post webhook ingest)
history (Telethon sync/backfill — telegram-archive-ops)
        │
        ▼
telegram_channel_messages  ──export──▶  lake/socialauto-telegram/messages.json
        │                                  (full-history snapshot, every
        │                                   export-datalake run)
        ▼ every 5 min
telegram-incidents-ingest   (app/worker/tasks/telegram_incidents.py)
  · scans EVERY channel present in the table
  · skip-set: {intel, digest} + TG_INGEST_SKIP="name,…"
  · _classify: alert-shaped text → (kind, target); recovery/
    heartbeat posts skipped; Redis cursor per channel
        │
        ▼
ops_incidents  (report_incident — fingerprint dedup + recurrence)
        │
        ▼ every 5 min
ops-agent-sweep             (app/services/ops_agent.py)
  · container evidence via read-only docker-socket-proxy
  · LLM RCA (DMR → CF fallback) grounded by ops_runbooks.py
  · verdict → none | rerun:<key> | resolve | escalate
  · ALLOWED_RERUN_TASKS whitelist only — no arbitrary mutation
        │
        ▼
send_telegram_alert → severity-routed channel reply (self-archives)
```

## Coverage matrix

| Message source | Archived? | Ingested to incidents? |
|---|---|---|
| bot-authored alerts (tg_alert) | send-time write | yes, if alert-shaped |
| external posts, any admined channel | webhook channel_post | yes — all channels scanned |
| channel history | Telethon backfill (needs TG_API_ID/HASH) | yes on next ingest sweep |
| intel/digest informational | archived | skipped by default |

## Runbook layer — `app/services/ops_runbooks.py`

The RCA prompt injects the top-3 matching runbook snippets (keyword
match over kind/target/title/detail). Entries are distilled from the
repo's ops docs — fsbridge, socket-proxy inodes, asyncpg DSN, tunnel
530, healthcheck rules, credential lanes, Celery baked-image, n8n,
DMR, disk, OOM.

**Maintenance contract**: runbooks are knowledge-as-code because the
baked image does not mount repo docs. When AGENTS.md or an ops skill
gains a new failure class/fix, add a `_RUNBOOKS` entry — ordering
matters (first match wins per row, specific classes before generic
keyword classes like "worker").

## Extending autonomy

- **New remediation**: add `task_key: (path, args)` to
  `ALLOWED_RERUN_TASKS` in `ops_agent.py`. The task must be read-only
  or idempotent-by-design — arbitrary container mutation requires an
  explicit safety review, not a dict entry.
- **New alert signature**: extend `_INCIDENT_RE` / kind needles in
  `telegram_incidents.py`. Recovery patterns live in `_RECOVERED_RE`.
- **Exclude a channel**: `TG_INGEST_SKIP="name"` env on workers.

## Verify

```bash
# ingest path (container has baked-image code — check drift first)
docker exec social-worker-default python -c \
  "from app.worker.tasks.telegram_incidents import telegram_incidents_ingest; \
   print(telegram_incidents_ingest.apply_async().get(timeout=60))"

# runbook grounding
docker exec social-worker-default python -c \
  "from app.services.ops_runbooks import runbook_context; \
   print(runbook_context('container_unhealthy','social-api','unhealthy','')[:200])"

# archive freshness
docker exec social-postgres psql -U social_user -d social_automation -c \
  "SELECT channel, max(date) FROM telegram_channel_messages GROUP BY 1;"
```

## Gotchas

- Baked images: new task/service files do NOT exist in running
  containers — `docker cp` is an ephemeral stopgap; durability = PR
  merge + image rebuild/deploy.
- Raw SQL inserts need `gen_random_uuid()` + `created_at` — ORM
  defaults are client-side only.
- `channel` values: env-mapped names (critical/ops/intel/digest),
  title-slug for other channels, raw chat_id as last resort.
- Severity: `critical` → CRITICAL, everything else → OPS by default.
