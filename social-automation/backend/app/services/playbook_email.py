"""Deliver the free Cloud Migration Playbook to public lead-capture signups.

The public funnel form (``POST /api/v1/leads/public``) promises the playbook.
The PDF (source: ``docs/playbooks/cloud-migration-playbook.md``) is committed
as ``social-automation/frontend/public/playbooks/cloud-migration-playbook.pdf``
and mirrored on the public site at ``PUBLIC_PLAYBOOK_URL`` (the default
``PLAYBOOK_URL``), because the SocialAuto frontend host is behind Cloudflare
Access. This module sends the submitter an email linking to it through the
standard ``send_email`` path.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any

from app.core.config import get_settings
from app.services.email_digest import _html_escape, send_email

logger = logging.getLogger(__name__)

PLAYBOOK_TITLE = "The Cloud Migration Playbook"
PLAYBOOK_TASK_NAME = "app.worker.tasks.lead_emails.send_playbook_email"
_BUTTON_STYLE = "display:inline-block;background:#0891b2;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none;font-weight:600"
_OUTLINE_BUTTON_STYLE = (
    "display:inline-block;background:#fff;color:#0891b2;padding:10px 18px;border:2px solid #0891b2;border-radius:6px;text-decoration:none;font-weight:600"
)


PLAYBOOK_PATH = "/playbooks/cloud-migration-playbook.pdf"
PUBLIC_PLAYBOOK_URL = f"https://cloudless.gr{PLAYBOOK_PATH}"


def playbook_url(settings=None) -> str:
    """PLAYBOOK_URL (defaults to the public cloudless.gr copy), else FRONTEND_URL's PDF."""
    settings = settings or get_settings()
    explicit = (getattr(settings, "PLAYBOOK_URL", "") or "").strip()
    if explicit:
        return explicit
    frontend = (getattr(settings, "FRONTEND_URL", "") or "").strip().rstrip("/")
    return f"{frontend}{PLAYBOOK_PATH}" if frontend else ""


def email_sender_configured(settings=None) -> bool:
    """True when the configured EMAIL_PROVIDER has what it needs to send."""
    settings = settings or get_settings()
    from_addr = (settings.SMTP_FROM or "").strip()
    if not from_addr:
        return False
    provider = (settings.EMAIL_PROVIDER or "local").strip().lower()
    if provider in {"cloudflare", "cf"}:
        return bool((settings.CLOUDFLARE_EMAIL_API_TOKEN or "").strip() or (getattr(settings, "CLOUDFLARE_API_TOKEN", "") or "").strip())
    if not (settings.SMTP_HOST or "").strip():
        return False
    # An authenticated relay (e.g. Resend) is useless without its password;
    # an unauthenticated local Postfix has no SMTP_USER.
    if (settings.SMTP_USER or "").strip() and not (settings.SMTP_PASSWORD or "").strip():
        return False
    return True


def playbook_delivery_enabled(settings=None) -> bool:
    settings = settings or get_settings()
    return bool(getattr(settings, "PLAYBOOK_EMAIL_ENABLED", True) and playbook_url(settings) and email_sender_configured(settings))


def _utm(url: str, content: str) -> str:
    """Tag an outbound link for funnel attribution in web analytics."""
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}utm_source=playbook-email&utm_medium=email&utm_campaign=cloud-migration-playbook&utm_content={content}"


def _first_name(name: str | None) -> str:
    return (name or "").strip().split(" ", 1)[0].strip()


def build_playbook_email(url: str, name: str | None = None) -> tuple[str, str, str]:
    """Return (subject, text_body, html_body) for the playbook email."""
    first = _first_name(name)
    greeting = f"Hi {first}," if first else "Hi,"
    download_url = _utm(url, "download")
    audit_url = _utm("https://cloudless.gr/contact", "audit-cta")
    subject = f"Your copy of {PLAYBOOK_TITLE}"
    text = (
        f"{greeting}\n\n"
        f"Thanks for signing up. Here is your copy of {PLAYBOOK_TITLE}:\n"
        f"{download_url}\n\n"
        "It walks through the framework we use with clients: decide whether to "
        "migrate, inventory what runs, measure a baseline, classify each "
        "workload, choose the target architecture, plan a reversible cutover, "
        "and put cost, security and operations guardrails in place. The "
        "appendix has inventory and baseline worksheets you can fill in.\n\n"
        "Want a second pair of eyes? Book a free 30-minute audit — we review\n"
        "your inventory and baseline (Steps 1 and 2), tell you which of the five\n"
        "paths each workload should take, and hand you a written action plan,\n"
        "whether or not you ever hire us. Fill in the worksheets and bring them:\n"
        f"{audit_url}\n\n"
        "Themistoklis Baltzakis\n"
        "cloudless.gr\n\n"
        "You received this because this address was entered in the playbook "
        "form on social.cloudless.gr. If that wasn't you, ignore this email; "
        "reply to be removed from the list.\n"
    )
    u = _html_escape(download_url)
    au = _html_escape(audit_url)
    preheader = _html_escape("Your Cloud Migration Playbook is attached — plus a free 30-minute audit offer.")
    html = f"""<!DOCTYPE html>
<html><body style="font-family:system-ui,-apple-system,sans-serif;line-height:1.5;color:#111;max-width:560px">
  <div style="display:none;max-height:0;overflow:hidden;mso-hide:all">{preheader}</div>
  <p>{_html_escape(greeting)}</p>
  <p>Thanks for signing up. Here is your copy of <strong>{_html_escape(PLAYBOOK_TITLE)}</strong>:</p>
  <p><a href="{u}" style="{_BUTTON_STYLE}">Download the playbook (PDF)</a></p>
  <p style="font-size:13px;color:#555">Or open this link: <a href="{u}">{u}</a></p>
  <p>It walks through the framework we use with clients: decide whether to migrate,
  inventory what runs, measure a baseline, classify each workload, choose the target
  architecture, plan a reversible cutover, and put cost, security and operations
  guardrails in place. The appendix has inventory and baseline worksheets.</p>
  <p><strong>Want a second pair of eyes?</strong> Book a free 30-minute audit — we review
  your inventory and baseline (Steps 1 and 2 of the playbook), tell you which of the
  five paths each workload should take, and hand you a written action plan, whether
  or not you ever hire us. Fill in the worksheets and bring them.</p>
  <p><a href="{au}" style="{_OUTLINE_BUTTON_STYLE}">Book the free audit</a></p>
  <p>Themistoklis Baltzakis<br>cloudless.gr</p>
  <p style="font-size:12px;color:#777">You received this because this address was entered in the
  playbook form on social.cloudless.gr. If that wasn't you, ignore this email;
  reply to be removed from the list.</p>
</body></html>
"""
    return subject, text, html


def playbook_unsubscribe_mailto() -> str:
    """mailto: unsubscribe for the List-Unsubscribe header and footer link."""
    from urllib.parse import quote

    return f"mailto:tbaltzakis@cloudless.gr?subject={quote('unsubscribe playbook list')}"


async def send_playbook_email(to_addr: str, name: str | None = None) -> None:
    """Send the playbook email to one recipient. Raises on delivery failure."""
    settings = get_settings()
    url = playbook_url(settings)
    if not url:
        raise RuntimeError("PLAYBOOK_URL not configured")
    subject, text, html = build_playbook_email(url, name=name)
    await send_email(
        subject=subject,
        text_body=text,
        html_body=html,
        to_addrs=[to_addr],
        extra_headers={
            "List-Unsubscribe": f"<{playbook_unsubscribe_mailto()}>",
        },
        reply_to="tbaltzakis@cloudless.gr",
        from_name="Cloudless",
    )
    logger.info("playbook email sent")


def _enqueue_playbook_email(email: str, name: str | None = None) -> None:
    from app.worker.celery_app import celery_app

    celery_app.send_task(PLAYBOOK_TASK_NAME, args=[email, name])


# One playbook email per address, ever. The claim key is taken with SET NX
# *before* queueing, so concurrent submissions for the same address can't both
# enqueue (a DB read-then-stamp is racy and website leads aren't unique per
# email). The lead.meta_data stamp stays as a durable second record.
_CLAIM_TTL_S = 365 * 24 * 3600


def _claim_key(email: str) -> str:
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    return f"leads:playbook_email:{digest}"


async def _redis_client():
    import redis.asyncio as aioredis

    return aioredis.from_url(get_settings().REDIS_URL, decode_responses=True)


async def claim_playbook_send(email: str) -> bool:
    """Atomically claim the right to send this address its playbook email.

    True exactly once per address; False for every later/concurrent caller.
    Raises if Redis is unreachable (callers must then not send).
    """
    r = await _redis_client()
    try:
        ok = await r.set(_claim_key(email), datetime.now(UTC).isoformat(), nx=True, ex=_CLAIM_TTL_S)
    finally:
        await r.aclose()  # per-call client: close its pool, don't leak connections
    return bool(ok)


async def release_playbook_claim(email: str) -> None:
    try:
        r = await _redis_client()
        try:
            await r.delete(_claim_key(email))
        finally:
            await r.aclose()
    except Exception:  # noqa: BLE001
        logger.warning("Could not release playbook claim", exc_info=True)


async def deliver_playbook(db: Any, lead: Any, email: str, *, already_sent: bool, settings=None) -> str:
    """Decide and trigger playbook delivery for a public signup.

    Returns the ``playbook_delivery`` value reported to the form:
    ``email`` | ``already_sent`` | ``download`` | ``none``. Sending requires
    winning the atomic claim (:func:`claim_playbook_send`); on ``email`` the
    lead is also stamped with ``meta_data.playbook_email_queued_at``.
    """
    settings = settings or get_settings()
    if not playbook_url(settings):
        return "none"
    if already_sent or (lead.meta_data or {}).get("playbook_email_queued_at"):
        return "already_sent"
    if not playbook_delivery_enabled(settings):
        return "download"
    try:
        claimed = await claim_playbook_send(email)
    except Exception:  # noqa: BLE001 — can't claim atomically: don't risk a duplicate
        logger.warning("Playbook claim unavailable; offering download only", exc_info=True)
        return "download"
    if not claimed:
        return "already_sent"
    try:
        _enqueue_playbook_email(email, name=getattr(lead, "name", None))
    except Exception:  # noqa: BLE001 — broker down: still hand out the link
        logger.warning("Could not enqueue playbook email", exc_info=True)
        await release_playbook_claim(email)
        return "download"
    md = dict(lead.meta_data or {})
    md["playbook_email_queued_at"] = datetime.now(UTC).isoformat()
    lead.meta_data = md
    await db.commit()
    return "email"
