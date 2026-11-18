"""Celery tasks: funnel reports — lead pulse, attribution, content weekly,
funnel monthly. Same-day dedupe via Redis so watcher resends can't double-mail.
"""

from __future__ import annotations

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


def _execute(key: str, send: bool) -> dict[str, Any]:
    r, lock_key = _dedupe_lock(key)
    claimed = False
    if r is not None and lock_key:
        try:
            # Atomic claim — SET NX returns falsy when the day key already
            # exists. The old get-then-set pair raced: two executions started
            # together both passed and double-mailed (2026-11-18).
            claimed = bool(r.set(lock_key, "1", ex=20 * 3600, nx=True))
        except Exception:  # noqa: BLE001
            r = None
    if not claimed:
        if r is not None and lock_key:
            return {"report": key, "skipped": "already-delivered-today"}
        if send:
            # Dedupe unavailable — never risk a duplicate email; the
            # delivery watcher resends once Redis recovers.
            return {"report": key, "skipped": "dedupe-unavailable"}
    try:
        result = run_async(_run_report(key, send))
        if not (send and result.get("emailed")) and claimed:
            try:
                r.delete(lock_key)
            except Exception:  # noqa: BLE001
                pass
        return result
    except Exception:
        if claimed:
            try:
                r.delete(lock_key)
            except Exception:  # noqa: BLE001
                pass
        raise


@shared_task(name="app.worker.tasks.funnel_reports.run_lead_pulse")
def run_lead_pulse(send: bool = True) -> dict[str, Any]:
    return _execute("lead_pulse", send)


@shared_task(name="app.worker.tasks.funnel_reports.run_attribution_digest")
def run_attribution_digest(send: bool = True) -> dict[str, Any]:
    return _execute("attribution", send)


@shared_task(name="app.worker.tasks.funnel_reports.run_content_weekly")
def run_content_weekly(send: bool = True) -> dict[str, Any]:
    return _execute("content_weekly", send)


@shared_task(name="app.worker.tasks.funnel_reports.run_funnel_monthly")
def run_funnel_monthly(send: bool = True) -> dict[str, Any]:
    return _execute("funnel_monthly", send)
