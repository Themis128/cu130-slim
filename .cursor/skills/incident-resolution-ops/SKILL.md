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
Slack alert channels (notify_archive_ingest — conversations.history)
ntfy alert topics   (notify_archive_ingest — /<topic>/json poll)
Gmail inbox         (notify_archive_ingest — messages.list/get)
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
| Slack alert channels (ops-alerts, socialauto, socialauto-telegram + `SLACK_ARCHIVE_CHANNELS`) | `notify_archive_ingest` 5-min | yes — `slack:<name>` rows |
| ntfy topics (cloudless-ops/alerts, nextcloud-ops + `NTFY_ARCHIVE_TOPICS`) | `notify_archive_ingest` 5-min | yes — `ntfy:<topic>` rows |
| Gmail inbox (`EMAIL_ARCHIVE_QUERY`, default `in:inbox`) | `notify_archive_ingest` 5-min | yes — `email:<mailbox>` rows, alert-sender allowlist |
| `slack:socialauto`, `slack:socialauto-telegram` (digest channels) | archived | skipped like intel/digest |

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

## Slack + ntfy surfaces

`app/worker/tasks/notify_archive.py::notify_archive_ingest` (beat :03
every 5 min) is the collector. Slack ids come from
`SLACK_ALERTS_CHANNEL_ID`/`SLACK_OPS_CHANNEL`/`SLACK_CHANNEL_ID`/
`SLACK_TELEGRAM_CHANNEL_ID` envs (or `SLACK_ARCHIVE_CHANNELS`
override); channel names resolve via `conversations.info`. ntfy topics
default `cloudless-ops,cloudless-alerts,nextcloud-ops`
(`NTFY_ARCHIVE_TOPICS` override) and read via `NTFY_READER_TOKEN` — a
dedicated read-only account minted by the
`cloudless.gr/.github/workflows/ntfy-reader-token.yml` workflow
(the alert-relay publisher is write-only by design; rotate by
re-running the workflow and re-downloading the artifact). Cursor hash:
`notify_archive:cursor`.

## Gotchas

- Baked images: new task/service files do NOT exist in running
  containers — `docker cp` is an ephemeral stopgap; durability = PR
  merge + image rebuild/deploy.
- Raw SQL inserts need `gen_random_uuid()` + `created_at` — ORM
  defaults are client-side only.
- `channel` values: env-mapped names (critical/ops/intel/digest),
  title-slug for other Telegram channels, `slack:<name>` / `ntfy:<topic>`
  for the new surfaces; raw chat_id as last resort.
- `msg_id` is BIGINT — Slack ids are ts-as-µs (16 digits); ntfy/Gmail
  ids are sha1(provider-id) masked to signed bigint. The int4→bigint
  migration is `u0a1b2c3d4e5`.
- Incident ingest cursor is `tg_ingest:v2` (created_at epoch-ms) —
  msg_id ordering only works for monotonic ids; hashed ids made rows
  invisible to the v1 cursor. Cursors land only after db.commit.
- `email:` channels classify ONLY when the sender matches
  `_EMAIL_ALERT_RE` (vercel/github/cloudflare/cloudless/…, extend via
  `TG_INGEST_EMAIL_SENDERS`) — the inbox is too broad for denylisting.
- `_SKIP_MSG_RE` drops noise INSIDE alert channels: [digest]/[intel]/
  [P3]/[P4] fanout, canary self-checks, generated reports.
- Slack cursor is the message ts STRING (e.g. "1795087811.752809"),
  stored verbatim in Redis; pagination follows `next_cursor` max 10
  pages/run — long backfills span multiple beats.
- Severity: `critical` → CRITICAL, everything else → OPS by default.
