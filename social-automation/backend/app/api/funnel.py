"""Funnel API — conversion ingest (lead→CTA→checkout join) + state report.

POST /funnel/conversion is the machine-to-machine ingest the checkout/CRM
side (Polar webhook relay on cloudless.gr, EspoCRM automation, ad-platform
offline-conversion uploads) posts into. It links the event to the SocialAuto
lead by email so funnel_score/dbt close the loop.

GET /funnel/state reports the live funnel: lead tiers, conversion counts,
per-campaign revenue — the data-driven read the monetization layer answers.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.core.config import get_settings
from app.db.session import get_db
from app.models.lead import Lead
from app.models.user import User

router = APIRouter()


class ConversionIn(BaseModel):
    email: str | None = None
    lead_id: uuid.UUID | None = None
    post_id: uuid.UUID | None = None
    platform: str | None = None
    utm_campaign: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_content: str | None = None
    event: str = "conversion"
    value: float | None = None
    currency: str | None = None
    provider: str | None = None        # polar|stripe|dodo|espo|manual
    provider_ref: str | None = None    # checkout/order id
    meta_data: dict | None = None


def _check_ingest_token(token: str | None) -> None:
    expected = (getattr(get_settings(), "FUNNEL_INGEST_TOKEN", "") or "").strip()
    if expected and token != expected:
        raise HTTPException(status_code=401, detail="bad ingest token")


@router.post("/conversion", status_code=201)
async def ingest_conversion(
    body: ConversionIn,
    db: AsyncSession = Depends(get_db),
    x_funnel_token: str | None = Header(default=None),
):
    """Record a funnel conversion — joins a checkout/CRM event back to the
    lead (+ post/campaign) so the monetization loop measures real revenue."""
    _check_ingest_token(x_funnel_token)
    email = (body.email or "").strip().lower() or None
    lead_id = body.lead_id
    if not lead_id and email:
        lead_id = await db.scalar(
            select(Lead.id).where(func.lower(Lead.email) == email).limit(1))
    conv_id = uuid.uuid4()
    await db.execute(text("""
        INSERT INTO funnel_conversions
            (id, lead_id, email, post_id, platform, utm_campaign, utm_source,
             utm_medium, utm_content, event, value, currency, provider,
             provider_ref, meta_data)
        VALUES (:id, :lead_id, :email, :post_id, :platform, :utm_campaign,
                :utm_source, :utm_medium, :utm_content, :event, :value,
                :currency, :provider, :provider_ref, :meta_data)"""), {
        "id": str(conv_id), "lead_id": str(lead_id) if lead_id else None,
        "email": email, "post_id": str(body.post_id) if body.post_id else None,
        "platform": body.platform, "utm_campaign": body.utm_campaign,
        "utm_source": body.utm_source, "utm_medium": body.utm_medium,
        "utm_content": body.utm_content, "event": body.event,
        "value": body.value, "currency": body.currency,
        "provider": body.provider, "provider_ref": body.provider_ref,
        "meta_data": __import__("json").dumps(body.meta_data)
                   if body.meta_data else None,
    })
    await db.commit()
    return {"id": str(conv_id), "lead_id": str(lead_id) if lead_id else None,
            "linked": lead_id is not None}


@router.get("/state")
async def funnel_state(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Live funnel state — tier distribution, conversions, revenue by campaign."""
    tiers = (await db.execute(text("""
        SELECT CASE
            WHEN interest = 'audit' OR thread_id IS NOT NULL
                 OR updated_at > now() - interval '7 days' THEN 'hot'
            WHEN interest IS NOT NULL AND email IS NOT NULL THEN 'warm'
            ELSE 'cold' END AS tier, count(*)
        FROM leads GROUP BY 1"""))).fetchall()
    convs = (await db.execute(text("""
        SELECT coalesce(utm_campaign,'(none)') AS campaign, count(*),
               coalesce(sum(value),0)
        FROM funnel_conversions GROUP BY 1 ORDER BY 2 DESC"""))).fetchall()
    totals = (await db.execute(text("""
        SELECT count(*), coalesce(sum(value),0) FROM funnel_conversions"""))
        ).one()
    return {
        "leads": {r.tier: r.count for r in tiers},
        "conversions": [
            {"campaign": r.campaign, "count": r.count, "value": float(r[2])}
            for r in convs],
        "total_conversions": totals[0],
        "total_value": float(totals[1]),
        "at": datetime.now(UTC).isoformat(),
    }
