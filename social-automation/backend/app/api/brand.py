"""Brand identity API — CRUD for brand, voice, visual, guidelines, and assets."""
import secrets
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth import get_current_user
from app.db.session import get_db
from app.models.brand import Brand, BrandAsset, BrandAssetType, BrandGuidelines, BrandVisual, BrandVoice
from app.models.user import Team, TeamMember, User

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_team(user: User, db: AsyncSession) -> Team:
    result = await db.execute(select(Team).join(TeamMember).where(TeamMember.user_id == user.id))
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


async def _get_brand(user: User, db: AsyncSession) -> Brand:
    team = await _get_team(user, db)
    result = await db.execute(
        select(Brand)
        .where(Brand.team_id == team.id)
        .options(
            selectinload(Brand.voice),
            selectinload(Brand.visual),
            selectinload(Brand.guidelines),
            selectinload(Brand.assets),
        )
    )
    brand = result.scalars().first()
    if not brand:
        raise HTTPException(status_code=404, detail="Brand not found — create one first")
    return brand


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class BrandCreate(BaseModel):
    name: str
    industry: str | None = None
    positioning_statement: str | None = None
    mission: str | None = None
    values: list[str] = []
    target_audience: dict = {}
    competitor_names: list[str] = []
    tagline: str | None = None
    website_url: str | None = None


class BrandUpdate(BaseModel):
    name: str | None = None
    industry: str | None = None
    positioning_statement: str | None = None
    mission: str | None = None
    values: list[str] | None = None
    target_audience: dict | None = None
    competitor_names: list[str] | None = None
    tagline: str | None = None
    website_url: str | None = None


class BrandVoiceUpdate(BaseModel):
    tone_dimensions: dict = {}
    messaging_pillars: list[dict] = []
    banned_phrases: list[str] = []
    preferred_phrases: list[str] = []
    example_content: str | None = None
    voice_signature: dict = {}


class BrandVisualUpdate(BaseModel):
    primary_color: str | None = None
    accent_color: str | None = None
    neutral_colors: list[str] = []
    font_heading: str | None = None
    font_body: str | None = None
    type_scale: dict = {}
    logo_url: str | None = None
    logo_variants: dict = {}
    image_style: str | None = None
    photography_direction: str | None = None


class BrandAssetCreate(BaseModel):
    asset_type: BrandAssetType = BrandAssetType.other
    name: str
    media_asset_id: uuid.UUID | None = None
    file_url: str | None = None
    asset_metadata: dict = {}


class BrandOut(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    name: str
    industry: str | None = None
    positioning_statement: str | None = None
    mission: str | None = None
    values: list = []
    target_audience: dict = {}
    competitor_names: list = []
    tagline: str | None = None
    website_url: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BrandVoiceOut(BaseModel):
    id: uuid.UUID
    brand_id: uuid.UUID
    tone_dimensions: dict = {}
    messaging_pillars: list = []
    banned_phrases: list = []
    preferred_phrases: list = []
    example_content: str | None = None
    voice_signature: dict = {}
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BrandVisualOut(BaseModel):
    id: uuid.UUID
    brand_id: uuid.UUID
    primary_color: str | None = None
    accent_color: str | None = None
    neutral_colors: list = []
    font_heading: str | None = None
    font_body: str | None = None
    type_scale: dict = {}
    logo_url: str | None = None
    logo_variants: dict = {}
    image_style: str | None = None
    photography_direction: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BrandGuidelinesOut(BaseModel):
    id: uuid.UUID
    brand_id: uuid.UUID
    content: dict = {}
    share_token: str | None = None
    version: int = 1
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BrandAssetOut(BaseModel):
    id: uuid.UUID
    brand_id: uuid.UUID
    media_asset_id: uuid.UUID | None = None
    asset_type: str
    name: str
    file_url: str | None = None
    asset_metadata: dict = {}
    created_at: datetime

    class Config:
        from_attributes = True


class BrandFullOut(BaseModel):
    """Full brand profile with nested voice, visual, guidelines, and assets."""
    id: uuid.UUID
    team_id: uuid.UUID
    name: str
    industry: str | None = None
    positioning_statement: str | None = None
    mission: str | None = None
    values: list = []
    target_audience: dict = {}
    competitor_names: list = []
    tagline: str | None = None
    website_url: str | None = None
    voice: BrandVoiceOut | None = None
    visual: BrandVisualOut | None = None
    guidelines: BrandGuidelinesOut | None = None
    assets: list[BrandAssetOut] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ── Brand CRUD ────────────────────────────────────────────────────────────────

@router.get("", response_model=BrandFullOut)
async def get_brand(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the current team's brand with all nested data."""
    try:
        brand = await _get_brand(current_user, db)
        return brand
    except HTTPException as exc:
        if exc.status_code == 404 and "Brand not found" in exc.detail:
            return None  # Return null so frontend can show onboarding
        raise


@router.post("", response_model=BrandFullOut)
async def create_brand(
    data: BrandCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a brand for the current team. One brand per team."""
    team = await _get_team(current_user, db)

    existing = await db.execute(select(Brand).where(Brand.team_id == team.id))
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail="Brand already exists for this team")

    brand = Brand(
        team_id=team.id,
        name=data.name,
        industry=data.industry,
        positioning_statement=data.positioning_statement,
        mission=data.mission,
        values=data.values,
        target_audience=data.target_audience,
        competitor_names=data.competitor_names,
        tagline=data.tagline,
        website_url=data.website_url,
    )
    db.add(brand)
    await db.flush()

    # Create empty voice and visual records
    voice = BrandVoice(brand_id=brand.id)
    visual = BrandVisual(brand_id=brand.id)
    db.add(voice)
    db.add(visual)
    await db.commit()
    await db.refresh(brand)

    # Reload with relationships
    result = await db.execute(
        select(Brand)
        .where(Brand.id == brand.id)
        .options(
            selectinload(Brand.voice),
            selectinload(Brand.visual),
            selectinload(Brand.guidelines),
            selectinload(Brand.assets),
        )
    )
    return result.scalars().first()


@router.put("", response_model=BrandOut)
async def update_brand(
    data: BrandUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update the current team's brand identity fields."""
    brand = await _get_brand(current_user, db)

    update_fields = data.model_dump(exclude_unset=True)
    for field, value in update_fields.items():
        setattr(brand, field, value)

    await db.commit()
    await db.refresh(brand)
    return brand


@router.delete("", status_code=204)
async def delete_brand(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete the current team's brand and all related data."""
    brand = await _get_brand(current_user, db)
    await db.delete(brand)
    await db.commit()


# ── Brand Voice ───────────────────────────────────────────────────────────────

@router.get("/voice", response_model=BrandVoiceOut)
async def get_brand_voice(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    brand = await _get_brand(current_user, db)
    if not brand.voice:
        raise HTTPException(status_code=404, detail="Brand voice not found")
    return brand.voice


@router.put("/voice", response_model=BrandVoiceOut)
async def update_brand_voice(
    data: BrandVoiceUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update the brand voice (tone, banned phrases, messaging pillars)."""
    brand = await _get_brand(current_user, db)
    if not brand.voice:
        voice = BrandVoice(brand_id=brand.id, **data.model_dump())
        db.add(voice)
    else:
        update_fields = data.model_dump(exclude_unset=True)
        for field, value in update_fields.items():
            setattr(brand.voice, field, value)

    await db.commit()
    await db.refresh(brand.voice)
    return brand.voice


# ── Brand Visual ──────────────────────────────────────────────────────────────

@router.get("/visual", response_model=BrandVisualOut)
async def get_brand_visual(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    brand = await _get_brand(current_user, db)
    if not brand.visual:
        raise HTTPException(status_code=404, detail="Brand visual not found")
    return brand.visual


@router.put("/visual", response_model=BrandVisualOut)
async def update_brand_visual(
    data: BrandVisualUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update the brand visual identity (colors, fonts, logo)."""
    brand = await _get_brand(current_user, db)
    if not brand.visual:
        visual = BrandVisual(brand_id=brand.id, **data.model_dump())
        db.add(visual)
    else:
        update_fields = data.model_dump(exclude_unset=True)
        for field, value in update_fields.items():
            setattr(brand.visual, field, value)

    await db.commit()
    await db.refresh(brand.visual)
    return brand.visual


# ── Brand Guidelines ──────────────────────────────────────────────────────────

@router.get("/guidelines", response_model=BrandGuidelinesOut)
async def get_brand_guidelines(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    brand = await _get_brand(current_user, db)
    if not brand.guidelines:
        raise HTTPException(status_code=404, detail="Brand guidelines not compiled yet")
    return brand.guidelines


@router.post("/guidelines/compile", response_model=BrandGuidelinesOut)
async def compile_brand_guidelines(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Compile brand guidelines from the brand, voice, and visual records."""
    brand = await _get_brand(current_user, db)

    content = {
        "brand": {
            "name": brand.name,
            "industry": brand.industry,
            "positioning": brand.positioning_statement,
            "mission": brand.mission,
            "values": brand.values,
            "tagline": brand.tagline,
            "target_audience": brand.target_audience,
        },
        "voice": {},
        "visual": {},
    }

    if brand.voice:
        content["voice"] = {
            "tone_dimensions": brand.voice.tone_dimensions,
            "messaging_pillars": brand.voice.messaging_pillars,
            "banned_phrases": brand.voice.banned_phrases,
            "preferred_phrases": brand.voice.preferred_phrases,
            "example_content": brand.voice.example_content,
        }

    if brand.visual:
        content["visual"] = {
            "primary_color": brand.visual.primary_color,
            "accent_color": brand.visual.accent_color,
            "neutral_colors": brand.visual.neutral_colors,
            "font_heading": brand.visual.font_heading,
            "font_body": brand.visual.font_body,
            "type_scale": brand.visual.type_scale,
            "logo_url": brand.visual.logo_url,
            "image_style": brand.visual.image_style,
            "photography_direction": brand.visual.photography_direction,
        }

    if brand.guidelines:
        brand.guidelines.content = content
        brand.guidelines.version += 1
        brand.guidelines.updated_at = datetime.now(UTC)
    else:
        guidelines = BrandGuidelines(
            brand_id=brand.id,
            content=content,
            share_token=secrets.token_urlsafe(32),
            version=1,
        )
        db.add(guidelines)

    await db.commit()

    result = await db.execute(
        select(BrandGuidelines).where(BrandGuidelines.brand_id == brand.id)
    )
    return result.scalars().first()


# ── Brand Assets ──────────────────────────────────────────────────────────────

@router.get("/assets", response_model=list[BrandAssetOut])
async def list_brand_assets(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    brand = await _get_brand(current_user, db)
    return brand.assets


@router.post("/assets", response_model=BrandAssetOut)
async def create_brand_asset(
    data: BrandAssetCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add a brand asset (logo, template, OG image, etc.)."""
    brand = await _get_brand(current_user, db)
    asset = BrandAsset(
        brand_id=brand.id,
        asset_type=data.asset_type,
        name=data.name,
        media_asset_id=data.media_asset_id,
        file_url=data.file_url,
        asset_metadata=data.asset_metadata,
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    return asset


@router.delete("/assets/{asset_id}", status_code=204)
async def delete_brand_asset(
    asset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    brand = await _get_brand(current_user, db)
    result = await db.execute(
        select(BrandAsset).where(BrandAsset.id == asset_id, BrandAsset.brand_id == brand.id)
    )
    asset = result.scalars().first()
    if not asset:
        raise HTTPException(status_code=404, detail="Brand asset not found")
    await db.delete(asset)
    await db.commit()
