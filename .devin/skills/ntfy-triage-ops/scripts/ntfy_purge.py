#!/usr/bin/env python3
"""ntfy_purge.py — delete cached ntfy messages matching a pattern.

Read side lives in ntfy_triage.py; this is the WRITE side for resolved
alerts. ntfy DELETE /<topic>/<id> removes a cached message; the phone
app drops the notification on next sync.

Safety: dry-run by default. Deletes ONLY messages whose body matches
ALL given --match regexes (case-insensitive). Rate-limit aware: ntfy
answers 429 under burst deletes — retries with backoff automatically.

Usage:
    ntfy_purge.py --match "database system is starting up"           # dry-run
    ntfy_purge.py --match "IG business_login token dead" --apply
    ntfy_purge.py --match "Worker task failed" --match "postgres" \
        --topic cloudless-alerts --since 12h --apply
    ntfy_purge.py --ids id1,id2 --topic cloudless-ops --apply
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path.home()
TOKEN_CACHE = HOME / ".cache" / "ntfy-reader-token"
TOPICS = ["cloudless-alerts", "cloudless-ops", "nextcloud-ops",
          "devoptimizer-alerts"]
BASES = ["https://ntfy.cloudless.gr", "http://github-omv:30080"]
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) ntfy-purge/1.0"}


def token() -> str:
    t = os.environ.get("NTFY_READ_TOKEN")
    if t:
        return t
    if TOKEN_CACHE.exists():
        return TOKEN_CACHE.read_text().strip()
    sys.exit("no read token — run ntfy_triage.py mint-token first")


def _req(url, method="GET", tok=None):
    h = dict(UA)
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return urllib.request.urlopen(
        urllib.request.Request(url, method=method, headers=h), timeout=30)


def fetch(topic, since, tok):
    last_err = None
    for base in BASES:
        try:
            r = _req(f"{base}/{topic}/json?poll=1&since={since}h", tok=tok)
            for line in r:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        pass
            return
        except Exception as e:  # try next base
            last_err = e
    raise last_err or RuntimeError("no ntfy base reachable")


def delete(topic, mid, tok):
    last = None
    for base in BASES:
        for attempt in range(5):
            try:
                _req(f"{base}/{topic}/{mid}", method="DELETE", tok=tok)
                return True
            except urllib.error.HTTPError as e:
                last = e
                if e.code == 429:
                    time.sleep(3 + attempt * 3)
                    continue
                break
            except Exception as e:
                last = e
                break
    return last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--match", action="append", default=[],
                    help="regex the message body must match (repeatable, ANDed)")
    ap.add_argument("--ids", help="comma-separated message ids (exact)")
    ap.add_argument("--topic", action="append", choices=TOPICS + ["all"],
                    default=["all"])
    ap.add_argument("--since", type=int, default=24,
                    help="hours of history to scan (default 24)")
    ap.add_argument("--apply", action="store_true",
                    help="actually delete (default dry-run)")
    a = ap.parse_args()

    pats = [re.compile(p, re.I) for p in a.match]
    ids = set(a.ids.split(",")) if a.ids else None
    if not pats and not ids:
        sys.exit("give --match or --ids")

    topics = TOPICS if "all" in a.topic else a.topic
    tok = token()
    targets = []
    for topic in topics:
        try:
            for ev in fetch(topic, a.since, tok):
                if ev.get("event") != "message":
                    continue
                msg = ev.get("message", "")
                if ids is not None:
                    if ev.get("id") not in ids:
                        continue
                elif not all(p.search(msg) for p in pats):
                    continue
                targets.append((topic, ev["id"], msg[:90].replace("\n", " ")))
        except Exception as e:
            print(f"fetch {topic}: {e}", file=sys.stderr)

    print(f"{len(targets)} message(s) matched")
    for t, i, m in targets[:20]:
        print(f"  {t}/{i}  {m}")
    if len(targets) > 20:
        print(f"  ... +{len(targets)-20} more")
    if not a.apply:
        print("dry-run — pass --apply to delete")
        return

    ok = fail = 0
    for topic, mid, _ in targets:
        res = delete(topic, mid, tok)
        if res is True:
            ok += 1
        else:
            fail += 1
            print(f"  FAIL {topic}/{mid}: {res}", file=sys.stderr)
        time.sleep(1.0)  # stay under ntfy's burst limit
    print(f"deleted {ok}, failed {fail}")


if __name__ == "__main__":
    main()
