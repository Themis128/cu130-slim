#!/usr/bin/env python3
"""Ops wrapper for the social-api Cloudflare (D1/KV/Vectorize) endpoints.

Base URL: http://localhost:8083/api/v1/cf-db

Usage:
    python3 scripts/d1_ops.py health            # d1/kv/vectorize/router/write-budget
    python3 scripts/d1_ops.py status            # router status
    python3 scripts/d1_ops.py tables            # D1 table row counts
    python3 scripts/d1_ops.py sync              # bidirectional D1 <-> Postgres sync
    python3 scripts/d1_ops.py replay            # replay queued writes after failover
    python3 scripts/d1_ops.py pk <table>        # check a D1 table has a PRIMARY KEY

`sync` can burn through the Cloudflare free-tier daily row-write limit —
check `health` (d1_write_budget) before running it.

`pk` shells into social-api and runs PRAGMA table_info(<table>) via the D1
client (there is no raw-SQL HTTP endpoint). Known-good: post_targets has PK
(post_id, social_account_id) in Postgres — a missing PK in D1 needs a
migration (exit 2).

Exit codes: 0 ok / 1 error / 2 needs-manual-action.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "http://localhost:8083/api/v1/cf-db"


def http(method: str, path: str, timeout: float = 30.0) -> tuple[int | None, dict]:
    req = urllib.request.Request(API + path, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return None, {"error": str(e)}


def cmd_health(args) -> int:
    code, body = http("GET", "/health")
    if code != 200:
        print(f"ERROR: cf-db health returned {code}: {body}")
        return 1

    budget = body.get("d1_write_budget") or {}
    router = body.get("router") or {}
    print("=== cf-db health ===")
    print(f"d1                    {body.get('d1')}")
    print(f"kv                    {body.get('kv')}")
    print(f"vectorize             {body.get('vectorize')}")
    print(f"router.mode           {router.get('mode')}")
    print(f"router.d1_primary     {router.get('d1_primary')}")
    print(f"router.circuit_open   {router.get('circuit_open')}")
    print(f"router.replay_queue   {router.get('replay_queue_size')}")
    print(
        f"d1_write_budget       {budget.get('writes_today')}/{budget.get('limit')} "
        f"(remaining {budget.get('remaining')}, throttled={budget.get('throttled')})"
    )
    print(f"tokens_available      {body.get('tokens_available')}")

    used = budget.get("writes_today") or 0
    limit = budget.get("limit") or 0
    remaining = budget.get("remaining")
    exhausted = (
        bool(budget.get("throttled"))
        or str(budget.get("exceeded", "")).lower() in ("true", "1")
        or (remaining is not None and remaining <= 0)
        or (limit and used >= limit)
    )
    if exhausted:
        print("\n!! d1_write_budget EXHAUSTED — avoid `sync`/`replay` until it resets")
        return 1
    return 0


def cmd_status(args) -> int:
    code, body = http("GET", "/status")
    print(json.dumps(body, indent=2))
    return 0 if code == 200 else 1


def cmd_tables(args) -> int:
    code, body = http("GET", "/tables")
    print(json.dumps(body, indent=2))
    return 0 if code == 200 else 1


def cmd_sync(args) -> int:
    print("NOTE: sync can hit the Cloudflare free-tier daily row-write limit.")
    print("      Check `d1_ops.py health` (d1_write_budget) first.")
    code, body = http("POST", "/sync", timeout=120.0)
    print(json.dumps(body, indent=2))
    return 0 if code == 200 else 1


def cmd_replay(args) -> int:
    code, body = http("POST", "/replay", timeout=120.0)
    print(json.dumps(body, indent=2))
    return 0 if code == 200 else 1


PK_SNIPPET = """
import asyncio, json
from app.services.d1_client import d1_client

async def main():
    rows = await d1_client.execute("PRAGMA table_info({table})")
    print("PKINFO:" + json.dumps(rows))

asyncio.run(main())
"""

# Tables known to need a PRIMARY KEY (Postgres PK -> expected D1 PK).
KNOWN_PK = {
    "post_targets": ("post_id", "social_account_id"),
}


def cmd_pk(args) -> int:
    table = args.table
    if not re.fullmatch(r"[a-zA-Z0-9_]+", table):
        print(f"ERROR: bad table name {table!r} (allowed: [a-zA-Z0-9_])")
        return 2

    snippet = PK_SNIPPET.format(table=f"{table!r}")
    res = subprocess.run(
        ["docker", "compose", "exec", "-T", "social-api", "python", "-c", snippet],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    if res.returncode != 0:
        print(res.stderr or res.stdout, file=sys.stderr)
        return 1

    rows = None
    for line in res.stdout.splitlines():
        if line.startswith("PKINFO:"):
            rows = json.loads(line[len("PKINFO:"):])
    if rows is None:
        print(f"ERROR: could not parse PRAGMA output:\n{res.stdout}\n{res.stderr}")
        return 1

    pk_cols = [r["name"] for r in rows if r.get("pk")]
    print(f"=== PRAGMA table_info({table}) ===")
    for r in rows:
        print(f"  {r.get('name'):<28}{r.get('type'):<10}pk={r.get('pk')}")

    if pk_cols:
        print(f"\nPASS — {table} PRIMARY KEY on ({', '.join(pk_cols)})")
        expected = KNOWN_PK.get(table)
        if expected and tuple(pk_cols) != expected:
            print(f"WARN — expected PK ({', '.join(expected)})")
            return 2
        return 0

    print(f"\nWARN — {table} has NO PRIMARY KEY in D1")
    if table in KNOWN_PK:
        print(f"       expected PK: ({', '.join(KNOWN_PK[table])}) — migration needed")
    else:
        print("       D1 upserts/sync may duplicate rows — add a PK migration")
    return 2


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("health"); p.set_defaults(f=cmd_health)
    p = sub.add_parser("status"); p.set_defaults(f=cmd_status)
    p = sub.add_parser("tables"); p.set_defaults(f=cmd_tables)
    p = sub.add_parser("sync"); p.set_defaults(f=cmd_sync)
    p = sub.add_parser("replay"); p.set_defaults(f=cmd_replay)
    p = sub.add_parser("pk"); p.add_argument("table"); p.set_defaults(f=cmd_pk)

    args = ap.parse_args()
    return args.f(args)


if __name__ == "__main__":
    sys.exit(main())
