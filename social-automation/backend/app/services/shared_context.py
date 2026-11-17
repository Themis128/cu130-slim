"""Shared-intent context — ACCORD/CONCORD adoption (Shift.Forward #2).

One context.json all beat tasks + skills read so survey, social, and
datalake agents act on the same intent instead of per-session journals.

    ctx = await shared_context.load()
    ctx["survey"]["goal"] = "validate Full Digital Department for SMBs"
    await shared_context.save(ctx)

Stored in the app KV/Redis (falls back to a JSON file under /app/.state).
"""

from __future__ import annotations

import json
import os
from typing import Any

import redis.asyncio as aioredis

KEY = "shared_context:v1"
FALLBACK = "/app/.state/shared_context.json"

DEFAULT: dict[str, Any] = {
    "survey": {
        "goal": "validate the Full Digital Department offer for Greek/EU SMBs",
        "campaign_id": "9f98e2ce-24da-46f3-a19f-8d7e0e875270",
        "audience": "SMB owners/operators needing an external digital/IT dept",
    },
    "offer": {
        "name": "Full Digital Department",
        "for_whom": "SMB owners without in-house digital/IT",
        "not_for": "enterprises, DIY builders, price-shoppers",
    },
    "ops": {"tier": "main=LinkedIn, secondary=Meta, last=X/TikTok"},
    "updated_at": "",
}


async def _redis() -> aioredis.Redis:
    from app.core.config import settings

    return aioredis.from_url(settings.REDIS_URL, decode_responses=True)


async def load() -> dict[str, Any]:
    try:
        r = await _redis()
        raw = await r.get(KEY)
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    try:
        with open(FALLBACK) as f:
            return json.load(f)
    except Exception:
        return dict(DEFAULT)


async def save(ctx: dict[str, Any]) -> None:
    import datetime

    ctx["updated_at"] = datetime.datetime.now(datetime.UTC).isoformat()
    raw = json.dumps(ctx)
    try:
        r = await _redis()
        await r.set(KEY, raw)
        return
    except Exception:
        pass
    # Prevent path traversal on relative paths; absolute paths are
    # operator-configured and allowed (e.g. /app/.state or test tmp dirs).
    fallback_dir = os.path.normpath(os.path.dirname(FALLBACK) or ".")
    if not os.path.isabs(fallback_dir):
        base = os.path.normpath(os.path.abspath("."))
        resolved = os.path.normpath(os.path.abspath(fallback_dir))
        if os.path.commonpath([resolved, base]) != base:
            raise ValueError(f"Invalid fallback directory: {fallback_dir}")
        fallback_dir = resolved
    os.makedirs(fallback_dir, exist_ok=True)
    with open(FALLBACK, "w") as f:
        f.write(raw)
