"""Instagram OAuth re-auth lane — scheduled runner for scripts/instagram_reauth.py.

The script drives the facebook-browser-sidecar logged-in session through the
Facebook-Login authorize flow; this task schedules it and escalates human
gates (login wall / 2FA / CAPTCHA → exit 3) to Slack. The script
self-throttles (1 attempt/hour, 3 consecutive failures → once/day), so an
hourly beat is safe.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from app.worker._async import run_async
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "instagram_reauth.py"


async def _alert(text: str) -> None:
    try:
        from app.services.slack_notifications import post_alert_to_slack  # noqa: PLC0415

        await post_alert_to_slack(text)
    except Exception:  # noqa: BLE001
        logger.exception("instagram reauth slack alert failed")


@celery_app.task(name="app.worker.tasks.instagram_reauth.run_instagram_reauth", bind=True, max_retries=0)
def run_instagram_reauth(self) -> dict:
    if not _SCRIPT.exists():
        logger.warning("instagram_reauth script missing at %s", _SCRIPT)
        return {"status": "missing-script"}
    try:
        proc = subprocess.run(  # noqa: S603
            ["python", str(_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=str(_SCRIPT.parents[1]),
        )
    except subprocess.TimeoutExpired:
        logger.warning("instagram_reauth timed out")
        return {"status": "timeout"}
    out = (proc.stdout + proc.stderr).strip()
    logger.info("instagram_reauth rc=%s: %s", proc.returncode, out[-500:])
    if proc.returncode == 3:
        run_async(_alert(":lock: Instagram re-auth needs a human — FB sidecar session is logged out or hit a gate (2FA/CAPTCHA). See logs/instagram_reauth."))
    elif proc.returncode not in (0, 2):
        run_async(_alert(f":warning: Instagram re-auth attempt failed (rc={proc.returncode}): {out[-200:]}"))
    return {"status": "ok" if proc.returncode == 0 else f"rc{proc.returncode}", "tail": out[-300:]}
