#!/usr/bin/env python3
"""tg_channel_janitor.py — daily owner-session cleanup of the 4 alert channels.

Closes the architecture loop unattended:
  channel post → telegram-incidents-ingest → ops_incidents → ops-agent-sweep
  → THIS script deletes what the loop has resolved.

Delete rules (per channel):
  ops/critical  — post linked to a status='resolved' ops_incidents row via
                  EITHER alert_chat_id+alert_message_id (the sweep's outbound
                  alert) OR detail.channel+detail.msg_id (the source post that
                  spawned the incident), OR matches a pure-noise signature and
                  is older than NOISE_GRACE_H (InfoInhibitor, skip rollups,
                  publish confirmations — they never create incidents).
  digest/intel  — every post older than DIGEST_GRACE_H (rollups/confirmations,
                  never actionable).
Everything else (open/escalated/triaged incidents, unmatched alerts, pins)
stays — the sweep resolves it and the next janitor pass clears it.

Runs as the channel OWNER (archive.session) — MTProto has no 48h limit and
no can_delete_messages requirement.

    tg_channel_janitor.py [--dry-run] [--days 7]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SESSION = Path.home() / ".local/share/telegram-archive/archive.session"

CHANNELS = {  # name -> (chat_id, class)
    "critical": (-1004389402878, "alert"),
    "ops": (-1004429103353, "alert"),
    "intel": (-1004362141941, "rollup"),
    "digest": (-1004363123500, "rollup"),
}
NOISE_GRACE_H = 2      # alert-channel noise deleted only after this age
DIGEST_GRACE_H = 6     # rollup channels cleared after this age
STALE_H = 24           # unactioned posts older than this delete if archived
MAX_AGE_D = 30         # never look further back than this
DELETE_CHUNK = 100
DELETE_CAP = 800       # per channel per run — bound API calls

# Pure-noise signatures — verified during the manual sweep: these never
# carry an actionable incident.
NOISE_RES = [
    re.compile(r"FIRING InfoInhibitor|Info-level alert inhibition", re.I),
    re.compile(r"⏭\ufe0f?|skipped:|skip rollup", re.I),
    re.compile(r"✅.*(published|posted|sent|scheduled|resolved)", re.I | re.S),
    re.compile(r"auto-resolved|self-healed|recovered\b|OK again|back online"
               r"|healed\b|auto-restart|repair complete|ADB back"
               r"|MCP health change.*->\s*ok", re.I),
]


def _env(key):
    for line in (REPO / ".env").read_text().splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    return os.environ.get(key)


def _pg():
    import psycopg
    user, pw = _env("SOCIAL_POSTGRES_USER"), _env("SOCIAL_POSTGRES_PASSWORD")
    db = _env("SOCIAL_POSTGRES_DB") or "social_automation"
    if user and pw:
        return psycopg.connect(
            f"postgresql://{user}:{pw}@127.0.0.1:5433/{db}", autocommit=True)
    return None


def _norm_chat(s) -> str:
    """'-1004429103353' and '4429103353' describe the same channel."""
    return re.sub(r"^-?100", "", str(s or ""))


def incident_sets(con) -> tuple[dict[str, set[int]], dict[str, set[int]]]:
    """Per-channel resolved/openish msg_id sets, joined on BOTH linkages:
    alert_chat_id+alert_message_id (the sweep's outbound alert post) and
    detail.channel+detail.msg_id (the source post that spawned the incident).
    A mid lands in openish if ANY of its incidents is unresolved — the stale
    and resolved rules both consult openish so live work is never deleted."""
    rows = con.execute(
        "SELECT alert_chat_id, alert_message_id, status, "
        "detail->>'msg_id', detail->>'channel' FROM ops_incidents").fetchall()
    resolved = {n: set() for n in CHANNELS}
    openish = {n: set() for n in CHANNELS}
    chat_map = {_norm_chat(cid): n for n, (cid, _) in CHANNELS.items()}
    for achat, amid, status, dmid, dchan in rows:
        targets = set()
        if achat and _norm_chat(achat) in chat_map:
            targets.add(chat_map[_norm_chat(achat)])
        if dchan:
            n = str(dchan).removeprefix("cloudless-")
            if n in CHANNELS:
                targets.add(n)
        if not targets:
            continue
        mids = set()
        for raw in (amid, dmid):
            if raw:
                try:
                    mids.add(int(raw))
                except (TypeError, ValueError):
                    pass
        for n in targets:
            (resolved if status == "resolved" else openish)[n] |= mids
    return resolved, openish


def archived_msg_ids(con, channel: str) -> set[int]:
    rows = con.execute(
        "SELECT msg_id FROM telegram_channel_messages WHERE channel=%s",
        (channel,)).fetchall()
    return {int(r[0]) for r in rows}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--days", type=int, default=7, help="look-back window")
    args = ap.parse_args()

    from telethon import TelegramClient
    api_id, api_hash = int(_env("TG_API_ID") or 0), _env("TG_API_HASH")
    if not api_id or not api_hash:
        sys.exit("TG_API_ID/TG_API_HASH missing")
    if not SESSION.exists():
        sys.exit(f"no session at {SESSION} — run tg_login_phone.py first")

    con = _pg()
    if con is None:
        sys.exit("postgres unreachable — cannot check incident status")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=min(args.days, MAX_AGE_D))
    noise_grace = now - timedelta(hours=NOISE_GRACE_H)
    rollup_grace = now - timedelta(hours=DIGEST_GRACE_H)
    stale = now - timedelta(hours=STALE_H)

    resolved_all, openish_all = incident_sets(con)

    total = {"scanned": 0, "deleted": 0, "kept": 0}
    async with TelegramClient(str(SESSION), api_id, api_hash) as c:
        for name, (chat_id, klass) in CHANNELS.items():
            entity = await c.get_entity(chat_id)
            resolved = resolved_all[name] - openish_all[name]
            openish = openish_all[name]
            archived = archived_msg_ids(con, f"cloudless-{name}")
            del_ids, scanned, kept = [], 0, 0
            async for m in c.iter_messages(entity):
                if m.date < cutoff:
                    break
                scanned += 1
                if len(del_ids) >= DELETE_CAP:
                    break
                text = m.text or ""
                if m.pinned or "📌" in text:
                    kept += 1
                    continue
                if klass == "rollup":
                    if m.date < rollup_grace:
                        del_ids.append(m.id)
                        continue
                else:
                    if m.id in resolved:
                        del_ids.append(m.id)
                        continue
                    if m.date < noise_grace and any(r.search(text) for r in NOISE_RES):
                        del_ids.append(m.id)
                        continue
                    # stale: old, no live incident, and safe in the datalake —
                    # the channel is an inbox, PG is the audit trail
                    if (m.date < stale and m.id not in openish
                            and m.id in archived):
                        del_ids.append(m.id)
                        continue
                kept += 1
            if del_ids and not args.dry_run:
                for i in range(0, len(del_ids), DELETE_CHUNK):
                    await c.delete_messages(entity, del_ids[i:i + DELETE_CHUNK])
                    time.sleep(1)
            print(f"{name}: scanned={scanned} "
                  f"{'would-delete' if args.dry_run else 'deleted'}={len(del_ids)} kept={kept}")
            total["scanned"] += scanned
            total["deleted"] += len(del_ids)
            total["kept"] += kept
    print(f"janitor done — {total}")


if __name__ == "__main__":
    asyncio.run(main())
