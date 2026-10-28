"""Email campaign service — 1-to-many marketing/survey sends to clients.

Recipient sources:
  * explicit email list
  * SocialAuto ``leads`` rows (optionally filtered by source/interest)
  * EspoCRM Leads/Contacts (the cloudless.gr CRM — the socialauto API user)

GDPR / deliverability rules enforced here:
  * the team suppression list is checked before every recipient is queued AND
    again before every send (an unsubscribe landing mid-campaign still wins);
  * every message carries an ``unsubscribe_url`` merge tag, a
    ``List-Unsubscribe`` header and a ``List-Unsubscribe-Post`` one-click
    header (RFC 8058 — required by Gmail/Yahoo bulk-sender rules);
  * the public unsubscribe endpoint records the suppression — the row stays
    (right to object → keep a do-not-contact entry, don't delete).
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.email_campaign import (
    CampaignKind,
    CampaignStatus,
    EmailCampaign,
    EmailRecipient,
    EmailSuppression,
    RecipientStatus,
    SurveyResponse,
)
from app.models.lead import Lead, LeadInterest, LeadSource
from app.services import espocrm_client
from app.services.leads import _is_synthetic_email, is_valid_email

logger = logging.getLogger(__name__)

# Free-tier Resend headroom (100/day shared with transactional mail).
DAILY_SEND_BUDGET = 80
SEND_DELAY_SECONDS = 2.0
# No-duplicate rule — a campaign may not repeat recently sent content.
DUP_WINDOW_DAYS = 30
DUP_THRESHOLD = 0.48  # same detector threshold as post publishing


def _campaign_text(campaign: EmailCampaign) -> str:
    """Canonical comparable text: subject + authored body (no per-recipient merges)."""
    return f"{campaign.subject}\n{campaign.text_body}"


async def find_duplicate_campaign(
    db: AsyncSession, campaign: EmailCampaign
) -> tuple[EmailCampaign | None, float]:
    """Return the recently-sent campaign this one duplicates, if any.

    Compares subject+body against this team's campaigns that are sending/sent
    within DUP_WINDOW_DAYS, using the same detector as post publishing.
    """
    # Drip clones intentionally resend identical copy to *new* recipients —
    # uniqueness for them is per-recipient, enforced by the drip sweep against
    # email_recipients (same address + same drip_key/cycle never mails twice).
    if (campaign.meta_data or {}).get("drip_key"):
        return None, 0.0

    from app.services.duplicate_detector import similarity_score

    since = datetime.now(UTC) - timedelta(days=DUP_WINDOW_DAYS)
    recent = (
        await db.execute(
            select(EmailCampaign)
            .where(
                EmailCampaign.team_id == campaign.team_id,
                EmailCampaign.id != campaign.id,
                EmailCampaign.status.in_(
                    [CampaignStatus.sending, CampaignStatus.sent, CampaignStatus.partial]
                ),
                EmailCampaign.updated_at >= since,
            )
            .order_by(EmailCampaign.updated_at.desc())
            .limit(50)
        )
    ).scalars().all()

    text = _campaign_text(campaign)
    best: EmailCampaign | None = None
    best_score = 0.0
    for other in recent:
        score = similarity_score(text, _campaign_text(other))
        if score > best_score:
            best, best_score = other, score
    if best is not None and best_score >= DUP_THRESHOLD:
        return best, best_score
    return None, best_score

_MERGE_TAG_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def unsubscribe_url(token: str) -> str:
    """Public one-click unsubscribe URL (no auth).

    The frontend URL is the public edge; `/u/<token>` proxies to the API.
    GET shows the confirm page, POST unsubscribes (RFC 8058 one-click)."""
    base = (get_settings().FRONTEND_URL or "https://social.cloudless.gr").rstrip("/")
    return f"{base}/u/{token}"


def survey_url_for(campaign: EmailCampaign, recipient: EmailRecipient) -> str:
    """Per-recipient funnel URL — opaque token identifies the respondent, so
    submissions join back to the campaign, lead and CRM record."""
    if campaign.survey_url:
        return campaign.survey_url
    base = (get_settings().FRONTEND_URL or "https://social.cloudless.gr").rstrip("/")
    return f"{base}/s/{recipient.token}"


def render_template(template: str, ctx: dict[str, str]) -> str:
    def _sub(m: re.Match) -> str:
        return ctx.get(m.group(1), m.group(0))

    return _MERGE_TAG_RE.sub(_sub, template)


_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# crm_<snake> tags that get shorter aliases for template authors.
_CRM_ALIASES = {
    "account_name": "company",
    "phone_number": "phone",
    "address_city": "city",
    "assigned_user_name": "owner",
}


def _crm_ctx(record: dict[str, Any] | None) -> dict[str, str]:
    """Flatten one CRM record into merge tags — ``accountName`` → ``crm_account_name``.

    Scalar fields only; ``id``/bools/nested values are skipped. Unknown tags
    stay literal in the output so authors can see what did not resolve."""
    if not record:
        return {}
    ctx: dict[str, str] = {}
    for key, val in record.items():
        if key == "id" or isinstance(val, bool) or not isinstance(val, str | int | float):
            continue
        s = str(val).strip()
        if s:
            ctx["crm_" + _CAMEL_BOUNDARY_RE.sub("_", key).lower()] = s
    for src, alias in _CRM_ALIASES.items():
        if f"crm_{src}" in ctx:
            ctx.setdefault(f"crm_{alias}", ctx[f"crm_{src}"])
    return ctx


async def _suppressed_emails(db: AsyncSession, team_id: uuid.UUID) -> set[str]:
    rows = (
        await db.execute(
            select(EmailSuppression.email).where(EmailSuppression.team_id == team_id)
        )
    ).scalars().all()
    return {e.strip().lower() for e in rows}


async def is_suppressed(db: AsyncSession, team_id: uuid.UUID, email: str) -> bool:
    return email.strip().lower() in await _suppressed_emails(db, team_id)


async def add_suppression(
    db: AsyncSession,
    team_id: uuid.UUID,
    email: str,
    *,
    reason: str = "unsubscribe",
    source: str = "campaign",
) -> EmailSuppression:
    email_n = email.strip().lower()
    existing = (
        await db.execute(
            select(EmailSuppression).where(
                EmailSuppression.team_id == team_id,
                EmailSuppression.email == email_n,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    row = EmailSuppression(
        id=uuid.uuid4(), team_id=team_id, email=email_n,
        reason=reason[:40], source=source[:120],
    )
    db.add(row)
    await db.commit()
    return row


async def resolve_audience(
    db: AsyncSession,
    campaign: EmailCampaign,
    *,
    explicit_emails: list[dict[str, str | None]] | None = None,
    lead_sources: list[LeadSource] | None = None,
    lead_interests: list[LeadInterest] | None = None,
    include_espocrm: bool = False,
    include_socialauto_leads: bool = False,
) -> list[EmailRecipient]:
    """Build the campaign audience. Dedupes by email, drops suppressed and
    invalid addresses. Returns the created (uncommitted-by-caller) rows."""
    suppressed = await _suppressed_emails(db, campaign.team_id)
    seen: set[str] = set()
    out: list[EmailRecipient] = []

    def _add(email: str, name: str | None, lead_id: uuid.UUID | None = None) -> None:
        e = (email or "").strip().lower()
        if not is_valid_email(e) or _is_synthetic_email(e) or e in seen:
            return
        seen.add(e)
        status = (
            RecipientStatus.skipped_suppressed if e in suppressed else RecipientStatus.pending
        )
        out.append(
            EmailRecipient(
                id=uuid.uuid4(),
                campaign_id=campaign.id,
                team_id=campaign.team_id,
                lead_id=lead_id,
                email=e,
                name=(name or "").strip() or None,
                status=status,
                token=EmailRecipient.new_token(),
            )
        )

    if explicit_emails:
        # Link explicit addresses to existing leads so the funnel (survey →
        # CRM note) still fires for hand-picked recipients.
        wanted = {(str(i.get("email") or "").strip().lower()) for i in explicit_emails}
        wanted.discard("")
        lead_rows = (
            await db.execute(
                select(Lead.id, Lead.email).where(
                    Lead.team_id == campaign.team_id,
                    func.lower(Lead.email).in_(wanted),
                )
            )
        ).all() if wanted else []
        lead_by_email = {e.strip().lower(): lid for lid, e in lead_rows}
        for item in explicit_emails:
            e = str(item.get("email") or "").strip().lower()
            _add(e, item.get("name"), lead_by_email.get(e))

    if include_socialauto_leads or lead_sources or lead_interests:
        q = select(Lead).where(Lead.team_id == campaign.team_id)
        if lead_sources:
            q = q.where(Lead.source.in_(lead_sources))
        if lead_interests:
            q = q.where(Lead.interest.in_(lead_interests))
        leads = (await db.execute(q)).scalars().all()
        for lead in leads:
            _add(lead.email, lead.name, lead.id)

    if include_espocrm and espocrm_client.configured():
        try:
            for crm_email, prof in (await espocrm_client.campaign_profiles()).items():
                full = " ".join(
                    p for p in (prof.get("firstName"), prof.get("lastName")) if p
                )
                _add(crm_email, full or prof.get("accountName"))
        except Exception:  # noqa: BLE001 — EspoCRM down shouldn't kill the send
            logger.exception("EspoCRM audience pull failed for campaign %s", campaign.id)

    for r in out:
        db.add(r)
    await db.commit()
    return out


def build_bodies(
    campaign: EmailCampaign,
    recipient: EmailRecipient,
    unsub: str,
    *,
    crm: dict[str, Any] | None = None,
) -> tuple[str, str | None]:
    """Personalised text + html bodies for one recipient.

    ``crm`` is the recipient's EspoCRM record (matched by email) — its scalar
    fields become ``{{crm_*}}`` merge tags, and CRM names backfill a missing
    recipient name."""
    survey_link = (
        survey_url_for(campaign, recipient)
        if campaign.kind == CampaignKind.survey
        else (campaign.survey_url or "")
    )
    name = recipient.name or ""
    if not name and crm:
        name = " ".join(
            str(p) for p in (crm.get("firstName"), crm.get("lastName")) if p
        ).strip()
    ctx = {
        "name": name,
        "first_name": name.split(" ")[0] if name else "",
        "email": recipient.email,
        "campaign": campaign.name,
        "survey_url": survey_link,
        "unsubscribe_url": unsub,
    }
    ctx.update(_crm_ctx(crm))
    text = render_template(campaign.text_body, ctx)
    if survey_link and "survey_url" not in campaign.text_body:
        text += f"\n\nTake the survey: {survey_link}"
    text += f"\n\n---\nUnsubscribe: {unsub}"
    html = campaign.html_body
    if not (html or "").strip():
        from app.services.email_brand import render_branded_html

        html = render_branded_html(
            campaign.text_body, subject=campaign.subject or "",
            from_name=campaign.from_name or "Cloudless",
            hero_url=(campaign.meta_data or {}).get("hero_url"),
        )
    html = render_template(html, ctx) if html else None
    if html:
        html += (
            '<hr><p style="font-size:12px;color:#666">'
            f'<a href="{unsub}">Unsubscribe</a></p>'
        )
    return text, html


async def send_campaign(campaign_id: uuid.UUID) -> dict[str, int]:
    """Deliver every pending recipient, one message each (no BCC leaks).

    Honours the daily Resend free-tier budget across all campaigns — when the
    budget is spent the campaign keeps its remaining rows pending.
    """
    from app.db.session import async_session_maker
    from app.services.email_digest import send_email

    stats = {"sent": 0, "failed": 0, "skipped": 0, "budget_left": DAILY_SEND_BUDGET}
    async with async_session_maker() as db:
        campaign = (
            await db.execute(select(EmailCampaign).where(EmailCampaign.id == campaign_id))
        ).scalar_one_or_none()
        if campaign is None:
            return {**stats, "error": "campaign_not_found"}  # type: ignore[dict-item]

        dup, score = await find_duplicate_campaign(db, campaign)
        if dup is not None:
            logger.warning(
                "campaign %s refused: %.0f%% duplicate of sent campaign %s",
                campaign.id, score * 100, dup.id,
            )
            return {
                **stats,
                "error": "duplicate_campaign",  # type: ignore[dict-item]
                "duplicate_of": str(dup.id),  # type: ignore[dict-item]
                "duplicate_name": dup.name,  # type: ignore[dict-item]
                "similarity": round(score, 3),  # type: ignore[dict-item]
            }

        campaign.status = CampaignStatus.sending
        await db.commit()

        sent_today = await _sent_today(db, campaign.team_id)
        budget = max(0, DAILY_SEND_BUDGET - sent_today)
        stats["budget_left"] = budget

        pending = (
            await db.execute(
                select(EmailRecipient).where(
                    EmailRecipient.campaign_id == campaign_id,
                    EmailRecipient.status == RecipientStatus.pending,
                )
            )
        ).scalars().all()

        # CRM personalization — one fetch per campaign; every recipient whose
        # email exists in EspoCRM gets {{crm_*}} tags filled from their record.
        crm_profiles: dict[str, dict[str, Any]] = {}
        if espocrm_client.configured():
            try:
                crm_profiles = await espocrm_client.campaign_profiles()
            except Exception:  # noqa: BLE001 — CRM outage must not block mail
                logger.exception(
                    "EspoCRM personalization pull failed for campaign %s", campaign.id
                )

        for rec in pending:
            if budget <= 0:
                break
            # Re-check suppression per send — an unsubscribe that landed after
            # queueing must still win.
            if await is_suppressed(db, campaign.team_id, rec.email):
                rec.status = RecipientStatus.skipped_suppressed
                stats["skipped"] += 1
                continue
            unsub = unsubscribe_url(rec.token)
            text, html = build_bodies(campaign, rec, unsub, crm=crm_profiles.get(rec.email))
            try:
                await send_email(
                    subject=campaign.subject,
                    text_body=text,
                    html_body=html,
                    to_addrs=[rec.email],
                    extra_headers={
                        "List-Unsubscribe": f"<{unsub}>",
                        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
                    },
                    reply_to=campaign.reply_to,
                    from_name=campaign.from_name,
                )
                rec.status = RecipientStatus.sent
                rec.sent_at = datetime.now(UTC)
                stats["sent"] += 1
                budget -= 1
            except Exception as exc:  # noqa: BLE001 — per-recipient isolation
                rec.status = RecipientStatus.failed
                rec.error = str(exc)[:2000]
                stats["failed"] += 1
                logger.warning("campaign %s send to %s failed: %s", campaign.id, rec.email, exc)
            await db.commit()
            if budget > 0:
                await asyncio.sleep(SEND_DELAY_SECONDS)

        remaining = (
            await db.execute(
                select(func.count())
                .select_from(EmailRecipient)
                .where(
                    EmailRecipient.campaign_id == campaign_id,
                    EmailRecipient.status == RecipientStatus.pending,
                )
            )
        ).scalar() or 0
        if remaining == 0:
            campaign.status = (
                CampaignStatus.sent if stats["failed"] == 0 else CampaignStatus.partial
            )
            campaign.sent_at = datetime.now(UTC)
        await db.commit()

    stats["budget_left"] = budget
    return stats


async def _sent_today(db: AsyncSession, team_id: uuid.UUID) -> int:
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        await db.execute(
            select(func.count())
            .select_from(EmailRecipient)
            .where(
                EmailRecipient.team_id == team_id,
                EmailRecipient.status == RecipientStatus.sent,
                EmailRecipient.sent_at >= start,
            )
        )
    ).scalar() or 0


async def unsubscribe_by_token(
    db: AsyncSession, token: str
) -> tuple[EmailRecipient | None, EmailSuppression | None]:
    """Public unsubscribe — marks the recipient and writes the do-not-contact
    row for the whole team."""
    rec = (
        await db.execute(
            select(EmailRecipient).where(EmailRecipient.token == token)
        )
    ).scalar_one_or_none()
    if rec is None:
        return None, None
    rec.status = RecipientStatus.unsubscribed
    sup = await add_suppression(
        db, rec.team_id, rec.email, reason="unsubscribe", source="unsubscribe_link"
    )
    await db.commit()
    return rec, sup


# ── Survey funnel ──────────────────────────────────────────────────────────


def survey_questions(campaign: EmailCampaign) -> list[dict[str, Any]]:
    """Question definitions from campaign metadata.

    Schema: ``[{"id": "q1", "label": "…", "type": "text|choice|rating",
    "options": [...], "required": true}]``
    """
    qs = (campaign.meta_data or {}).get("questions") or []
    return [q for q in qs if isinstance(q, dict) and q.get("id") and q.get("label")]


def validate_answers(
    questions: list[dict[str, Any]], answers: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Normalize + validate submitted answers. Returns (clean, errors)."""
    clean: dict[str, Any] = {}
    errors: list[str] = []
    valid_ids = set()
    for q in questions:
        qid = str(q["id"])
        valid_ids.add(qid)
        val = answers.get(qid)
        missing = val is None or (isinstance(val, str) and not val.strip())
        if missing:
            if q.get("required"):
                errors.append(f"{qid}: required")
            continue
        qtype = q.get("type", "text")
        if qtype == "rating":
            try:
                n = int(val)
            except (TypeError, ValueError):
                errors.append(f"{qid}: not a number")
                continue
            lo, hi = int(q.get("min", 1)), int(q.get("max", 5))
            if not lo <= n <= hi:
                errors.append(f"{qid}: rating out of range {lo}-{hi}")
                continue
            clean[qid] = n
        elif qtype == "choice":
            opts = [str(o) for o in (q.get("options") or [])]
            s = str(val).strip()
            if opts and s not in opts:
                errors.append(f"{qid}: not a valid option")
                continue
            clean[qid] = s[:500]
        else:  # text
            clean[qid] = str(val).strip()[:4000]
    # Drop answers to unknown question ids — prevents junk keys in the funnel.
    return clean, errors


async def submit_survey(
    db: AsyncSession, token: str, answers: dict[str, Any]
) -> tuple[SurveyResponse | None, list[str]]:
    """Record a funnel submission: validate, persist, update the local lead,
    and attach an activity Note to the CRM record. Idempotent per recipient —
    a second submit returns the stored response."""
    rec = (
        await db.execute(
            select(EmailRecipient).where(EmailRecipient.token == token)
        )
    ).scalar_one_or_none()
    if rec is None:
        return None, ["invalid token"]
    campaign = (
        await db.execute(
            select(EmailCampaign).where(EmailCampaign.id == rec.campaign_id)
        )
    ).scalar_one_or_none()
    if campaign is None or campaign.kind != CampaignKind.survey:
        return None, ["not a survey campaign"]

    existing = (
        await db.execute(
            select(SurveyResponse).where(SurveyResponse.recipient_id == rec.id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, []

    clean, errors = validate_answers(survey_questions(campaign), answers or {})
    if errors:
        return None, errors

    resp = SurveyResponse(
        id=uuid.uuid4(),
        team_id=rec.team_id,
        campaign_id=campaign.id,
        recipient_id=rec.id,
        lead_id=rec.lead_id,
        answers=clean,
    )
    db.add(resp)

    # Funnel write-back: mark the local lead and the CRM record.
    if rec.lead_id:
        lead = (
            await db.execute(select(Lead).where(Lead.id == rec.lead_id))
        ).scalar_one_or_none()
        if lead is not None:
            stamp = datetime.now(UTC).isoformat(timespec="seconds")
            summary = "; ".join(f"{k}={v}" for k, v in clean.items())[:800]
            lead.notes = (f"{lead.notes}\n" if lead.notes else "") + (
                f"[survey {stamp}] {campaign.name}: {summary}"
            )
            meta = dict(lead.meta_data or {})
            meta["last_survey_campaign_id"] = str(campaign.id)
            meta["last_survey_at"] = stamp
            lead.meta_data = meta
            crm_id = meta.get("espocrm_lead_id")
            if crm_id:
                resp.espocrm_noted = await espocrm_client.add_lead_note(
                    str(crm_id),
                    f"Survey '{campaign.name}' ({stamp} UTC)\n"
                    + "\n".join(f"- {k}: {v}" for k, v in clean.items()),
                )

    await db.commit()
    return resp, []


async def campaign_stats(db: AsyncSession, campaign_id: uuid.UUID) -> dict[str, int]:
    rows = (
        await db.execute(
            select(EmailRecipient.status, func.count())
            .where(EmailRecipient.campaign_id == campaign_id)
            .group_by(EmailRecipient.status)
        )
    ).all()
    return {str(s): int(c) for s, c in rows}


def campaign_payload(c: EmailCampaign) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "name": c.name,
        "kind": c.kind.value,
        "subject": c.subject,
        "status": c.status.value,
        "survey_url": c.survey_url,
        "questions": survey_questions(c),
        "sent_at": c.sent_at.isoformat() if c.sent_at else None,
        "created_at": c.created_at.isoformat(),
    }


async def delete_draft_campaign(
    db: AsyncSession, campaign_id: uuid.UUID, team_id: uuid.UUID
) -> EmailCampaign | None:
    """Delete a draft campaign + its queued recipients (DB cascades).

    Returns the deleted campaign, ``None`` if not found, and raises
    ``ValueError`` when the campaign is not a draft — sent/partial campaigns
    are kept: their recipient tokens power live unsubscribe/survey links and
    the rows are the audit record."""
    c = (
        await db.execute(
            select(EmailCampaign).where(
                EmailCampaign.id == campaign_id, EmailCampaign.team_id == team_id
            )
        )
    ).scalar_one_or_none()
    if c is None:
        return None
    if c.status != CampaignStatus.draft:
        raise ValueError(f"campaign status is {c.status.value}, not draft")
    await db.delete(c)
    await db.commit()
    return c
