"""Weekly funnel audit — stage counts + weakest-link report to Slack.

Runs ``scripts/funnel_audit.py`` (the Tolstova & Silakova stage audit) as a
subprocess so the beat uses the same audited logic as the CLI, then posts the
human report to Slack. Exit-1 (a stage is broken with live upstream volume)
escalates to the alert lane instead of the digest lane.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess

from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_audit() -> tuple[int, str]:
    proc = subprocess.run(
        ["python", "scripts/funnel_audit.py"],
        capture_output=True,
        text=True,
        timeout=180,
        cwd="/app",
    )
    return proc.returncode, proc.stdout + proc.stderr


async def _post(text: str, *, alert: bool) -> None:
    try:
        from app.services.slack_notifications import post_alert_to_slack, post_digest_text_to_slack

        if alert:
            await post_alert_to_slack(text)
        else:
            await post_digest_text_to_slack(text)
    except Exception:  # noqa: BLE001
        logger.exception("funnel audit slack post failed")


@celery_app.task(name="app.worker.tasks.funnel_audit.run_funnel_audit")
def run_funnel_audit() -> dict:
    rc, out = _run_audit()
    # Keep the Slack copy compact: stage table + verdict lines only.
    lines = [ln for ln in out.splitlines() if ln.strip() and "sqlalchemy" not in ln.lower()]
    body = "\n".join(lines[-40:])
    from app.worker._async import run_async

    if rc == 0:
        run_async(_post(f":funnel_with_coins: *Weekly funnel audit*\n```\n{body}\n```", alert=False))
        return {"status": "ok", "rc": rc}
    run_async(_post(f":rotating_light: *Funnel audit* exit {rc} — broken stage detected\n```\n{body}\n```", alert=True))
    return {"status": "broken-stage", "rc": rc}
