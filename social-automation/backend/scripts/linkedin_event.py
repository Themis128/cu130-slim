#!/usr/bin/env python3
"""LinkedIn Events ops — list/create org events via the Events Management API.

Usage (inside social-api):
    python3 /app/scripts/linkedin_event.py list
    python3 /app/scripts/linkedin_event.py create --name "..." \
        --start 2026-11-20T17:00:00+02:00 --end 2026-11-20T17:45:00+02:00 \
        [--desc "..."] [--url https://cloudless.gr/contact]
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime

sys.path.insert(0, "/app")


async def _main() -> int:
    from app.services import linkedin_events

    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    c = sub.add_parser("create")
    c.add_argument("--name", required=True)
    c.add_argument("--start", required=True, help="ISO datetime, e.g. 2026-11-20T17:00:00+02:00")
    c.add_argument("--end", required=True)
    c.add_argument("--desc", default="")
    c.add_argument("--url", default=None)
    args = ap.parse_args()

    if args.cmd == "list":
        out = await linkedin_events.list_org_events()
        print(json.dumps(out, indent=2, default=str)[:4000])
        return 0

    start = int(datetime.fromisoformat(args.start).timestamp() * 1000)
    end = int(datetime.fromisoformat(args.end).timestamp() * 1000)
    out = await linkedin_events.create_org_event(
        args.name, start, end, description=args.desc, event_url=args.url
    )
    print(json.dumps(out, indent=2, default=str))
    return 0 if out.get("urn") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
