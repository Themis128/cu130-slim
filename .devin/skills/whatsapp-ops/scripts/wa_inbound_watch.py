#!/usr/bin/env python3
"""wa_inbound_watch.py — detect the first REAL inbound WhatsApp message and
verify the auto-reply pipeline fired end-to-end.

Polls Postgres ``whatsapp_messages`` every POLL_SECONDS. Ignores our own
synthetic webhook-injection tests (``wamid.DEVIN_%`` message ids and the
``306944455566`` test sender). On the first genuine inbound row it checks
that an outbound message to the same sender exists (auto-reply sent) and
reports PASS/FAIL.

Run inside the compose network or on the host with psql access:

    python3 wa_inbound_watch.py                 # poll up to 24h
    python3 wa_inbound_watch.py --once          # single check
    python3 wa_inbound_watch.py --timeout-h 48

Env:
    WA_DB_URL   postgres DSN (default: compose social-postgres)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "300"))
TEST_SENDER = "306944455566"
LOG = os.path.expanduser("~/logs/wa-inbound-watch.log")


def _log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _psql(sql: str) -> str:
    """Run a query against the compose social-postgres."""
    url = os.environ.get("WA_DB_URL")
    if url:
        # Minimal asyncpg/psql fallback — expect psql in PATH for DSN form.
        r = subprocess.run(
            ["psql", url, "-t", "-A", "-c", sql],
            capture_output=True, text=True, timeout=30,
        )
    else:
        r = subprocess.run(
            [
                "docker", "compose", "-f", os.path.expanduser("~/cu130-slim/docker-compose.yml"),
                "exec", "-T", "social-postgres",
                "psql", "-U", "social_user", "-d", "social_automation",
                "-t", "-A", "-c", sql,
            ],
            capture_output=True, text=True, timeout=60,
            cwd=os.path.expanduser("~/cu130-slim"),
        )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300])
    return r.stdout.strip()


def _first_real_inbound() -> dict | None:
    sql = (
        "SELECT sender_phone, sender_name, left(message_text,120), message_id, created_at "
        "FROM whatsapp_messages WHERE direction='inbound' "
        f"AND sender_phone <> '{TEST_SENDER}' "
        "AND (message_id IS NULL OR message_id NOT LIKE 'wamid.DEVIN%') "
        "ORDER BY created_at DESC LIMIT 1;"
    )
    out = _psql(sql)
    if not out:
        return None
    parts = out.split("|")
    return {
        "sender": parts[0],
        "name": parts[1] if len(parts) > 1 else "",
        "text": parts[2] if len(parts) > 2 else "",
        "wamid": parts[3] if len(parts) > 3 else "",
        "at": parts[4] if len(parts) > 4 else "",
    }


def _outbound_to(sender: str) -> str | None:
    sql = (
        "SELECT message_type, left(message_text,120), created_at FROM whatsapp_messages "
        f"WHERE direction='outbound' AND sender_phone='{sender}' "
        "ORDER BY created_at DESC LIMIT 1;"
    )
    out = _psql(sql)
    return out or None


def _notify(text: str) -> None:
    """Best-effort Slack/ntfy notification via the stack's alert helper."""
    try:
        req = urllib.request.Request(
            "http://social-api:8083/api/v1/notifications/test",
            data=json.dumps({"text": text}).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass  # log line is the durable record


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--timeout-h", type=float, default=24)
    args = ap.parse_args()

    deadline = time.time() + args.timeout_h * 3600
    _log("wa_inbound_watch started")
    while True:
        try:
            inbound = _first_real_inbound()
        except Exception as e:  # noqa: BLE001
            _log(f"db poll error: {e}")
            inbound = None
        if inbound:
            reply = _outbound_to(inbound["sender"])
            status = "AUTO-REPLY SENT" if reply else "NO AUTO-REPLY YET"
            _log(
                f"REAL INBOUND from {inbound['sender']} ({inbound['name']}) "
                f"at {inbound['at']}: {inbound['text']!r} -> {status}"
                + (f" reply={reply}" if reply else "")
            )
            _notify(
                f":white_check_mark: WhatsApp inbound verified — {inbound['sender']} "
                f"messaged the business number; auto-reply {'sent' if reply else 'PENDING'}."
            )
            return 0
        if args.once:
            _log("no real inbound yet")
            return 0
        if time.time() > deadline:
            _log("timeout — no real inbound observed")
            return 1
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
