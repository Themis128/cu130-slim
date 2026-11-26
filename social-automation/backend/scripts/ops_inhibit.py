#!/usr/bin/env python3
# ruff: noqa: E402
"""ops_inhibit.py — declare/clear alert-inhibition maintenance windows.

While a window is active, the ops-agent suppresses non-critical Telegram
alerts (criticals always fire) — cascade prevention during deliberate
maintenance. Incidents stay visible in the pinned live-state message, and
the alert gate still stamps so lifting the window does not replay the flood.

    docker exec social-api python3 /app/scripts/ops_inhibit.py --hours 2
    docker exec social-api python3 /app/scripts/ops_inhibit.py --clear
    docker exec social-api python3 /app/scripts/ops_inhibit.py --status
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

sys.path.insert(0, "/app")

KEY = "ops:inhibited-until"


def _redis():
    import redis

    from app.core.config import get_settings

    return redis.from_url(get_settings().REDIS_URL, decode_responses=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=0, help="inhibit non-critical alerts for N hours from now")
    ap.add_argument("--clear", action="store_true", help="lift the window immediately")
    ap.add_argument("--status", action="store_true", help="show current window state")
    args = ap.parse_args()

    r = _redis()
    now = datetime.now(UTC).timestamp()

    if args.clear:
        r.delete(KEY)
        print("inhibition window cleared")
        return 0

    if args.hours > 0:
        until = now + args.hours * 3600
        r.set(KEY, str(until))
        print(f"inhibited until {datetime.fromtimestamp(until, UTC):%Y-%m-%d %H:%M}Z ({args.hours}h) — non-critical alerts suppressed")
        return 0

    # default: status
    val = r.get(KEY)
    if val and float(val) > now:
        remaining = (float(val) - now) / 3600
        print(f"INHIBITED until {datetime.fromtimestamp(float(val), UTC):%Y-%m-%d %H:%M}Z ({remaining:.1f}h left)")
        return 0
    print("no active inhibition window")
    return 0


if __name__ == "__main__":
    sys.exit(main())
