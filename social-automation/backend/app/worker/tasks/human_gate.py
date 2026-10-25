"""Celery tasks — human-gate watcher.

Polls the LinkedIn + Meta sidecars for blocked checkpoints (pending_2fa,
account restriction) and nudges the ops channel — at most once per 6h per
gate (Redis dedup), and a clear notice the moment a gate opens. Replaces the
nohup human-gate-ops watchers with a governed beat worker.
"""

import logging
import time

import httpx
from celery import shared_task

from app.worker._async import run_async

celery_app = __import__("app.worker.celery_app", fromlist=["celery_app"]).celery_app
celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)

_NUDGE_SECONDS = 6 * 3600
_GATES = (
    ("linkedin-2fa", "http://linkedin-sidecar:5000/session",
     ":warning: LinkedIn 2FA still pending — approve the push in the app (#794)"),
    ("meta-status", "http://facebook-sidecar:9226/session/validate",
     ":warning: Meta sidecar shows a blocked/restricted session — Accounts Center needs the owner (#785)"),
)


async def _watch() -> dict:
    import redis.asyncio as aioredis

    from app.core.config import get_settings
    from app.services.slack_notifications import post_alert_to_slack

    out: dict[str, str] = {}
    r = aioredis.from_url(get_settings().REDIS_URL)
    async with httpx.AsyncClient(timeout=10) as http:
        for name, url, nudge in _GATES:
            try:
                resp = await http.get(url)
                data = resp.json()
            except Exception as exc:  # noqa: BLE001 — asleep/unreachable is normal
                out[name] = f"unreachable ({exc.__class__.__name__})"
                continue
            state = str(data.get("status") or data.get("checkpoint") or data.get("logged_in"))
            blocked = "pending" in state.lower() or "restrict" in state.lower() or state == "False"
            key = f"humangate:nudge:{name}"
            if blocked:
                last = float(await r.get(key) or 0)
                if time.time() - last > _NUDGE_SECONDS:
                    await post_alert_to_slack(nudge)
                    await r.set(key, str(time.time()))
                    out[name] = "nudged"
                else:
                    out[name] = "blocked (recently nudged)"
            else:
                if await r.get(key):
                    await r.delete(key)
                    await post_alert_to_slack(f":white_check_mark: {name} cleared — gate open")
                    out[name] = "cleared"
                else:
                    out[name] = "ok"
    return out


@shared_task
def run_human_gate_watch() -> dict | None:
    """Every 30 min — surface blocked human gates to ops (6h nudge dedup)."""
    from app.core.config import get_settings

    if not get_settings().AGENCY_ENABLED:
        return None
    try:
        return run_async(_watch())
    except Exception as exc:  # noqa: BLE001
        logger.warning("human-gate watch failed: %s", exc)
        return None
