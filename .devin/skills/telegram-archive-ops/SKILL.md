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

## Architecture

- **Reader**: Telethon MTProto user session (needs `TG_API_ID` +
  `TG_API_HASH` from my.telegram.org → API development tools).
- **Writer path (unchanged)**: `send_telegram_alert(severity, text)` →
  @Cloudless_newbot → severity-routed channels.
- **Store**: dual-write — local SQLite cursor/cache at
  `~/.local/share/telegram-archive/archive.db` + canonical
  `telegram_channel_messages` table in SocialAuto Postgres
  (`social-postgres` on 127.0.0.1:5433, alembic `t9g0h1i2j3k4`).
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
for extras.

## One-time login (human gate, phone-assisted)

```bash
.devin/skills/telegram-archive-ops/scripts/tg_login_phone.py \
    --phone +30XXXXXXXXX          # TG_PHONE env also works
```

The helper prefers USB-C ADB (user directive) and falls back to the
tailnet wireless session (`100.84.31.85:5555`), starts `login`, waits for Telegram's code
notification via `dumpsys notification`, feeds it back, and handles the
2FA prompt if `TG_2FA_PASSWORD`/`--password` is set. Manual fallback:
run `telegram_archive.py login` and type the code yourself.

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

## Triage loop (the point of the archive)

1. `sync` (cron, hourly) or `backfill` after gaps.
2. `search`/SQL for alert keywords; cross-check live state
   (`docker ps`, healthchecks, ntfy) before acting — archived alerts
   are historical, verify before "fixing".
3. Write responses through the bot: `tg_alert.py "P2 …"`.
4. Never edit/delete channel messages; archive is read-only by design.

## Gotchas

- FloodWait/rate limits: `iter_messages` is gentle; backfill large
  ranges off-peak. Telethon retries FloodWait automatically.
- Session revoked (logout elsewhere / Telegram ban) → rerun `login`.
- `getUpdates` is unusable — the bot webhook owns updates.
- Message `date` is UTC; channel cursors are per-channel `msg_id`.
- Media is metadata-only (type name) — bytes are not downloaded.
