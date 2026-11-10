"""Agent-workforce audit Celery task.

Runs scripts/workforce_audit.py's audit() on a beat cadence and pushes the
attention list (dark/escalation/ungoverned workers + autonomy proposals) to
Slack — the paper's human-intervention lane, automated. CLI remains for
manual --json/--governance inspection.
"""

import logging

from app.worker._async import run_async
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.worker.tasks.workforce_audit.run_workforce_audit", bind=True, max_retries=0)
def run_workforce_audit(self, notify: bool = True):
    async def _run():
        import scripts.workforce_audit as WA

        report = await WA.audit()
        counts = {"ok": 0, "idle": 0, "dark": 0, "escalation": 0, "ungoverned": 0, "unverified": 0}
        for w in report["workers"]:
            counts[w["status"]] = counts.get(w["status"], 0) + 1
        if notify:
            try:
                await WA.push_slack(report)
            except Exception:
                logger.warning("workforce audit slack push failed", exc_info=True)
        bad = sum(1 for w in report["workers"] if w["status"] in ("dark", "escalation", "ungoverned"))
        return {
            "workers": len(report["workers"]),
            **counts,
            "attention": bad,
            "proposals": len(report.get("autonomy_proposals") or []),
        }

    stats = run_async(_run())
    logger.info("workforce audit: %s", stats)
    return stats
