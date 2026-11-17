"""Celery tasks: funnel reports — lead pulse, attribution, content weekly,
funnel monthly. Same-day dedupe via Redis so watcher resends can't double-mail.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

from app.worker._async import run_async
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()


def _dedupe_lock(key: str) -> Any:
    """Redis same-day lock — one delivery per (report, Athens date)."""
    try:
        import redis

        from app.core.config import get_settings

        r = redis.Redis.from_url(get_settings().REDIS_URL, decode_responses=True)
        from datetime import datetime
        from zoneinfo import ZoneInfo

        day = datetime.now(ZoneInfo("Europe/Athens")).date().isoformat()
        k = f"funnelreport:sent:{key}:{day}"
        return r, k
    except Exception:  # noqa: BLE001
        return None, None


async def _run_report(key: str, send: bool) -> dict[str, Any]:
    from app.services.funnel_reports import BUILDERS, _send

    report = await BUILDERS[key]()
    emailed = False
    if send:
        report = await _send(report)
        emailed = report.emailed
    return {
        "report": key,
        "emailed": emailed,
        "email_error": report.email_error,
        "sections": len(report.sections),
    }


def _make_task(name: str, key: str):
    @shared_task(name=name)
    def _task(send: bool = True) -> dict[str, Any]:
        r, lock_key = _dedupe_lock(key)
        if r is not None and lock_key:
            try:
                if r.get(lock_key):
                    return {"report": key, "skipped": "already-delivered-today"}
                r.set(lock_key, "1", ex=20 * 3600, nx=True)
            except Exception:  # noqa: BLE001
                r = None
        try:
            result = run_async(_run_report(key, send))
            if not (send and result.get("emailed")) and r is not None and lock_key:
                try:
                    r.delete(lock_key)
                except Exception:  # noqa: BLE001
                    pass
            return result
        except Exception:
            if r is not None and lock_key:
                try:
                    r.delete(lock_key)
                except Exception:  # noqa: BLE001
                    pass
            raise

    return _task


run_lead_pulse = _make_task(
    "app.worker.tasks.funnel_reports.run_lead_pulse", "lead_pulse")
run_attribution_digest = _make_task(
    "app.worker.tasks.funnel_reports.run_attribution_digest", "attribution")
run_content_weekly = _make_task(
    "app.worker.tasks.funnel_reports.run_content_weekly", "content_weekly")
run_funnel_monthly = _make_task(
    "app.worker.tasks.funnel_reports.run_funnel_monthly", "funnel_monthly")
