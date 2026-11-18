"""Notebook-driven report delivery — execute a report notebook via papermill,
then send its rendered output through the existing email channel.

The notebook writes subject/html/text + attachments to a manifest; this task
is the delivery boundary so secrets stay in the worker. If the notebook run
fails, ``fallback_to_code`` re-runs the equivalent code-path report so a
brief is never silently skipped.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from celery import shared_task
from celery.result import AsyncResult

from app.worker._async import run_async
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


# Dedupe-unavailable requeue: same contract as funnel_reports — a Redis
# outage must not consume the delivery watcher's only daily resend.
_DEDUPE_RETRY_MAX = 6
_DEDUPE_RETRY_COUNTDOWN = 600  # 10 min — ~1h of outage coverage per attempt chain
_TASK_NAME = "app.worker.tasks.notebook_reports.run_notebook_report"


def _requeue_for_dedupe(notebook: str, parameters: dict | None, send: bool,
                        fallback_to_code: bool, retries: int) -> None:
    """Re-queue this report a bounded number of times when the dedupe lock
    is unavailable, so a transient Redis outage self-heals instead of
    burning the watcher's single daily resend."""
    if retries >= _DEDUPE_RETRY_MAX:
        return
    try:
        celery_app.send_task(
            _TASK_NAME,
            kwargs={
                "notebook": notebook,
                "parameters": parameters,
                "send": send,
                "fallback_to_code": fallback_to_code,
                "_dedupe_retries": retries + 1,
            },
            countdown=_DEDUPE_RETRY_COUNTDOWN,
        )
    except Exception:  # noqa: BLE001
        pass


async def _fallback_strategy(parameters: dict[str, Any], *, send: bool) -> Any:
    from app.worker.tasks.digest import _send_strategy_async

    return await _send_strategy_async(insight_days=parameters.get("insight_days", 30), send=send)


async def _fallback_monetization(parameters: dict[str, Any], *, send: bool) -> Any:
    from app.db.session import async_session_maker
    from app.services.monetization_report import run_monetization_report_for_all_teams

    async with async_session_maker() as db:
        return await run_monetization_report_for_all_teams(
            db,
            insight_days=parameters.get("insight_days", 30),
            send=send,
            execute=parameters.get("execute", True),
        )


# Notebook name → code-path equivalent. A notebook failure must never
# silently substitute the WRONG report (e.g. a strategy brief for a failed
# monetization run).
_CODE_FALLBACKS = {
    "daily_strategy_brief": _fallback_strategy,
    "monetization_daily": _fallback_monetization,
}


def _report_lock() -> Any:
    """Sync Redis client for the same-day report dedupe lock.

    Fail-closed for sends: a duplicate report email is worse than a missed
    one, so ``send=True`` skips delivery when the lock is unavailable and
    re-queues a bounded number of times (``_requeue_for_dedupe``). Renders
    (``send=False``) still proceed without it.
    """
    try:
        import redis

        from app.core.config import get_settings

        return redis.Redis.from_url(get_settings().REDIS_URL, decode_responses=True)
    except Exception:  # noqa: BLE001
        logger.warning("report dedupe lock unavailable — proceeding without it")
        return None


def _lock_owner_alive(task: Any, owner: str) -> bool:
    """True while the lock-owning task is plausibly still running.

    ``AsyncResult.state`` is a tombstone, not liveness — a worker killed
    mid-run leaves ``STARTED`` until result expiry — so a started-looking
    owner is confirmed against the workers' active+reserved sets. When
    liveness can't be verified, assume alive (never steal blind).
    """
    if AsyncResult(owner, app=task.app).state not in {"STARTED", "RETRY"}:
        return False
    try:
        insp = task.app.control.inspect(timeout=3)
        active = {t["id"] for reply in (insp.active() or {}, insp.reserved() or {}) for tasks in reply.values() for t in tasks}
    except Exception:  # noqa: BLE001
        return True
    return owner in active


@shared_task(bind=True, name="app.worker.tasks.notebook_reports.run_notebook_report")
def run_notebook_report(
    self,
    notebook: str,
    parameters: dict[str, Any] | None = None,
    *,
    send: bool = True,
    fallback_to_code: bool = True,
    _dedupe_retries: int = 0,
) -> dict[str, Any]:
    """Execute ``reports/<notebook>.ipynb`` and email the rendered report.

    Returns execution stats; on notebook failure falls back to the
    code-path strategy report so the daily brief still goes out.

    Same-day dedupe (2026-11-13): celery redelivery after a worker restart
    plus watcher resends produced 3+ concurrent runs of the same brief,
    each emailing every team report. A Redis lock now allows one run per
    (notebook, Athens date): ``running`` blocks concurrent duplicates for
    3h, ``done`` (set only after ≥1 email actually sent) blocks same-day
    reruns for 20h. Failures release ``running`` so retries stay possible.
    When Redis itself is unavailable the send path fails closed
    (``dedupe-unavailable``) and re-queues itself up to
    ``_DEDUPE_RETRY_MAX`` times, 10 min apart, so a transient outage does
    not consume the delivery watcher's only daily resend.

    The ``running`` value is the owning task id. A worker killed mid-run
    (deploy, Docker restart, OOM) can never reach the ``finally`` that
    releases it, so a new run validates the owner through the result
    backend first: an owner whose task is no longer STARTED/RETRY is dead
    and the lock is stolen instead of blocking the day's report for 3h.
    """
    from datetime import datetime
    from uuid import uuid4
    from zoneinfo import ZoneInfo

    from app.services.notebook_runner import run_report_notebook

    task_id = self.request.id or f"eager-{uuid4()}"
    r = _report_lock()
    if send and r is None:
        # Dedupe unavailable — a report email is worth less than a duplicate;
        # re-queue a bounded number of times so a Redis outage doesn't
        # consume the watcher's only daily resend.
        _requeue_for_dedupe(notebook, parameters, send, fallback_to_code, _dedupe_retries)
        return {"notebook": notebook, "executed": False, "skipped": "dedupe-unavailable"}
    day = datetime.now(ZoneInfo("Europe/Athens")).date().isoformat()
    done_key = f"nbreport:done:{notebook}:{day}"
    running_key = f"nbreport:running:{notebook}:{day}"
    if r is not None:
        try:
            if r.get(done_key):
                return {"notebook": notebook, "executed": False, "skipped": "already-delivered-today"}
            owner = r.get(running_key)
            if owner == task_id:
                # Celery redelivery (acks_late) reuses the task id — *this*
                # run already holds the lock; refresh it and proceed.
                r.set(running_key, task_id, ex=3 * 3600)
            elif owner:
                if _lock_owner_alive(self, owner):
                    return {"notebook": notebook, "executed": False, "skipped": "run-in-progress"}
                logger.warning("report lock %s held by dead task %s — stealing", running_key, owner)
                r.delete(running_key)
            if owner != task_id and not r.set(running_key, task_id, nx=True, ex=3 * 3600):
                return {"notebook": notebook, "executed": False, "skipped": "run-in-progress"}
        except Exception:  # noqa: BLE001
            logger.warning("report dedupe lock check failed — %s", "skipping send" if send else "proceeding without it")
            if send:
                _requeue_for_dedupe(notebook, parameters, send, fallback_to_code, _dedupe_retries)
                return {"notebook": notebook, "executed": False, "skipped": "dedupe-unavailable"}
            r = None

    delivered = False
    try:
        try:
            result = run_report_notebook(notebook, parameters or {})
        except Exception as exc:  # noqa: BLE001
            logger.exception("Notebook report %s failed", notebook)
            if not fallback_to_code:
                raise
            fallback = run_async(_CODE_FALLBACKS.get(notebook, _fallback_strategy)(parameters or {}, send=send))
            delivered = _fallback_delivered(fallback, send)
            return {
                "notebook": notebook,
                "executed": False,
                "error": str(exc) or repr(exc),
                "fallback": fallback,
            }

        manifest = result["manifest"]
        base_dir = Path(result["manifest_path"]).parent
        # Manifests may carry one report (flat keys) or several ({"reports":[…]})
        # — one per team.
        reports = manifest.get("reports") or ([manifest] if manifest else [])
        emailed = 0
        email_errors: list[str] = []
        if send:
            for rep in reports:
                ok, err = run_async(_deliver_manifest(rep, base_dir))
                emailed += int(ok)
                if err:
                    email_errors.append(err)
        delivered = bool(send and emailed > 0)

        return {
            "notebook": notebook,
            "executed": True,
            "duration_s": result["duration_s"],
            "artifact": result["notebook"],
            "emailed": emailed,
            "email_error": "; ".join(email_errors) or None,
        }
    finally:
        if r is not None:
            try:
                if delivered:
                    r.set(done_key, "1", ex=20 * 3600)
                # Only release the lock if it is still ours — a presumed-dead
                # run that resumes must not delete a new owner's lock.
                if r.get(running_key) == task_id:
                    r.delete(running_key)
            except Exception:  # noqa: BLE001
                pass


def _fallback_delivered(fallback: Any, send: bool) -> bool:
    """Did the code-path fallback actually email? ``delivered`` gates the
    same-day done-key — marking it on a failed send would suppress the
    watcher resend and lose the day's report."""
    if not send:
        return False
    if isinstance(fallback, dict):
        return bool(fallback.get("emailed"))
    if isinstance(fallback, list):
        return any(isinstance(r, dict) and r.get("emailed") for r in fallback)
    return True  # unknown shape — assume delivered rather than risk a resend duplicate


async def _deliver_manifest(manifest: dict[str, Any], base_dir: Path) -> tuple[bool, str | None]:
    """Send the notebook's rendered report through the standard email path."""
    from app.services.email_digest import send_email

    def _resolve(f: str | None) -> Path | None:
        if not f:
            return None
        p = Path(f)
        return p if p.is_absolute() else base_dir / p

    html_file = _resolve(manifest.get("html_file"))
    text_file = _resolve(manifest.get("text_file"))
    if not html_file or not text_file:
        return False, "manifest missing html_file/text_file"
    try:
        attachments = [
            {
                "name": a["file"],
                "data": (Path(a["file"]) if Path(a["file"]).is_absolute() else base_dir / a["file"]).read_bytes(),
                "cid": a.get("cid"),
                "mime": a.get("mime"),
            }
            for a in manifest.get("attachments") or []
        ]
        await send_email(
            subject=manifest.get("subject") or "SocialAuto report",
            text_body=Path(text_file).read_text(),
            html_body=Path(html_file).read_text(),
            attachments=attachments or None,
        )
        return True, None
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to send notebook report")
        return False, str(exc) or repr(exc)
