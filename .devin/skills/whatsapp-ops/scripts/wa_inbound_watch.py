#!/usr/bin/env python3
"""Watch whatsapp_messages for new inbound rows and verify the auto-reply loop.

Polls Postgres every ``--interval`` seconds. For each new inbound message it
waits up to ``--reply-window`` seconds for a matching outbound row (same
contact, created after the inbound) and logs the verdict. Runs for ``--hours``
then exits. State/log: ~/logs/wa-inbound-watch.log
"""

import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone

LOG = "/home/tbaltzakis/logs/wa-inbound-watch.log"

PSQL = [
    "docker", "exec", "social-postgres", "psql",
    "-U", "social_user", "-d", "social_automation", "-t", "-A", "-F", "|",
    "-c",
]

QUERY = (
    "select id, created_at, direction, coalesce(sender_phone,''), "
    "left(message_text,80) from whatsapp_messages "
    "where created_at > '{since}' order by created_at asc;"
)


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with open(LOG, "a") as fh:
        fh.write(line + "\n")


def query(since: str) -> list[list[str]]:
    sql = QUERY.format(since=since)
    out = subprocess.run(PSQL + [sql], capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        log(f"psql error: {out.stderr.strip()[:200]}")
        return []
    rows = []
    for line in out.stdout.strip().splitlines():
        parts = line.split("|")
        if len(parts) >= 5:
            rows.append(parts)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--reply-window", type=int, default=120)
    args = ap.parse_args()

    # watermark = latest existing row so old/synthetic pings don't retrigger
    latest = subprocess.run(
        PSQL + ["select coalesce(max(created_at)::text,'epoch') from whatsapp_messages;"],
        capture_output=True, text=True, timeout=30,
    )
    since = latest.stdout.strip() or "epoch"
    log(f"wa_inbound_watch started since={since} interval={args.interval}s")

    deadline = time.time() + args.hours * 3600
    pending: dict[str, dict] = {}  # inbound id -> {ts, phone, text, deadline}

    while time.time() < deadline:
        rows = query(since)
        for rid, ts, direction, phone, text in rows:
            since = max(since, ts)
            if direction == "inbound":
                pending[rid] = {
                    "ts": ts, "phone": phone, "text": text,
                    "deadline": time.time() + args.reply_window,
                }
                log(f"INBOUND id={rid} from={phone or '?'} text={text!r}")
            elif direction == "outbound":
                # match pending inbound from same phone created before this
                for iid, p in list(pending.items()):
                    if p["phone"] and phone and p["phone"] == phone:
                        log(f"REPLY-OK inbound={iid} -> outbound id={rid} text={text!r}")
                        del pending[iid]
                        break
        now = time.time()
        for iid, p in list(pending.items()):
            if now > p["deadline"]:
                log(f"REPLY-MISSING inbound={iid} from={p['phone'] or '?'} "
                    f"text={p['text']!r} (no outbound within {args.reply_window}s)")
                del pending[iid]
        time.sleep(args.interval)

    log("wa_inbound_watch exiting (window elapsed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
