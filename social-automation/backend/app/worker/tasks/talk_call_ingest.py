"""Celery task: Talk call-event ingest → analytics_events → datalake."""

import logging
from typing import Any

from celery import shared_task

from app.services.talk_calls import ingest_call_events
from app.worker._async import run_async

logger = logging.getLogger(__name__)


@shared_task(name="app.worker.tasks.talk_call_ingest.talk_call_ingest")
def talk_call_ingest() -> dict[str, Any]:
    return run_async(ingest_call_events())
