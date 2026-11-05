"""Token expiry sweep — proactive refresh-ahead with reauth escalation.

Runs ``scripts/token_expiry_sweep.py`` so the beat uses the same logic as
the CLI. The script posts its own Slack report when there is something to
say; the task just returns the parsed report for observability.
"""

from __future__ import annotations

import json
import logging
import subprocess

from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.worker.tasks.token_expiry.run_token_expiry_sweep", bind=True, max_retries=0)
def run_token_expiry_sweep(self) -> dict:
    proc = subprocess.run(
        ["python", "scripts/token_expiry_sweep.py"],
        capture_output=True,
        text=True,
        timeout=300,
        cwd="/app",
    )
    out = proc.stdout.strip()
    try:
        report = json.loads(out.splitlines()[0] if out else "{}") if out.startswith("{") else {}
    except Exception:
        report = {}
    if proc.returncode != 0:
        logger.error("token expiry sweep failed rc=%s: %s", proc.returncode, out[-500:])
        return {"status": "error", "rc": proc.returncode, "tail": out[-500:]}
    logger.info("token expiry sweep: %s", report or out[:200])
    return {"status": "ok", **report}
