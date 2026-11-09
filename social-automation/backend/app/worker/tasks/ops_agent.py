"""ops-agent Celery task — the AgentOps sweep.

Runs every 5 minutes on the default queue. Triages open ops_incidents,
runs the LLM RCA pass, applies the safety policy and escalates to the
severity-routed Telegram channels. Registered in celery_app.py.
"""

import logging

from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.worker.tasks.ops_agent.ops_agent_sweep", bind=True, max_retries=1)
def ops_agent_sweep(self):
    async def _run():
        from app.services.ops_agent import sweep

        async with task_session() as db:
            return await sweep(db)

    try:
        stats = run_async(_run())
        logger.info("ops-agent sweep: %s", stats)
        return stats
    except Exception as exc:
        logger.exception("ops-agent sweep failed")
        raise self.retry(exc=exc, countdown=120)
