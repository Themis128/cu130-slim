"""Deliver the free Cloud Migration Playbook to public lead-capture signups.

The public funnel form (``POST /api/v1/leads/public``) promises the playbook.
The PDF is a static asset served by the frontend
(``social-automation/frontend/public/playbooks/cloud-migration-playbook.pdf``,
source: ``docs/playbooks/cloud-migration-playbook.md``); this module sends the
submitter an email linking to it through the standard ``send_email`` path.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.core.config import get_settings
from app.services.email_digest import _html_escape, send_email

logger = logging.getLogger(__name__)

PLAYBOOK_TITLE = "The Cloud Migration Playbook"
PLAYBOOK_TASK_NAME = "app.worker.tasks.lead_emails.send_playbook_email"


def playbook_url(settings=None) -> str:
    settings = settings or get_settings()
    return (getattr(settings, "PLAYBOOK_URL", "") or "").strip()


def email_sender_configured(settings=None) -> bool:
    """True when the configured EMAIL_PROVIDER has what it needs to send."""
    settings = settings or get_settings()
    from_addr = (settings.SMTP_FROM or "").strip()
    if not from_addr:
        return False
    provider = (settings.EMAIL_PROVIDER or "local").strip().lower()
    if provider in {"cloudflare", "cf"}:
        return bool(
            (settings.CLOUDFLARE_EMAIL_API_TOKEN or "").strip()
            or (getattr(settings, "CLOUDFLARE_API_TOKEN", "") or "").strip()
        )
    if not (settings.SMTP_HOST or "").strip():
        return False
    # An authenticated relay (e.g. Resend) is useless without its password;
    # an unauthenticated local Postfix has no SMTP_USER.
    if (settings.SMTP_USER or "").strip() and not (settings.SMTP_PASSWORD or "").strip():
        return False
    return True


def playbook_delivery_enabled(settings=None) -> bool:
    settings = settings or get_settings()
    return bool(
        getattr(settings, "PLAYBOOK_EMAIL_ENABLED", True)
        and playbook_url(settings)
        and email_sender_configured(settings)
    )


def build_playbook_email(url: str) -> tuple[str, str, str]:
    """Return (subject, text_body, html_body) for the playbook email."""
    subject = f"Your copy of {PLAYBOOK_TITLE}"
    text = (
        "Hi,\n\n"
        f"Thanks for signing up. Here is your copy of {PLAYBOOK_TITLE}:\n"
        f"{url}\n\n"
        "It walks through the framework we use with clients: decide whether to "
        "migrate, inventory what runs, measure a baseline, classify each "
        "workload, choose the target architecture, plan a reversible cutover, "
        "and put cost, security and operations guardrails in place. The "
        "appendix has inventory and baseline worksheets you can fill in.\n\n"
        "Want a second pair of eyes on your setup? Book a free 30-minute audit:\n"
        "https://cloudless.gr/contact\n\n"
        "Themistoklis Baltzakis\n"
        "cloudless.gr\n\n"
        "You received this because this address was entered in the playbook "
        "form on social.cloudless.gr. If that wasn't you, ignore this email; "
        "reply to be removed from the list.\n"
    )
    u = _html_escape(url)
    html = f"""<!DOCTYPE html>
<html><body style="font-family:system-ui,-apple-system,sans-serif;line-height:1.5;color:#111;max-width:560px">
  <p>Hi,</p>
  <p>Thanks for signing up. Here is your copy of <strong>{_html_escape(PLAYBOOK_TITLE)}</strong>:</p>
  <p><a href="{u}" style="display:inline-block;background:#0891b2;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none;font-weight:600">Download the playbook (PDF)</a></p>
  <p style="font-size:13px;color:#555">Or open this link: <a href="{u}">{u}</a></p>
  <p>It walks through the framework we use with clients: decide whether to migrate,
  inventory what runs, measure a baseline, classify each workload, choose the target
  architecture, plan a reversible cutover, and put cost, security and operations
  guardrails in place. The appendix has inventory and baseline worksheets.</p>
  <p>Want a second pair of eyes on your setup?
  <a href="https://cloudless.gr/contact">Book a free 30-minute audit</a>.</p>
  <p>Themistoklis Baltzakis<br>cloudless.gr</p>
  <p style="font-size:12px;color:#777">You received this because this address was entered in the
  playbook form on social.cloudless.gr. If that wasn't you, ignore this email;
  reply to be removed from the list.</p>
</body></html>
"""
    return subject, text, html


async def send_playbook_email(to_addr: str) -> None:
    """Send the playbook email to one recipient. Raises on delivery failure."""
    settings = get_settings()
    url = playbook_url(settings)
    if not url:
        raise RuntimeError("PLAYBOOK_URL not configured")
    subject, text, html = build_playbook_email(url)
    await send_email(subject=subject, text_body=text, html_body=html, to_addrs=[to_addr])
    logger.info("playbook email sent")


def _enqueue_playbook_email(email: str) -> None:
    from app.worker.celery_app import celery_app

    celery_app.send_task(PLAYBOOK_TASK_NAME, args=[email])


async def deliver_playbook(db: Any, lead: Any, email: str, *, already_sent: bool, settings=None) -> str:
    """Decide and trigger playbook delivery for a public signup.

    Returns the ``playbook_delivery`` value reported to the form:
    ``email`` | ``already_sent`` | ``download`` | ``none``. On ``email`` the
    lead is stamped with ``meta_data.playbook_email_queued_at`` so repeat
    submissions never re-send (the public form must not become a way to mail
    arbitrary third parties repeatedly).
    """
    settings = settings or get_settings()
    if not playbook_url(settings):
        return "none"
    if already_sent:
        return "already_sent"
    if not playbook_delivery_enabled(settings):
        return "download"
    try:
        _enqueue_playbook_email(email)
    except Exception:  # noqa: BLE001 — broker down: still hand out the link
        logger.warning("Could not enqueue playbook email", exc_info=True)
        return "download"
    md = dict(lead.meta_data or {})
    md["playbook_email_queued_at"] = datetime.now(UTC).isoformat()
    lead.meta_data = md
    await db.commit()
    return "email"
