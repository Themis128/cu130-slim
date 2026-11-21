"""Celery task — daily LinkedIn profile invite outreach (5/day).

Scrapes people-search results on the LinkedIn sidecar for
SocialAuto-relevant keywords and sends a small batch of connection
invites via the voyager /connect endpoint. See
app.services.linkedin_invite_outreach for the safety contract.
"""

import logging

from app.worker._async import run_async
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)


@celery_app.task(name="app.worker.tasks.linkedin_invite_outreach.send_profile_invites")
def send_profile_invites(count: int = 5) -> dict:
    from app.services.linkedin_invite_outreach import send_profile_invites as _send

    res = run_async(_send(count))
    logger.info("linkedin_invite_outreach: %s", res)
    return res
