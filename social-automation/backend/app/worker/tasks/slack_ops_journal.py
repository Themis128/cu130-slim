"""Celery task — daily Slack ops journal → Nextcloud Notes + company brain."""

import logging
from typing import Any

from celery import shared_task

from app.services import slack_ops_journal
from app.worker._async import run_async

logger = logging.getLogger(__name__)


@shared_task(name="app.worker.tasks.slack_ops_journal.slack_ops_journal_task", bind=True, max_retries=1)
def slack_ops_journal_task(self) -> dict[str, Any]:
    if not slack_ops_journal.slack_ops_journal_enabled():
        return {"skipped": "SLACK_BOT_TOKEN not set"}
    try:
        return run_async(slack_ops_journal.run_journal())
    except Exception as exc:
        logger.warning("slack ops journal failed: %s", exc)
        return {"error": str(exc)[:200]}
