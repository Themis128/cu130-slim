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

from app.worker._async import run_async

logger = logging.getLogger(__name__)


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

    Fail-open: a Redis outage must never block report delivery.
    """
    try:
        import redis

        from app.core.config import get_settings

        return redis.Redis.from_url(get_settings().REDIS_URL, decode_responses=True)
    except Exception:  # noqa: BLE001
        logger.warning("report dedupe lock unavailable — proceeding without it")
        return None


@shared_task(name="app.worker.tasks.notebook_reports.run_notebook_report")
def run_notebook_report(
    notebook: str,
    parameters: dict[str, Any] | None = None,
    *,
    send: bool = True,
    fallback_to_code: bool = True,
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
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.services.notebook_runner import run_report_notebook

    r = _report_lock()
    day = datetime.now(ZoneInfo("Europe/Athens")).date().isoformat()
    done_key = f"nbreport:done:{notebook}:{day}"
    running_key = f"nbreport:running:{notebook}:{day}"
    if r is not None:
        try:
            if r.get(done_key):
                return {"notebook": notebook, "executed": False, "skipped": "already-delivered-today"}
            if not r.set(running_key, "1", nx=True, ex=3 * 3600):
                return {"notebook": notebook, "executed": False, "skipped": "run-in-progress"}
        except Exception:  # noqa: BLE001
            logger.warning("report dedupe lock check failed — proceeding")
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
            delivered = bool(send)
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
                r.delete(running_key)
            except Exception:  # noqa: BLE001
                pass


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
