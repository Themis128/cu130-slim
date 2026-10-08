"""Transactional emails for inbound leads (public funnel)."""
from __future__ import annotations

import logging

from app.worker._async import run_async
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.worker.tasks.lead_emails.send_playbook_email",
    bind=True,
    max_retries=3,
    default_retry_delay=120,
)
def send_playbook_email(self, email: str) -> dict:
    """Email the Cloud Migration Playbook link to a public signup."""
    from app.services.playbook_email import send_playbook_email as _send

    try:
        run_async(_send(email))
    except Exception as exc:  # noqa: BLE001 — SMTP/network errors are transient
        logger.warning("playbook email failed (attempt %s): %s", self.request.retries + 1, exc)
        raise self.retry(exc=exc) from exc
    return {"sent": True}
