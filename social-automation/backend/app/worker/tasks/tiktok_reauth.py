"""TikTok OAuth re-auth lane — scheduled runner for scripts/tiktok_reauth.py.

The script drives the tiktok-browser-sidecar logged-in session through the
PKCE authorize flow; this task just schedules it and escalates human gates
(login wall / 2FA / CAPTCHA → exit 3) to Slack. The script self-throttles
(1 attempt/hour, 3 consecutive failures → once/day), so an hourly beat is
safe.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from app.worker._async import run_async
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "tiktok_reauth.py"


async def _alert(text: str) -> None:
    try:
        from app.services.slack_notifications import post_alert_to_slack  # noqa: PLC0415

        await post_alert_to_slack(text)
    except Exception:  # noqa: BLE001
        logger.exception("tiktok reauth slack alert failed")


@celery_app.task(name="app.worker.tasks.tiktok_reauth.run_tiktok_reauth", bind=True, max_retries=0)
def run_tiktok_reauth(self) -> dict:
    if not _SCRIPT.exists():
        logger.warning("tiktok_reauth script missing at %s", _SCRIPT)
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
        logger.warning("tiktok_reauth timed out")
        return {"status": "timeout"}
    out = (proc.stdout + proc.stderr).strip()
    logger.info("tiktok_reauth rc=%s: %s", proc.returncode, out[-500:])
    if proc.returncode == 3:
        run_async(_alert(":lock: TikTok re-auth hit a login wall / 2FA — human gate needed in tiktok-browser-sidecar session"))
    elif proc.returncode not in (0, 2):
        run_async(_alert(f":warning: TikTok re-auth failed (rc={proc.returncode}): {out[-200:]}"))
    return {"status": "ok", "rc": proc.returncode}
