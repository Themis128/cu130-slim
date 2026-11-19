#!/usr/bin/env python3
"""telegram_archive.py — read-side archive for the Cloudless Telegram channels.

The Bot API can post but cannot read channel history, so archiving needs a
user session (Telethon MTProto). This script keeps a persistent session +
SQLite store under ~/.local/share/telegram-archive/ (never in git).

Channels: TG_CH_CRITICAL / TG_CH_OPS / TG_CH_INTEL / TG_CH_DIGEST from the
repo .env (same IDs the alert router uses), plus extras via
TG_ARCHIVE_CHANNELS="name:-100id,name:-100id".

Credentials: TG_API_ID / TG_API_HASH env (create an app at my.telegram.org).

    telegram_archive.py login                  # one-time: phone -> code -> 2FA
    telegram_archive.py channels               # resolve + list archived channels
    telegram_archive.py sync                   # incremental pull (cron-safe)
    telegram_archive.py backfill --days 30     # deeper history pull
    telegram_archive.py status                 # per-channel counts + freshness
    telegram_archive.py search "oom"           # full-text search over archive
    telegram_archive.py export ops --since 2026-11-01 --out ops.jsonl

Writes still go through @Cloudless_newbot (tg_alert.py) — this is the
read/archive side only.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("TG_ARCHIVE_DIR", Path.home() / ".local/share/telegram-archive"))
SESSION = DATA_DIR / "archive"
DB = DATA_DIR / "archive.db"

DEFAULT_CHANNELS = {
    "critical": "-1004389402878",
    "ops": "-1004429103353",
    "intel": "-1004362141941",
    "digest": "-1004363123500",
}


def _env(name: str) -> str | None:
    v = os.environ.get(name)
    if v:
        return v
    try:
        for line in (REPO_ROOT / ".env").read_text().splitlines():
            if line.startswith(name + "="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    except OSError:
        pass
    return None


def channel_map() -> dict[str, str]:
    """name -> marked channel id (-100...)."""
    chans = dict(DEFAULT_CHANNELS)
    for key, envname in (("critical", "TG_CH_CRITICAL"), ("ops", "TG_CH_OPS"),
                         ("intel", "TG_CH_INTEL"), ("digest", "TG_CH_DIGEST")):
        if _env(envname):
            chans[key] = _env(envname)
    extra = _env("TG_ARCHIVE_CHANNELS") or ""
    for part in extra.split(","):
        if ":" in part:
            name, cid = part.split(":", 1)
            chans[name.strip()] = cid.strip()
    return chans


def db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS channels(
        name TEXT PRIMARY KEY, channel_id TEXT, entity_id INTEGER,
        title TEXT, username TEXT);
    CREATE TABLE IF NOT EXISTS messages(
        channel TEXT NOT NULL, msg_id INTEGER NOT NULL,
        date TEXT NOT NULL, sender TEXT, text TEXT,
        media TEXT, raw_json TEXT,
        PRIMARY KEY(channel, msg_id));
    CREATE TABLE IF NOT EXISTS state(
        channel TEXT PRIMARY KEY, last_msg_id INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX IF NOT EXISTS idx_messages_date ON messages(date);
    """)
    return con


def _client():
    from telethon import TelegramClient
    api_id = int(_env("TG_API_ID") or 0)
    api_hash = _env("TG_API_HASH")
    if not api_id or not api_hash:
        sys.exit("TG_API_ID / TG_API_HASH missing — create an app at my.telegram.org")
    return TelegramClient(str(SESSION), api_id, api_hash)


async def _resolve(client, con, name: str, marked_id: str):
    """Resolve a -100... marked id to an entity via dialogs cache."""
    want = int(marked_id)  # e.g. -1004389402878
    row = con.execute("SELECT entity_id FROM channels WHERE name=?", (name,)).fetchone()
    if row and row[0]:
        from telethon.tl.types import PeerChannel
        try:
            return await client.get_entity(PeerChannel(row[0]))
        except Exception:
            pass
    # Scan dialogs — access_hash lives there for private channels.
    async for d in client.iter_dialogs():
        pid = int(str(d.entity.id))
        marked = -1000000000000 - pid if pid > 0 else pid
        # Telethon marked ids: -100<id> where id already includes the marker math
        candidates = {pid, -pid, int(marked_id)}
        if d.entity.id in candidates or str(d.entity.id) in marked_id:
            con.execute("INSERT OR REPLACE INTO channels(name,channel_id,entity_id,title,username) VALUES(?,?,?,?,?)",
                        (name, marked_id, d.entity.id, getattr(d.entity, "title", ""), getattr(d.entity, "username", "")))
            con.commit()
            return d.entity
    # Fallback: bare channel id = marked id minus the -100 prefix
    try:
        ent = await client.get_entity(int(marked_id.lstrip("-").removeprefix("100")))
        return ent
    except Exception as e:
        print(f"  !! cannot resolve {name} ({marked_id}): {e}", file=sys.stderr)
        return None


def _msg_row(name, m):
    sender = ""
    try:
        sender = (m.sender.title if m.sender else "") or (getattr(m.sender, "username", "") or "") or str(getattr(m.sender, "id", ""))
    except Exception:
        pass
    media = ""
    if m.media:
        media = type(m.media).__name__
    return (name, m.id, m.date.astimezone(timezone.utc).isoformat() if m.date else "",
            sender, (m.text or "")[:8000], media,
            json.dumps({"id": m.id, "date": str(m.date), "media": media})[:2000])


async def cmd_login(args):
    client = _client()
    await client.connect()
    if await client.is_user_authorized():
        print("already authorized")
        await client.disconnect()
        return
    phone = args.phone or input("phone (+...): ").strip()
    await client.send_code_request(phone)
    code = args.code or input("code from Telegram: ").strip()
    try:
        await client.sign_in(phone, code)
    except Exception as e:
        if "password" in str(e).lower() or "2FA" in str(e):
            pw = args.password or input("2FA password: ").strip()
            await client.sign_in(password=pw)
        else:
            raise
    me = await client.get_me()
    print(f"authorized as {getattr(me,'username',None) or me.id}")
    await client.disconnect()


async def _sync_range(client, con, name, entity, min_id=0, max_id=None, limit=None):
    last = con.execute("SELECT last_msg_id FROM state WHERE channel=?", (name,)).fetchone()
    cursor = max(min_id, last[0] if last else 0)
    newest = cursor
    n = 0
    kwargs = {"reverse": True}
    if cursor:
        kwargs["min_id"] = cursor
    if max_id:
        kwargs["max_id"] = max_id
    if limit:
        kwargs["limit"] = limit
    async for m in client.iter_messages(entity, **kwargs):
        if not m.date:
            continue
        con.execute("INSERT OR REPLACE INTO messages VALUES(?,?,?,?,?,?,?)", _msg_row(name, m))
        newest = max(newest, m.id)
        n += 1
        if n % 200 == 0:
            con.commit()
            print(f"  {name}: {n} msgs...")
    con.execute("INSERT OR REPLACE INTO state VALUES(?,?)", (name, newest))
    con.commit()
    return n, newest


async def cmd_sync(args):
    client = _client()
    con = db()
    await client.connect()
    if not await client.is_user_authorized():
        sys.exit("not authorized — run `telegram_archive.py login` first")
    total = 0
    for name, marked in channel_map().items():
        ent = await _resolve(client, con, name, marked)
        if not ent:
            continue
        n, newest = await _sync_range(client, con, name, ent)
        total += n
        print(f"{name}: +{n} msgs (cursor {newest})")
    await client.disconnect()
    print(f"done — {total} new messages")


async def cmd_backfill(args):
    """Walk channel history oldest->newest, stop once messages predate --days."""
    client = _client()
    con = db()
    await client.connect()
    if not await client.is_user_authorized():
        sys.exit("not authorized — run `telegram_archive.py login` first")
    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    for name, marked in channel_map().items():
        if args.channel and name != args.channel:
            continue
        ent = await _resolve(client, con, name, marked)
        if not ent:
            continue
        n = 0
        async for m in client.iter_messages(ent, reverse=True):
            if not m.date:
                continue
            d = m.date if m.date.tzinfo else m.date.replace(tzinfo=timezone.utc)
            if d < since:
                break
            con.execute("INSERT OR REPLACE INTO messages VALUES(?,?,?,?,?,?,?)", _msg_row(name, m))
            n += 1
            if n % 200 == 0:
                con.commit()
                print(f"  {name}: {n}...")
        con.commit()
        newest = con.execute("SELECT COALESCE(MAX(msg_id),0) FROM messages WHERE channel=?", (name,)).fetchone()[0]
        con.execute("INSERT OR REPLACE INTO state VALUES(?,?)", (name, newest))
        con.commit()
        print(f"{name}: backfilled {n} msgs")
    await client.disconnect()


def cmd_status(args):
    con = db()
    for name, marked in channel_map().items():
        cnt = con.execute("SELECT COUNT(*) FROM messages WHERE channel=?", (name,)).fetchone()[0]
        last = con.execute("SELECT MAX(date) FROM messages WHERE channel=?", (name,)).fetchone()[0]
        cur = con.execute("SELECT last_msg_id FROM state WHERE channel=?", (name,)).fetchone()
        print(f"{name:9} {cnt:6} msgs  cursor={cur[0] if cur else 0:<12} latest={last}")


def cmd_search(args):
    con = db()
    q = f"%{args.query}%"
    rows = con.execute(
        "SELECT channel,date,sender,substr(text,1,300) FROM messages WHERE text LIKE ? ORDER BY date DESC LIMIT ?",
        (q, args.limit)).fetchall()
    for r in rows:
        print(f"[{r[0]}] {r[1][:16]} {r[2][:20]}: {r[3]}")
    print(f"-- {len(rows)} hits")


def cmd_export(args):
    con = db()
    q = "SELECT msg_id,date,sender,text,media FROM messages WHERE channel=?"
    params = [args.channel]
    if args.since:
        q += " AND date>=?"
        params.append(args.since)
    q += " ORDER BY msg_id"
    rows = con.execute(q, params).fetchall()
    out = args.out or f"{args.channel}-export.jsonl"
    with open(out, "w") as f:
        for r in rows:
            f.write(json.dumps({"channel": args.channel, "msg_id": r[0], "date": r[1],
                                "sender": r[2], "text": r[3], "media": r[4]}, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} messages -> {out}")


def cmd_channels(args):
    con = db()
    for r in con.execute("SELECT name,channel_id,title,username FROM channels"):
        print(f"{r[0]:9} {r[1]:<16} {r[2] or ''} @{r[3] or ''}")


async def _async_main(args):
    fn = {"login": cmd_login, "sync": cmd_sync, "backfill": cmd_backfill}.get(args.cmd)
    if fn:
        await fn(args)
    else:
        {"status": cmd_status, "search": cmd_search, "export": cmd_export,
         "channels": cmd_channels}.get(args.cmd, lambda a: None)(args)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("login"); lp.add_argument("--phone"); lp.add_argument("--code"); lp.add_argument("--password")
    sub.add_parser("sync")
    bp = sub.add_parser("backfill"); bp.add_argument("--days", type=int, default=30); bp.add_argument("--channel")
    sub.add_parser("status"); sub.add_parser("channels")
    sp = sub.add_parser("search"); sp.add_argument("query"); sp.add_argument("--limit", type=int, default=50)
    ep = sub.add_parser("export"); ep.add_argument("channel"); ep.add_argument("--since"); ep.add_argument("--out")
    args = p.parse_args()
    if args.cmd in ("login", "sync", "backfill"):
        asyncio.run(_async_main(args))
    else:
        _async_main(args)


if __name__ == "__main__":
    main()
