"""Celery task — self-healing session sweep across all browser transports.

Runs hourly. Probes the LinkedIn/Facebook sidecars and the shared
browser-novnc bridge, attempts the cheapest recovery path per platform
(cookie re-inject, credential login, Threads IG-SSO bootstrap), persists
fresh session material to Postgres, and Slack-alerts only when human
action is required. See ``app.services.session_healer`` for the ladder.
"""
import logging

from celery import shared_task

from app.worker._async import run_async
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)


@shared_task(
    name="app.worker.tasks.session_healer.heal_sessions",
    soft_time_limit=1500,
    time_limit=1700,
)
def heal_sessions() -> dict:
    """Periodic task — probe and heal all browser sessions (hourly)."""
    from app.services.session_healer import heal_all_sessions

    return run_async(heal_all_sessions())
