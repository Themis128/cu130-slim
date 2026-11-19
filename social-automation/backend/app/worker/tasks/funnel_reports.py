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

# Dedupe-unavailable requeue: a Redis outage must not silently consume the
# delivery watcher's only daily resend — re-queue the same report a bounded
# number of times so it self-heals once Redis recovers.
_DEDUPE_RETRY_MAX = 6
_DEDUPE_RETRY_COUNTDOWN = 600  # 10 min — ~1h of outage coverage per attempt chain

# Durable outbox on the shared uploads bind mount: celery's broker IS
# Redis, so when Redis is down send_task() itself fails and in-memory
# requeues die with the worker. Records here survive restarts; _execute
# drains them once the broker is reachable again.
_OUTBOX_DIR = "/app/uploads/.report-outbox"
_OUTBOX_MAX = _DEDUPE_RETRY_MAX


def _outbox_write(key: str, send: bool, retries: int) -> None:
    import json
    import os

    try:
        os.makedirs(_OUTBOX_DIR, exist_ok=True)
        tmp = os.path.join(_OUTBOX_DIR, f"{key}.{os.getpid()}.tmp")
        with open(tmp, "w") as f:
            json.dump({"key": key, "send": send, "retries": retries}, f)
        os.replace(tmp, os.path.join(_OUTBOX_DIR, f"{key}.json"))
    except Exception:  # noqa: BLE001 — outbox is best-effort; never break delivery
        pass


def _outbox_drain() -> None:
    """Re-enqueue outbox records once the broker accepts tasks again."""
    import glob
    import json
    import os

    for path in glob.glob(os.path.join(_OUTBOX_DIR, "*.json")):
        try:
            rec = json.load(open(path))
            key, send, retries = rec["key"], bool(rec.get("send")), int(rec.get("retries", 0))
        except Exception:  # noqa: BLE001
            continue
        if key not in _TASK_NAMES or retries >= _OUTBOX_MAX:
            try:
                os.unlink(path)
            except OSError:
                pass
            continue
        try:
            celery_app.send_task(
                _TASK_NAMES[key],
                kwargs={"send": send, "_dedupe_retries": retries + 1},
                countdown=_DEDUPE_RETRY_COUNTDOWN,
            )
            os.unlink(path)
        except Exception:  # noqa: BLE001 — broker still down; keep the record
            pass


_TASK_NAMES = {
    "lead_pulse": "app.worker.tasks.funnel_reports.run_lead_pulse",
    "attribution": "app.worker.tasks.funnel_reports.run_attribution_digest",
    "content_weekly": "app.worker.tasks.funnel_reports.run_content_weekly",
    "funnel_monthly": "app.worker.tasks.funnel_reports.run_funnel_monthly",
}


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


def _execute(key: str, send: bool, _dedupe_retries: int = 0) -> dict[str, Any]:
    _outbox_drain()
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
            # Dedupe unavailable — never risk a duplicate email, but don't
            # let a transient Redis outage consume the watcher's only daily
            # resend either: re-queue a bounded number of times.
            # Persist before requeueing: if the broker is down too, the
            # outbox record is the only thing that retains this delivery.
            _outbox_write(key, send, _dedupe_retries + 1)
            if _dedupe_retries < _DEDUPE_RETRY_MAX:
                try:
                    celery_app.send_task(
                        _TASK_NAMES[key],
                        kwargs={"send": send, "_dedupe_retries": _dedupe_retries + 1},
                        countdown=_DEDUPE_RETRY_COUNTDOWN,
                    )
                except Exception:  # noqa: BLE001
                    pass
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
def run_lead_pulse(send: bool = True, _dedupe_retries: int = 0) -> dict[str, Any]:
    return _execute("lead_pulse", send, _dedupe_retries)


@shared_task(name="app.worker.tasks.funnel_reports.run_attribution_digest")
def run_attribution_digest(send: bool = True, _dedupe_retries: int = 0) -> dict[str, Any]:
    return _execute("attribution", send, _dedupe_retries)


@shared_task(name="app.worker.tasks.funnel_reports.run_content_weekly")
def run_content_weekly(send: bool = True, _dedupe_retries: int = 0) -> dict[str, Any]:
    return _execute("content_weekly", send, _dedupe_retries)


@shared_task(name="app.worker.tasks.funnel_reports.run_funnel_monthly")
def run_funnel_monthly(send: bool = True, _dedupe_retries: int = 0) -> dict[str, Any]:
    return _execute("funnel_monthly", send, _dedupe_retries)
