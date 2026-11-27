# telegram-archive-ops

Read-side archive for the Cloudless Telegram infra channels. Companion to
`telegram-alert-ops` (write path via @Cloudless_newbot) and
`telegram-channel-ops` (bot-side channel admin).

## Why this exists

The Bot API **cannot read channel history** — bots only post. The four
private infra channels (#critical/#ops/#intel/#digest) therefore had no
readable archive until the Telethon user-session archiver landed
(PR cu130-slim#2385). Alert triage ("check all the telegram
notifications") is impossible without it.

## Read lanes (three, converging on one table)

`telegram_channel_messages` is fed by three lanes — each covers what the
others can't:

1. **Bot-authored posts → send-time archive** (`tg_alert.py::_archive`).
   Telegram never emits `channel_post` updates for a bot's own posts, so
   every alert send dual-writes into the table (psycopg, best-effort,
   never fails the alert). Covers ~all #ops/#critical traffic.
2. **External channel posts → webhook ingest** (`receive_webhook` in
   `app/api/telegram.py`). `allowed_updates` now includes
   `channel_post`/`edited_channel_post`; posts by other channel
   members are archived live.
3. **Historical backfill → Telethon archiver** (`telegram_archive.py`
   `sync`/`backfill`). Only lane needing `TG_API_ID`/`TG_API_HASH`.

## Architecture

- **Reader**: Telethon MTProto user session (needs `TG_API_ID` +
  `TG_API_HASH` from my.telegram.org → API development tools —
  `tg_api_setup.py` automates this: send_password → login → /apps,
  code read off the phone via ADB uiautomator).
- **Writer path (unchanged)**: `send_telegram_alert(severity, text)` →
  @Cloudless_newbot → severity-routed channels.
- **Store**: dual-write — local SQLite cursor/cache at
  `~/.local/share/telegram-archive/archive.db` + canonical
  `telegram_channel_messages` table in SocialAuto Postgres
  (`social-postgres` on 127.0.0.1:5433, db `social_automation`,
  alembic `t9g0h1i2j3k4`).
- **Session**: `~/.local/share/telegram-archive/archive.session`
  (SQLiteSession — persists auth keys; chmod 600 dir, never commit).
- **Runtime**: venv at `~/.local/share/telegram-archive/venv`
  (telethon + psycopg[binary]); hourly cron `17 * * * *` runs `sync`,
  logs to `~/logs/telegram-archive.log`.

## Commands

```bash
PY=~/.local/share/telegram-archive/venv/bin/python
$PY scripts/telegram_archive.py login                 # one-time auth
$PY scripts/telegram_archive.py sync                  # incremental (cron)
$PY scripts/telegram_archive.py backfill --days 30    # history pull
$PY scripts/telegram_archive.py status                # counts + cursors
$PY scripts/telegram_archive.py search "oom"          # full-text search
$PY scripts/telegram_archive.py export ops --out ops.jsonl
$PY scripts/telegram_archive.py channels              # resolved entities
```

Channels resolve from `TG_CH_CRITICAL/OPS/INTEL/DIGEST` in `.env`
(same IDs as the alert router) + `TG_ARCHIVE_CHANNELS="name:-100id,…"`
for extras. **All-channels coverage**: `sync`/`backfill` auto-discover
every broadcast channel in the account's dialogs (title-slugged names,
registered in the sqlite `channels` table) — the env map only pins the
canonical names for the four infra channels.

## One-time login (human gate, phone-assisted)

```bash
.devin/skills/telegram-archive-ops/scripts/tg_login_phone.py \
    --phone +30XXXXXXXXX          # TG_PHONE env also works
```

The helper prefers USB-C ADB (user directive), falls back to the
tailnet wireless session (`100.84.31.85:5555`), and finally to the
**Termux SSH lane** (`ssh -p8022 $TG_TERMUX_HOST termux-notification-list`,
default host `100.84.31.85`) — the Termux lane needs no adb at all and
survives wireless-debugging flaps. It waits for Telegram's code
notification, feeds it back, and handles the
2FA prompt if `TG_2FA_PASSWORD`/`--password` is set. Manual fallback:
run `telegram_archive.py login` and type the code yourself.
`tg_api_setup.py --phone-adb` reads my.telegram.org web codes the same
way (uiautomator → Termux notifications fallback).

The session is per-user-account — it reads every channel/dialog that
account belongs to, including the private Cloudless channels.

## Querying the archive

Postgres (canonical, survives host rebuilds):

```sql
SELECT channel, date, sender, left(text,200)
FROM telegram_channel_messages
WHERE channel='ops' AND date > now() - interval '7 days'
ORDER BY date DESC;
```

Or via Metabase / the SocialAuto API once a read endpoint exists.
Local quick search: `telegram_archive.py search "<term>"`.

## Automated resolution loop

`telegram-incidents-ingest` (5-min Celery beat) scans **every channel**
in the archive table (skip-set: `intel`, `digest`, extend via
`TG_INGEST_SKIP`), classifies alert-shaped posts, and feeds
`ops_incidents` via `report_incident`. `ops-agent-sweep` (5-min) then
runs LLM RCA grounded in `app/services/ops_runbooks.py` — curated
runbooks distilled from the repo docs (fsbridge, socket-proxy inodes,
asyncpg driver, tunnel/530, healthcheck rules, credential lanes, DMR,
disk/OOM) — applies whitelisted task reruns, verifies, and escalates to
Telegram. Full message history also lands in the datalake at
`lake/socialauto-telegram/messages.json` (full-history snapshot, every
`export-datalake` run).

## Triage loop (the point of the archive)

1. `sync` (cron, hourly) or `backfill` after gaps.
2. `search`/SQL for alert keywords; cross-check live state
   (`docker ps`, healthchecks, ntfy) before acting — archived alerts
   are historical, verify before "fixing".
3. Write responses through the bot: `tg_alert.py "P2 …"`.
4. `tg_channel_janitor.py` (daily cron 04:30) closes the loop — the
   owner session deletes posts the sweep marked `resolved`, known
   noise signatures (>2h), rollup-channel posts (>6h), and stale posts
   (>24h) that have no live incident and are already in the datalake.
   Keeps: open/escalated/triaged incidents, 📌 live-state pins, fresh
   alerts. The channel is an inbox; Postgres is the audit trail.

## Gotchas

- FloodWait/rate limits: `iter_messages` is gentle; backfill large
  ranges off-peak. Telethon retries FloodWait automatically.
- Session revoked (logout elsewhere / Telegram ban) → rerun `login`.
- `getUpdates` is unusable — the bot webhook owns updates.
- Message `date` is UTC; channel cursors are per-channel `msg_id`.
- Media is metadata-only (type name) — bytes are not downloaded.
