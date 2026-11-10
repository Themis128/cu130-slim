"""Celery task — consultation Talk rooms + meeting-notes pipeline."""

import logging

from celery import shared_task

from app.services import meeting_notes
from app.worker._async import run_async

celery_app = __import__("app.worker.celery_app", fromlist=["celery_app"]).celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)


@shared_task
def run_meeting_notes_sweep() -> dict | None:
    """Ensure upcoming consultations have Talk rooms; turn past-call
    recordings into EspoCRM meeting minutes."""
    try:
        return run_async(meeting_notes.run_meeting_notes_sweep())
    except Exception as exc:  # noqa: BLE001
        logger.warning("meeting notes sweep failed: %s", exc)
        return None
