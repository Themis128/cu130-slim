"""Meta ad-infra retry — scheduled runner for scripts/meta_infra.py.

Daily 10:00 UTC (12:00 EET). Runs the idempotent provisioning chain
(system user -> non-expiring token -> WABA assign -> ad account -> appeals).
The script exits rc=3 while the personal-account restriction gates Meta
mutations (OAuthException subcode 2859015); that state is silent — the
id/hub identity doc is the only unblock. Any other non-zero is alerted.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from app.worker._async import run_async
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "meta_infra.py"


async def _alert(text: str) -> None:
    try:
        from app.services.slack_notifications import post_alert_to_slack  # noqa: PLC0415

        await post_alert_to_slack(text)
    except Exception:  # noqa: BLE001
        logger.exception("meta_infra slack alert failed")


@celery_app.task(name="app.worker.tasks.meta_infra_retry.run_meta_infra_retry")
def run_meta_infra_retry() -> dict:
    try:
        proc = subprocess.run(
            ["python", str(_SCRIPT), "chain"],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=str(_SCRIPT.parents[1]),
        )
    except subprocess.TimeoutExpired:
        logger.warning("meta_infra chain timed out")
        return {"status": "timeout"}
    out = (proc.stdout + proc.stderr).strip()
    logger.info("meta_infra chain rc=%s: %s", proc.returncode, out[-500:])
    if proc.returncode == 0:
        run_async(_alert(":white_check_mark: Meta infra chain completed — system user / WABA / ad account provisioned. See uploads/meta-infra-log.jsonl"))
    elif proc.returncode == 3:
        pass  # restriction still on — silent, human gate (id/hub) owns it
    else:
        run_async(_alert(f":warning: Meta infra chain failed (rc={proc.returncode}): {out[-200:]}"))
    return {"status": "ok" if proc.returncode == 0 else f"rc{proc.returncode}", "tail": out[-300:]}
