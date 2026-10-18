"""Email campaign API — 1-to-many sends to clients, CRM access, unsubscribe.

Mounted at ``/api/v1/email-campaigns``. The ``/unsubscribe`` routes are
public (no auth) — they must work from an email client one-click POST and
a browser GET.
"""

from __future__ import annotations

import uuid
from html import escape as _esc

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.api.deps import TeamId
from app.db.session import get_db
from app.models.email_campaign import (
    CampaignKind,
    EmailCampaign,
    EmailRecipient,
    EmailSuppression,
    RecipientStatus,
)
from app.models.lead import LeadInterest, LeadSource
from app.models.user import User
from app.services import email_campaigns as svc
from app.services import espocrm_client

router = APIRouter()


# ── Campaigns ──────────────────────────────────────────────────────────────


class SurveyQuestion(BaseModel):
    id: str = Field(..., max_length=60)
    label: str = Field(..., max_length=300)
    type: str = Field("text", pattern="^(text|choice|rating)$")
    options: list[str] | None = None
    required: bool = False
    min: int = 1
    max: int = 5


class CampaignCreate(BaseModel):
    name: str = Field(..., max_length=200)
    kind: CampaignKind = CampaignKind.marketing
    subject: str = Field(..., max_length=500)
    text_body: str
    html_body: str | None = None
    from_name: str = Field("Cloudless", max_length=120)
    reply_to: str | None = Field(None, max_length=320)
    # External form URL (e.g. Nextcloud Forms public link). When omitted on a
    # survey campaign, SocialAuto hosts the form at a per-recipient token URL.
    survey_url: str | None = Field(None, max_length=500)
    questions: list[SurveyQuestion] = Field(default_factory=list)
    # Branded hero image at the top of the email shell — public URL (R2 /
    # media library). Stored in meta_data so the send-time fallback render
    # carries it too.
    hero_url: str | None = Field(None, max_length=1000)


class AudienceSpec(BaseModel):
    emails: list[dict] = Field(default_factory=list, description="[{email,name?}]")
    include_socialauto_leads: bool = False
    lead_sources: list[LeadSource] | None = None
    lead_interests: list[LeadInterest] | None = None
    include_espocrm: bool = Field(False, description="Pull Leads+Contacts from the CRM")


@router.get("")
async def list_campaigns(
    team_id: TeamId,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[dict]:
    rows = (
        await db.execute(
            select(EmailCampaign)
            .where(EmailCampaign.team_id == team_id)
            .order_by(desc(EmailCampaign.created_at))
            .limit(limit)
        )
    ).scalars().all()
    out = []
    for c in rows:
        p = svc.campaign_payload(c)
        p["stats"] = await svc.campaign_stats(db, c.id)
        out.append(p)
    return out


@router.post("", status_code=201)
async def create_campaign(
    body: CampaignCreate,
    team_id: TeamId,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict:
    html_body = body.html_body
    if not (html_body or "").strip():
        from app.services.email_brand import render_branded_html

        html_body = render_branded_html(
            body.text_body, subject=body.subject.strip(),
            from_name=body.from_name.strip() or "Cloudless",
            hero_url=body.hero_url,
        )
    campaign = EmailCampaign(
        id=uuid.uuid4(),
        team_id=team_id,
        name=body.name.strip(),
        kind=body.kind,
        subject=body.subject.strip(),
        text_body=body.text_body,
        html_body=html_body,
        from_name=body.from_name.strip() or "Cloudless",
        reply_to=body.reply_to,
        survey_url=body.survey_url,
        meta_data={
            "questions": [q.model_dump() for q in body.questions],
            **({"hero_url": body.hero_url} if body.hero_url else {}),
        },
    )
    db.add(campaign)
    await db.commit()
    return svc.campaign_payload(campaign)


@router.get("/{campaign_id}")
async def get_campaign(
    campaign_id: uuid.UUID,
    team_id: TeamId,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict:
    c = (
        await db.execute(
            select(EmailCampaign).where(
                EmailCampaign.id == campaign_id, EmailCampaign.team_id == team_id
            )
        )
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    p = svc.campaign_payload(c)
    p["stats"] = await svc.campaign_stats(db, c.id)
    p["text_body"] = c.text_body
    p["html_body"] = c.html_body
    return p


@router.delete("/{campaign_id}")
async def delete_campaign(
    campaign_id: uuid.UUID,
    team_id: TeamId,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict:
    """Delete a draft campaign + its queued recipients (DB cascades).

    Only drafts are deletable: sent/partial campaigns must stay — their
    recipient tokens power live unsubscribe and survey links, and the rows
    are the compliance/audit record for what was sent to whom."""
    try:
        c = await svc.delete_draft_campaign(db, campaign_id, team_id)
    except ValueError:
        raise HTTPException(
            status_code=409,
            detail=(
                "Only draft campaigns can be deleted — sent campaigns are kept "
                "as the audit record and hold live unsubscribe/survey tokens."
            ),
        ) from None
    if c is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return {"deleted": True}


@router.post("/{campaign_id}/audience")
async def add_audience(
    campaign_id: uuid.UUID,
    body: AudienceSpec,
    team_id: TeamId,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict:
    """Resolve + queue recipients: explicit emails, SocialAuto leads, EspoCRM
    clients. Deduped, suppression-checked, each with an unsubscribe token."""
    c = (
        await db.execute(
            select(EmailCampaign).where(
                EmailCampaign.id == campaign_id, EmailCampaign.team_id == team_id
            )
        )
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    recipients = await svc.resolve_audience(
        db,
        c,
        explicit_emails=body.emails,
        lead_sources=body.lead_sources,
        lead_interests=body.lead_interests,
        include_espocrm=body.include_espocrm,
        include_socialauto_leads=body.include_socialauto_leads,
    )
    return {
        "queued": sum(1 for r in recipients if r.status == RecipientStatus.pending),
        "skipped_suppressed": sum(
            1 for r in recipients if r.status == RecipientStatus.skipped_suppressed
        ),
        "stats": await svc.campaign_stats(db, c.id),
    }


@router.get("/{campaign_id}/recipients")
async def list_recipients(
    campaign_id: uuid.UUID,
    team_id: TeamId,
    limit: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[dict]:
    c = (
        await db.execute(
            select(EmailCampaign).where(
                EmailCampaign.id == campaign_id, EmailCampaign.team_id == team_id
            )
        )
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    rows = (
        await db.execute(
            select(EmailRecipient)
            .where(EmailRecipient.campaign_id == campaign_id)
            .order_by(EmailRecipient.created_at)
            .limit(limit)
        )
    ).scalars().all()
    return [
        {
            "email": r.email,
            "name": r.name,
            "status": r.status.value,
            "sent_at": r.sent_at.isoformat() if r.sent_at else None,
            "error": r.error,
        }
        for r in rows
    ]


@router.get("/{campaign_id}/responses")
async def list_responses(
    campaign_id: uuid.UUID,
    team_id: TeamId,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[dict]:
    """Survey funnel responses for one campaign (staff view)."""
    from app.models.email_campaign import SurveyResponse

    c = (
        await db.execute(
            select(EmailCampaign).where(
                EmailCampaign.id == campaign_id, EmailCampaign.team_id == team_id
            )
        )
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    rows = (
        await db.execute(
            select(SurveyResponse, EmailRecipient.email)
            .outerjoin(EmailRecipient, EmailRecipient.id == SurveyResponse.recipient_id)
            .where(SurveyResponse.campaign_id == campaign_id)
            .order_by(SurveyResponse.submitted_at)
        )
    ).all()
    return [
        {
            "email": email,
            "lead_id": str(r.lead_id) if r.lead_id else None,
            "answers": r.answers,
            "espocrm_noted": r.espocrm_noted,
            "submitted_at": r.submitted_at.isoformat(),
        }
        for r, email in rows
    ]


@router.post("/{campaign_id}/send")
async def send_campaign(
    campaign_id: uuid.UUID,
    team_id: TeamId,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict:
    """Enqueue the campaign for delivery (worker task, per-recipient sends)."""
    c = (
        await db.execute(
            select(EmailCampaign).where(
                EmailCampaign.id == campaign_id, EmailCampaign.team_id == team_id
            )
        )
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    pending = (
        await db.execute(
            select(EmailRecipient).where(
                EmailRecipient.campaign_id == campaign_id,
                EmailRecipient.status == RecipientStatus.pending,
            )
        )
    ).scalars().all()
    if not pending:
        raise HTTPException(status_code=400, detail="No pending recipients — add an audience first")

    from app.services.email_campaigns import find_duplicate_campaign

    dup, score = await find_duplicate_campaign(db, c)
    if dup is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Duplicate content: matches sent campaign '{dup.name}' "
                f"({score:.0%} similar, sent within the last 30 days). "
                "Every campaign must be unique."
            ),
        )

    from app.worker.celery_app import celery_app

    celery_app.send_task(
        "app.worker.tasks.email_campaigns.send_campaign", args=[str(campaign_id)]
    )
    return {"queued": True, "pending": len(pending)}


# ── Suppression list (GDPR do-not-contact) ────────────────────────────────


@router.get("/suppressions/list")
async def list_suppressions(
    team_id: TeamId,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[dict]:
    rows = (
        await db.execute(
            select(EmailSuppression)
            .where(EmailSuppression.team_id == team_id)
            .order_by(desc(EmailSuppression.created_at))
        )
    ).scalars().all()
    return [
        {"email": r.email, "reason": r.reason, "source": r.source,
         "created_at": r.created_at.isoformat()}
        for r in rows
    ]


class SuppressionAdd(BaseModel):
    email: str
    reason: str = Field("manual", max_length=40)


@router.post("/suppressions/list", status_code=201)
async def add_suppression(
    body: SuppressionAdd,
    team_id: TeamId,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict:
    row = await svc.add_suppression(db, team_id, body.email, reason=body.reason, source="api")
    return {"email": row.email, "suppressed": True}


# ── CRM access (SocialAuto → EspoCRM client info) ─────────────────────────


@router.get("/crm/summary")
async def crm_summary(
    team_id: TeamId,
    _user: User = Depends(get_current_user),
) -> dict:
    """CRM reachability + entity counts — proves SocialAuto can read the CRM."""
    if not espocrm_client.configured():
        raise HTTPException(status_code=503, detail="EspoCRM not configured")
    return {"configured": True, "counts": await espocrm_client.lead_counts()}


@router.get("/crm/clients")
async def crm_clients(
    team_id: TeamId,
    limit: int = Query(100, ge=1, le=500),
    _user: User = Depends(get_current_user),
) -> dict:
    """Client catalog from EspoCRM — contacts + open leads with email."""
    if not espocrm_client.configured():
        raise HTTPException(status_code=503, detail="EspoCRM not configured")
    contacts = (await espocrm_client.list_contacts())[:limit]
    leads = (await espocrm_client.list_leads_for_campaign())[:limit]
    return {
        "contacts": [
            {
                "id": c.get("id"),
                "name": " ".join(
                    p for p in (c.get("firstName"), c.get("lastName")) if p
                ) or c.get("accountName"),
                "email": c.get("emailAddress"),
                "account": c.get("accountName"),
                "title": c.get("title"),
            }
            for c in contacts
        ],
        "leads": [
            {
                "id": row.get("id"),
                "name": " ".join(
                    p for p in (row.get("firstName"), row.get("lastName")) if p
                ),
                "email": row.get("emailAddress"),
                "status": row.get("status"),
                "source": row.get("source"),
            }
            for row in leads
        ],
    }


# ── Public unsubscribe (no auth) ──────────────────────────────────────────

_FUNNEL_HTML = """<!doctype html><html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · Cloudless</title></head>
<body style="margin:0;background:#0b1220;font-family:'Work Sans',Arial,Helvetica,sans-serif;color:#dde4f0;">
<div style="max-width:560px;margin:0 auto;padding:48px 20px;">
<img src="/api/v1/media/view?path=2026/09/22/1e9617f5368a4916.png" alt="Cloudless" style="height:34px;display:block;margin:0 auto 28px;"/>
<div style="background:#111a2e;border:1px solid #1e2a45;border-radius:14px;padding:32px 28px;">
<h2 style="margin:0 0 12px;color:#dde4f0;font-size:22px;text-align:center;">{title}</h2>
<p style="color:#8895ac;font-size:15px;line-height:1.7;margin:0 0 20px;text-align:center;">{body}</p>
{form}
</div>
<p style="color:#5f6b85;font-size:12px;margin-top:24px;text-align:center;">Cloudless · cloudless.gr</p>
</div>
</body></html>"""

_UNSUB_HTML = _FUNNEL_HTML

_BTN_STYLE = (
    "display:block;margin:0 auto;padding:.75em 2.4em;font-size:15px;font-weight:600;"
    "color:#0b1220;background:#22d3e6;border:0;border-radius:8px;cursor:pointer"
)


@router.get("/unsubscribe/{token}/confirm", response_class=HTMLResponse)
async def unsubscribe_confirm(token: str, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    """Browser GET — show a one-click confirm (GETs never mutate per RFC 9110)."""
    rec = (
        await db.execute(select(EmailRecipient).where(EmailRecipient.token == token))
    ).scalar_one_or_none()
    if rec is None:
        return HTMLResponse(
            _UNSUB_HTML.format(title="Link expired", body="This unsubscribe link is invalid or already used.", form=""),
            status_code=404,
        )
    if rec.status == RecipientStatus.unsubscribed:
        return HTMLResponse(
            _UNSUB_HTML.format(title="Already unsubscribed", body=f"{_esc(rec.email)} is already on our do-not-contact list.", form="")
        )
    form = (
        f'<form method="post" action="/u/{_esc(token)}">'
        f'<button type="submit" style="{_BTN_STYLE}">Unsubscribe</button></form>'
    )
    return HTMLResponse(
        _UNSUB_HTML.format(
            title="Unsubscribe",
            body=f"Stop marketing emails to <b>{_esc(rec.email)}</b>?",
            form=form,
        )
    )


@router.post("/unsubscribe/{token}/post", response_class=HTMLResponse)
async def unsubscribe_post(token: str, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    """One-click unsubscribe — RFC 8058 POST and the confirm form both land here."""
    rec, _sup = await svc.unsubscribe_by_token(db, token)
    if rec is None:
        return HTMLResponse(
            _UNSUB_HTML.format(title="Link expired", body="This unsubscribe link is invalid.", form=""),
            status_code=404,
        )
    return HTMLResponse(
        _UNSUB_HTML.format(
            title="Unsubscribed",
            body=f"<b>{_esc(rec.email)}</b> has been removed from marketing emails.",
            form="",
        )
    )


# ── Public survey funnel (no auth — token IS the credential) ──────────────

_SURVEY_HTML = _FUNNEL_HTML

_Q_STYLE = "width:100%;box-sizing:border-box;background:#0d1526;border:1px solid #1e2a45;border-radius:8px;color:#dde4f0;padding:10px 12px;font-size:14px;"
_LABEL_STYLE = "display:block;color:#dde4f0;font-size:14px;font-weight:600;margin-bottom:6px;text-align:left;"
_RADIO_STYLE = "display:block;color:#b9c4d8;font-size:14px;margin:6px 0;text-align:left;"


def _question_input(q: dict) -> str:
    # Question ids/labels/options come from the campaign definition and are
    # rendered on an unauthenticated page — escape for both text and
    # attribute contexts.
    qid = _esc(str(q["id"]))
    label = f'<label for="{qid}" style="{_LABEL_STYLE}">{_esc(str(q["label"]))}</label>'
    req = " required" if q.get("required") else ""
    if q.get("type") == "choice":
        opts = "".join(
            f'<label style="{_RADIO_STYLE}"><input type="radio" name="{qid}" value="{eo}"{req} '
            f'style="accent-color:#22d3e6;margin-right:8px;"> {eo}</label>'
            for eo in (_esc(str(o)) for o in (q.get("options") or []))
        )
        return label + opts
    if q.get("type") == "rating":
        lo, hi = int(q.get("min", 1)), int(q.get("max", 5))
        opts = "".join(
            f'<label style="{_RADIO_STYLE}"><input type="radio" name="{qid}" value="{n}"{req} style="accent-color:#22d3e6;margin-right:8px;"> {n}</label>'
            for n in range(lo, hi + 1)
        )
        return label + opts
    return label + f'<textarea name="{qid}" rows="3" style="{_Q_STYLE}"{req}></textarea>'


@router.get("/surveys/{token}", response_class=HTMLResponse)
async def survey_form(token: str, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    """Render the funnel form for one invited recipient."""
    from app.models.email_campaign import SurveyResponse

    rec = (
        await db.execute(select(EmailRecipient).where(EmailRecipient.token == token))
    ).scalar_one_or_none()
    if rec is None:
        return HTMLResponse(
            _SURVEY_HTML.format(title="Link expired", body="This survey link is invalid.", form=""),
            status_code=404,
        )
    answered = (
        await db.execute(
            select(SurveyResponse.id).where(SurveyResponse.recipient_id == rec.id)
        )
    ).scalar_one_or_none()
    if answered is not None:
        return HTMLResponse(
            _SURVEY_HTML.format(
                title="Already submitted",
                body="Thanks — your response has already been recorded.",
                form="",
            )
        )
    campaign = (
        await db.execute(select(EmailCampaign).where(EmailCampaign.id == rec.campaign_id))
    ).scalar_one_or_none()
    questions = svc.survey_questions(campaign) if campaign else []
    if not questions:
        return HTMLResponse(
            _SURVEY_HTML.format(title="Unavailable", body="This survey is not active.", form=""),
            status_code=404,
        )
    fields = "".join(f'<div style="margin-bottom:18px;text-align:left;">{_question_input(q)}</div>' for q in questions)
    form = (
        f'<form method="post" action="/s/{_esc(token)}">'
        f'{fields}<button type="submit" style="{_BTN_STYLE}">Submit</button></form>'
    )
    return HTMLResponse(
        _SURVEY_HTML.format(
            title=_esc(campaign.name) if campaign else "Survey",
            body=_esc(campaign.subject or "") if campaign else "",
            form=form,
        )
    )


@router.post("/surveys/{token}", response_class=HTMLResponse)
async def survey_submit(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Accept the funnel submission (HTML form or JSON) — validates against
    the campaign's question schema, stores the response, updates the local
    lead and attaches an activity note to the EspoCRM record."""
    ctype = (request.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        raw = await request.json()
        answers = raw.get("answers", raw) if isinstance(raw, dict) else {}
    else:
        form = await request.form()
        answers = {k: str(v) for k, v in form.items()}
    resp, errors = await svc.submit_survey(db, token, answers)
    if resp is None:
        status = 404 if errors == ["invalid token"] else 422
        return HTMLResponse(
            _SURVEY_HTML.format(
                title="Not accepted",
                body=_esc("; ".join(errors)) or "This survey link is invalid.",
                form="",
            ),
            status_code=status,
        )
    return HTMLResponse(
        _SURVEY_HTML.format(
            title="Thank you",
            body="Your response has been recorded.",
            form="",
        )
    )


# ── Short public funnel paths ─────────────────────────────────────────────
# Mounted at app root in main.py (schema-excluded). Customer-facing email
# links use these instead of the raw /api/v1/* paths so recipients see
# clean URLs: https://social.cloudless.gr/s/<token>, /u/<token>.
public_router = APIRouter()


@public_router.get("/s/{token}", response_class=HTMLResponse)
async def short_survey_form(token: str, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    return await survey_form(token, db)


@public_router.post("/s/{token}", response_class=HTMLResponse)
async def short_survey_submit(
    token: str, request: Request, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    return await survey_submit(token, request, db)


@public_router.get("/u/{token}", response_class=HTMLResponse)
async def short_unsubscribe_confirm(token: str, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    return await unsubscribe_confirm(token, db)


@public_router.post("/u/{token}", response_class=HTMLResponse)
async def short_unsubscribe_post(token: str, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    return await unsubscribe_post(token, db)
