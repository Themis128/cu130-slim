#!/usr/bin/env python3
# ruff: noqa: E402
"""LinkedIn sidecar session diagnostics — read-only auth/rate-limit report.

Run inside the social-api container:

    docker exec social-api python3 /app/scripts/li_session_tool.py [cmd]

Commands:
    status      sidecar /health + /session probe, rate-limit circuit state,
                pending_2fa flag, credential availability — the full picture
                session_healer sees, without triggering any recovery action.
    heal        run the hourly session_healer lane once (POST equivalent of
                the heal_sessions Celery task) and print its verdict.

Never prints cookies, tokens, or credentials.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import UTC, datetime

for path in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if path not in sys.path and os.path.isdir(os.path.join(path, "app")):
        sys.path.insert(0, path)

SIDECAR = os.getenv("LINKEDIN_BROWSER_SIDECAR_URL", "http://linkedin-browser-sidecar:9225")
PENDING_KEY = "session_healer:linkedin_pending_2fa"


async def _get(path: str) -> dict:
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{SIDECAR}{path}")
        try:
            return {"http": resp.status_code, "body": resp.json()}
        except Exception:  # noqa: BLE001
            return {"http": resp.status_code, "body": resp.text[:200]}


async def cmd_status() -> None:
    import redis.asyncio as aioredis

    from app.core.config import get_settings
    from app.services.secret_store import secret_store

    health = await _get("/health")
    session = await _get("/session")
    hb = health.get("body") or {}
    sb = session.get("body") or {}

    until = hb.get("rate_limit_until")
    rl_state = "none"
    if hb.get("rate_limited") and until:
        left = (until / 1000) - time.time()
        rl_state = (
            f"OPEN — clears {datetime.fromtimestamp(until / 1000, UTC):%Y-%m-%d %H:%MZ} "
            f"(in {left / 3600:.1f}h)"
            if left > 0
            else "stale (>6h) — healer will clear on next run"
        )

    pending = ""
    try:
        r = aioredis.from_url(get_settings().REDIS_URL, decode_responses=True)
        try:
            pending = await r.get(PENDING_KEY) or ""
        finally:
            await r.aclose()
    except Exception:  # noqa: BLE001
        pending = "redis-unreachable"

    email = bool(os.environ.get("LINKEDIN_EMAIL")) or bool(await secret_store.get("LINKEDIN_EMAIL"))
    password = bool(os.environ.get("LINKEDIN_PASSWORD")) or bool(await secret_store.get("LINKEDIN_PASSWORD"))

    print(f"sidecar /health  http={health.get('http')}  status={hb.get('status')}")
    print(f"  has_session={hb.get('has_session')}  browser_connected={hb.get('browser_connected')}  page_ok={hb.get('page_ok')}")
    print(f"  rate-limit circuit: {rl_state or 'closed'}")
    if isinstance(sb, dict):
        print(f"sidecar /session  http={session.get('http')}  logged_in={sb.get('logged_in')}  code={sb.get('code') or '-'}")
        if sb.get("error"):
            print(f"  session error: {str(sb['error'])[:160]}")
    else:
        print(f"sidecar /session  http={session.get('http')}  body={str(sb)[:120]}")
    print(f"pending_2fa checkpoint: {'YES (awaiting owner tap)' if pending else 'no'}")
    print(f"credentials (secret store): email={'yes' if email else 'NO'}  password={'yes' if password else 'NO'}")
    if rl_state.startswith("OPEN"):
        print("  → wait for the circuit to clear; healer re-checks hourly and")
        print("    auto-runs credential login once it does.")
    elif not (isinstance(sb, dict) and sb.get("logged_in")) and email and password and not pending:
        print("  → credentials present, no pending 2FA — run `li_session_tool.py heal`")


async def cmd_heal() -> None:
    from app.worker.tasks.session_healer import heal_sessions

    result = heal_sessions.apply().get(timeout=300)
    li = (result or {}).get("linkedin") or {}
    print("heal_sessions verdict:")
    print(f"  linkedin: {li}")
    for plat, verdict in (result or {}).items():
        if plat != "linkedin":
            print(f"  {plat}: {verdict}")


async def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        await cmd_status()
    elif cmd == "heal":
        await cmd_heal()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
