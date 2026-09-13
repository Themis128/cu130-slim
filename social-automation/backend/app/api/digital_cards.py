"""Digital Business Cards API — CRUD, vCard 4.0 generation, public share, WhatsApp/Messenger send."""
import os
import secrets
import uuid
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.db.session import get_db
from app.models.digital_card import DigitalCard
from app.models.social_account import SocialAccount
from app.models.user import Team, TeamMember, User

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _get_team(user: User, db: AsyncSession) -> Team:
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == user.id)
    )
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")
    return team


def _gen_token() -> str:
    return secrets.token_urlsafe(32)


def _build_vcard(card: DigitalCard) -> str:
    """Build a vCard 4.0 (RFC 6350) string from a DigitalCard."""
    lines = [
        "BEGIN:VCARD",
        "VERSION:4.0",
        f"FN:{card.name}",
    ]
    if card.company:
        lines.append(f"ORG:{card.company}")
    if card.title:
        lines.append(f"TITLE:{card.title}")
    if card.email:
        lines.append(f"EMAIL;TYPE=work;PREF=1:{card.email}")
    if card.phone:
        lines.append(f"TEL;TYPE=work,voice;VALUE=uri;PREF=1:tel:{card.phone.replace(' ', '')}")
    if card.website:
        lines.append(f"URL:{card.website}")
    if card.address:
        lines.append(f"ADR;TYPE=work:;;{card.address};;;;")
    if card.tagline:
        lines.append(f"NOTE:{card.tagline}")
    for s in (card.social_links or []):
        url = s.get("url")
        platform = s.get("platform", "social")
        if url:
            lines.append(f"URL;TYPE={platform}:{url}")
    if card.logo_url:
        lines.append(f"LOGO;VALUE=uri:{card.logo_url}")
    lines.append("END:VCARD")
    return "\r\n".join(lines)


def _card_url(token: str) -> str:
    base = os.environ.get("FRONTEND_URL", "https://social.cloudless.gr")
    return f"{base}/card/{token}"


# ── Schemas ───────────────────────────────────────────────────────────────────


class SocialLinkIn(BaseModel):
    platform: str
    handle: str = ""
    url: str | None = None
    display_name: str | None = None


class SocialLinkOut(SocialLinkIn):
    model_config = ConfigDict(from_attributes=True)


class ServiceIn(BaseModel):
    title: str
    description: str = ""


class DigitalCardCreate(BaseModel):
    name: str
    title: str | None = None
    company: str | None = None
    tagline: str | None = None
    description: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    address: str | None = None
    primary_color: str | None = None
    accent_color: str | None = None
    logo_url: str | None = None
    avatar_url: str | None = None
    social_links: list[SocialLinkIn] = []
    services: list[ServiceIn] = []
    brand_id: uuid.UUID | None = None


class DigitalCardUpdate(BaseModel):
    name: str | None = None
    title: str | None = None
    company: str | None = None
    tagline: str | None = None
    description: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    address: str | None = None
    primary_color: str | None = None
    accent_color: str | None = None
    logo_url: str | None = None
    avatar_url: str | None = None
    social_links: list[SocialLinkIn] | None = None
    services: list[ServiceIn] | None = None
    is_active: bool | None = None


class DigitalCardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    team_id: uuid.UUID
    brand_id: uuid.UUID | None
    name: str
    title: str | None = None
    company: str | None = None
    tagline: str | None = None
    description: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    address: str | None = None
    primary_color: str | None = None
    accent_color: str | None = None
    logo_url: str | None = None
    avatar_url: str | None = None
    social_links: list[dict] = []
    services: list[dict] = []
    share_token: str
    is_active: bool
    view_count: int = 0
    contact_save_count: int = 0
    share_count: int = 0
    card_url: str | None = None
    vcard: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DigitalCardPublicOut(BaseModel):
    """Public card data — no auth, no sensitive fields."""
    name: str
    title: str | None = None
    company: str | None = None
    tagline: str | None = None
    description: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    address: str | None = None
    primary_color: str | None = None
    accent_color: str | None = None
    logo_url: str | None = None
    avatar_url: str | None = None
    social_links: list[dict] = []
    services: list[dict] = []
    card_url: str | None = None
    vcard: str | None = None


class SendCardRequest(BaseModel):
    """Send card link via WhatsApp or Messenger."""
    to_phone: str | None = None  # E.164 for WhatsApp
    platform: str = "whatsapp"  # whatsapp | messenger
    message: str | None = None


class SendCardResult(BaseModel):
    success: bool
    platform: str
    message_id: str | None = None
    error: str | None = None


# ── CRUD endpoints (auth required) ────────────────────────────────────────────


@router.get("", response_model=list[DigitalCardOut])
async def list_cards(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = await _get_team(current_user, db)
    result = await db.execute(
        select(DigitalCard)
        .where(DigitalCard.team_id == team.id)
        .order_by(DigitalCard.created_at.desc())
    )
    cards = result.scalars().all()
    out = []
    for c in cards:
        d = DigitalCardOut.model_validate(c)
        d.card_url = _card_url(c.share_token)
        d.vcard = _build_vcard(c)
        out.append(d)
    return out


@router.post("", response_model=DigitalCardOut, status_code=201)
async def create_card(
    data: DigitalCardCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = await _get_team(current_user, db)
    card = DigitalCard(
        team_id=team.id,
        brand_id=data.brand_id,
        name=data.name,
        title=data.title,
        company=data.company,
        tagline=data.tagline,
        description=data.description,
        email=data.email,
        phone=data.phone,
        website=data.website,
        address=data.address,
        primary_color=data.primary_color,
        accent_color=data.accent_color,
        logo_url=data.logo_url,
        avatar_url=data.avatar_url,
        social_links=[s.model_dump() for s in data.social_links],
        services=[s.model_dump() for s in data.services],
        share_token=_gen_token(),
    )
    db.add(card)
    await db.commit()
    await db.refresh(card)
    out = DigitalCardOut.model_validate(card)
    out.card_url = _card_url(card.share_token)
    out.vcard = _build_vcard(card)
    return out


@router.get("/{card_id}", response_model=DigitalCardOut)
async def get_card(
    card_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = await _get_team(current_user, db)
    result = await db.execute(
        select(DigitalCard).where(DigitalCard.id == card_id, DigitalCard.team_id == team.id)
    )
    card = result.scalars().first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    out = DigitalCardOut.model_validate(card)
    out.card_url = _card_url(card.share_token)
    out.vcard = _build_vcard(card)
    return out


@router.patch("/{card_id}", response_model=DigitalCardOut)
async def update_card(
    card_id: uuid.UUID,
    data: DigitalCardUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = await _get_team(current_user, db)
    result = await db.execute(
        select(DigitalCard).where(DigitalCard.id == card_id, DigitalCard.team_id == team.id)
    )
    card = result.scalars().first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    updates = data.model_dump(exclude_unset=True)
    if "social_links" in updates and updates["social_links"] is not None:
        updates["social_links"] = [s.model_dump() if hasattr(s, "model_dump") else s for s in data.social_links]
    if "services" in updates and updates["services"] is not None:
        updates["services"] = [s.model_dump() if hasattr(s, "model_dump") else s for s in data.services]
    for k, v in updates.items():
        setattr(card, k, v)
    await db.commit()
    await db.refresh(card)
    out = DigitalCardOut.model_validate(card)
    out.card_url = _card_url(card.share_token)
    out.vcard = _build_vcard(card)
    return out


@router.delete("/{card_id}", status_code=204)
async def delete_card(
    card_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = await _get_team(current_user, db)
    result = await db.execute(
        select(DigitalCard).where(DigitalCard.id == card_id, DigitalCard.team_id == team.id)
    )
    card = result.scalars().first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    await db.delete(card)
    await db.commit()


# ── vCard download ────────────────────────────────────────────────────────────


@router.get("/{card_id}/vcard", response_class=PlainTextResponse)
async def download_vcard(
    card_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = await _get_team(current_user, db)
    result = await db.execute(
        select(DigitalCard).where(DigitalCard.id == card_id, DigitalCard.team_id == team.id)
    )
    card = result.scalars().first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    vcard = _build_vcard(card)
    filename = f"{card.name.replace(' ', '_')}.vcf"
    return PlainTextResponse(
        content=vcard,
        media_type="text/vcard;charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Public card (no auth) ─────────────────────────────────────────────────────


@router.get("/public/{token}", response_model=DigitalCardPublicOut)
async def get_public_card(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DigitalCard).where(DigitalCard.share_token == token, DigitalCard.is_active == True)  # noqa: E712
    )
    card = result.scalars().first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    # Increment view count
    card.view_count += 1
    await db.commit()
    return DigitalCardPublicOut(
        name=card.name,
        title=card.title,
        company=card.company,
        tagline=card.tagline,
        description=card.description,
        email=card.email,
        phone=card.phone,
        website=card.website,
        address=card.address,
        primary_color=card.primary_color,
        accent_color=card.accent_color,
        logo_url=card.logo_url,
        avatar_url=card.avatar_url,
        social_links=card.social_links or [],
        services=card.services or [],
        card_url=_card_url(card.share_token),
        vcard=_build_vcard(card),
    )


@router.post("/public/{token}/track", response_model=dict)
async def track_card_action(
    token: str,
    action: str = Query(..., pattern="^(save|share)$"),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DigitalCard).where(DigitalCard.share_token == token)
    )
    card = result.scalars().first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    if action == "save":
        card.contact_save_count += 1
    elif action == "share":
        card.share_count += 1
    await db.commit()
    return {"ok": True, "action": action}


# ── Send via WhatsApp / Messenger ─────────────────────────────────────────────


@router.post("/{card_id}/send", response_model=SendCardResult)
async def send_card(
    card_id: uuid.UUID,
    data: SendCardRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send the card link via WhatsApp Cloud API or Facebook Messenger.

    For WhatsApp: uses the WhatsApp Cloud API (Graph API) to send a text
    message with the card link to the specified phone number.
    Requires a WhatsApp account connected in SocialAuto.

    For Messenger: uses the Messenger Platform API to send a message
    to a PSID. Requires a Facebook Page account connected.
    """
    team = await _get_team(current_user, db)
    result = await db.execute(
        select(DigitalCard).where(DigitalCard.id == card_id, DigitalCard.team_id == team.id)
    )
    card = result.scalars().first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    card_url = _card_url(card.share_token)
    msg = data.message or f"Hi! Here's my digital business card: {card_url}"

    if data.platform == "whatsapp":
        if not data.to_phone:
            raise HTTPException(status_code=400, detail="to_phone is required for WhatsApp")
        # Find WhatsApp account
        result = await db.execute(
            select(SocialAccount).where(
                SocialAccount.team_id == team.id,
                SocialAccount.platform == "whatsapp",
                SocialAccount.status == "active",
            )
        )
        wa_account = result.scalars().first()
        if not wa_account:
            raise HTTPException(status_code=400, detail="No active WhatsApp account found. Connect one first.")
        # Get token and phone_number_id from meta_data
        token = wa_account.meta_data.get("access_token", "")
        phone_number_id = wa_account.meta_data.get("phone_number_id", "")
        if not token or not phone_number_id:
            raise HTTPException(status_code=400, detail="WhatsApp account missing access_token or phone_number_id")
        # Send via WhatsApp Cloud API
        url = f"https://graph.facebook.com/v21.0/{phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": data.to_phone.replace("+", "").replace(" ", ""),
            "type": "text",
            "text": {"body": msg},
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, json=payload, headers={"Authorization": f"Bearer {token}"})
            if resp.status_code == 200:
                rdata = resp.json()
                msg_id = rdata.get("messages", [{}])[0].get("id")
                card.share_count += 1
                await db.commit()
                return SendCardResult(success=True, platform="whatsapp", message_id=msg_id)
            else:
                err = resp.text[:200]
                return SendCardResult(success=False, platform="whatsapp", error=err)
        except Exception as e:
            return SendCardResult(success=False, platform="whatsapp", error=str(e))

    elif data.platform == "messenger":
        if not data.to_phone:
            raise HTTPException(status_code=400, detail="to_phone (PSID) is required for Messenger")
        # Find Facebook Page account
        result = await db.execute(
            select(SocialAccount).where(
                SocialAccount.team_id == team.id,
                SocialAccount.platform == "facebook",
                SocialAccount.account_type == "page",
                SocialAccount.status == "active",
            )
        )
        fb_account = result.scalars().first()
        if not fb_account:
            raise HTTPException(status_code=400, detail="No active Facebook Page account found. Connect one first.")
        token = fb_account.meta_data.get("access_token", "")
        page_id = fb_account.account_id
        if not token:
            raise HTTPException(status_code=400, detail="Facebook Page missing access_token")
        # Send via Messenger Platform API
        url = f"https://graph.facebook.com/v21.0/{page_id}/messages"
        payload = {
            "recipient": {"id": data.to_phone},
            "message": {"text": msg},
            "messaging_type": "RESPONSE",
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, json=payload, headers={"Authorization": f"Bearer {token}"})
            if resp.status_code == 200:
                rdata = resp.json()
                msg_id = rdata.get("message_id", "")
                card.share_count += 1
                await db.commit()
                return SendCardResult(success=True, platform="messenger", message_id=msg_id)
            else:
                err = resp.text[:200]
                return SendCardResult(success=False, platform="messenger", error=err)
        except Exception as e:
            return SendCardResult(success=False, platform="messenger", error=str(e))

    else:
        raise HTTPException(status_code=400, detail=f"Unsupported platform: {data.platform}")


# ── Auto-create from brand ─────────────────────────────────────────────────────


@router.post("/from-brand", response_model=DigitalCardOut, status_code=201)
async def create_card_from_brand(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Auto-create a digital card from the team's brand identity and social accounts."""
    from sqlalchemy.orm import selectinload

    from app.models.brand import Brand

    team = await _get_team(current_user, db)

    # Get brand with voice eagerly loaded
    result = await db.execute(
        select(Brand)
        .options(selectinload(Brand.voice), selectinload(Brand.visual))
        .where(Brand.team_id == team.id)
    )
    brand = result.scalars().first()
    if not brand:
        raise HTTPException(status_code=400, detail="No brand found. Create a brand first.")

    visual = brand.visual

    # Get social accounts
    result = await db.execute(
        select(SocialAccount).where(SocialAccount.team_id == team.id, SocialAccount.status == "active")
    )
    accounts = result.scalars().all()

    platform_urls = {
        "facebook": "https://facebook.com/{handle}",
        "instagram": "https://instagram.com/{handle}",
        "linkedin": "https://linkedin.com/company/{handle}",
        "twitter": "https://twitter.com/{handle}",
        "threads": "https://threads.net/@{handle}",
        "tiktok": "https://tiktok.com/@{handle}",
    }
    social_links = []
    for acc in accounts:
        handle = acc.username or acc.display_name or ""
        url_template = platform_urls.get(acc.platform, "")
        url = url_template.format(handle=handle) if handle and url_template else None
        social_links.append({
            "platform": acc.platform,
            "handle": handle,
            "url": url,
            "display_name": acc.display_name or handle,
        })

    # Get messaging pillars as services
    services = []
    if brand.voice and brand.voice.messaging_pillars:
        services = [{"title": p["title"], "description": p["description"]} for p in brand.voice.messaging_pillars]

    email = os.environ.get("BUSINESS_EMAIL", "tbaltzakis@cloudless.gr")
    phone = os.environ.get("BUSINESS_PHONE", "+30 697 777 7838")

    card = DigitalCard(
        team_id=team.id,
        brand_id=brand.id,
        name=brand.name,
        title=brand.industry,
        company=brand.name,
        tagline=brand.tagline,
        description=brand.mission,
        email=email,
        phone=phone,
        website=brand.website_url or "https://cloudless.gr",
        address="Athens, Greece",
        primary_color=visual.primary_color if visual else "#0b1220",
        accent_color=visual.accent_color if visual else "#00fff5",
        logo_url=visual.logo_url if visual else None,
        social_links=social_links,
        services=services,
        share_token=_gen_token(),
    )
    db.add(card)
    await db.commit()
    await db.refresh(card)
    out = DigitalCardOut.model_validate(card)
    out.card_url = _card_url(card.share_token)
    out.vcard = _build_vcard(card)
    return out
