"""Brand identity API — CRUD for brand, voice, visual, guidelines, and assets."""
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
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
    from sqlalchemy import case

    from app.models.user import UserRole
    _tier_rank = case(
        (Team.plan_tier == "enterprise", 4),
        (Team.plan_tier == "business", 3),
        (Team.plan_tier == "pro", 2),
        (Team.plan_tier == "free", 1),
        else_=0,
    )
    result = await db.execute(
        select(Team)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(TeamMember.user_id == user.id)
        .order_by(
            (TeamMember.role == UserRole.OWNER).desc(),
            _tier_rank.desc(),
        )
    )
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

    model_config = ConfigDict(from_attributes=True)


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

    model_config = ConfigDict(from_attributes=True)


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

    model_config = ConfigDict(from_attributes=True)


class BrandGuidelinesOut(BaseModel):
    id: uuid.UUID
    brand_id: uuid.UUID
    content: dict = {}
    share_token: str | None = None
    version: int = 1
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BrandAssetOut(BaseModel):
    id: uuid.UUID
    brand_id: uuid.UUID
    media_asset_id: uuid.UUID | None = None
    asset_type: str
    name: str
    file_url: str | None = None
    asset_metadata: dict = {}
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


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

    model_config = ConfigDict(from_attributes=True)


# ── Brand CRUD ────────────────────────────────────────────────────────────────

@router.get("", response_model=BrandFullOut | None)
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


@router.get("/guidelines/share/{token}", response_model=BrandGuidelinesOut)
async def get_brand_guidelines_by_token(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint — no auth required. Returns guidelines by share token."""
    result = await db.execute(
        select(BrandGuidelines).where(BrandGuidelines.share_token == token)
    )
    guidelines = result.scalars().first()
    if not guidelines:
        raise HTTPException(status_code=404, detail="Brand guidelines not found")
    return guidelines


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


class AdKitRequest(BaseModel):
    headline: str
    subline: str = ""
    cta: str = "Learn more"
    stat: str | None = None
    stat_label: str | None = None
    image_prompt: str | None = None


class AdKitAssetOut(BaseModel):
    id: uuid.UUID
    filename: str | None
    public_url: str | None
    width: int | None
    height: int | None


@router.post("/ad-kit", response_model=list[AdKitAssetOut])
async def generate_ad_brand_kit(
    data: AdKitRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a full branded ad-creative kit in all LinkedIn ad sizes
    (1200x627 landscape, 1080x1080 square, 1080x1350 portrait), persist each
    at full resolution in the media library, and register them as brand assets.
    """
    from app.services.carousel_pipeline import build_ad_brand_kit

    team = await _get_team(current_user, db)
    brand = (await db.execute(select(Brand).where(Brand.team_id == team.id))).scalars().first()

    assets = await build_ad_brand_kit(
        db,
        team_id=team.id, user_id=current_user.id,
        headline=data.headline, subline=data.subline, cta=data.cta,
        stat=data.stat, stat_label=data.stat_label,
        image_prompt=data.image_prompt,
    )
    if brand:
        for a in assets:
            db.add(BrandAsset(
                brand_id=brand.id,
                asset_type=BrandAssetType.social_template,
                name=a.filename,
                media_asset_id=a.id,
                file_url=a.public_url,
                asset_metadata={"kit": "linkedin-ad", "width": a.width, "height": a.height},
            ))
    await db.commit()
    return assets


# ── AI Brand Kit Extractor ────────────────────────────────────────────────────


class ExtractRequest(BaseModel):
    url: str


@router.post("/extract")
async def extract_brand_kit(
    data: ExtractRequest,
    current_user: User = Depends(get_current_user),
):
    """Extract a brand kit draft from a website URL using AI.

    Fetches the website, parses colors/fonts/logo/copy, then uses
    Cloudflare Workers AI to analyze tone and generate positioning/mission.
    Returns a structured draft that the user can review and edit.
    """
    from app.services.brand_extractor import extract_brand_from_url
    from app.services.url_safety import UnsafeUrlError

    try:
        result = await extract_brand_from_url(data.url)
        return result
    except UnsafeUrlError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Brand extraction failed: {e}") from e


# ── AI Voice Analyzer ─────────────────────────────────────────────────────────


class AnalyzeVoiceRequest(BaseModel):
    samples: list[str]


@router.post("/analyze-voice")
async def analyze_voice(
    data: AnalyzeVoiceRequest,
    current_user: User = Depends(get_current_user),
):
    """Analyze content samples and return a voice signature using AI.

    Sends the samples to Cloudflare Workers AI to extract tone dimensions,
    messaging pillars, banned/preferred phrases, and a voice signature.
    """
    from app.services.brand_voice import analyze_brand_voice

    try:
        result = await analyze_brand_voice(data.samples)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Voice analysis failed: {e}") from e


# ── Brand Compliance Scorer ───────────────────────────────────────────────────


class ComplianceRequest(BaseModel):
    content: str
    platform: str | None = None


@router.post("/compliance")
async def score_compliance(
    data: ComplianceRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Score content against the team's brand guidelines.

    Checks banned phrases, preferred phrases, and uses AI to score
    tone/voice match. Returns a 1-5 score with issues and suggestions.
    """
    from app.services.brand_compliance import score_brand_compliance

    brand = await _get_brand(current_user, db)
    brand_dict = {
        "name": brand.name,
        "positioning_statement": brand.positioning_statement,
        "mission": brand.mission,
        "values": brand.values or [],
        "tagline": brand.tagline,
        "target_audience": brand.target_audience or {},
    }
    voice_dict = None
    if brand.voice:
        voice_dict = {
            "tone_dimensions": brand.voice.tone_dimensions or {},
            "messaging_pillars": brand.voice.messaging_pillars or [],
            "banned_phrases": brand.voice.banned_phrases or [],
            "preferred_phrases": brand.voice.preferred_phrases or [],
            "example_content": brand.voice.example_content,
            "voice_signature": brand.voice.voice_signature or {},
        }

    try:
        result = await score_brand_compliance(
            content=data.content,
            brand=brand_dict,
            voice=voice_dict,
            platform=data.platform,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Compliance scoring failed: {e}") from e


# ── AI Logo Generator ─────────────────────────────────────────────────────────


class GenerateLogoRequest(BaseModel):
    description: str = ""
    style: str = "modern minimalist"  # modern minimalist | geometric | abstract | lettermark | wordmark | emblem
    color_scheme: str = ""  # optional: "dark navy and teal" — defaults to brand colors


class GenerateLogoResponse(BaseModel):
    logo_url: str
    asset_id: str
    prompt: str


@router.post("/generate-logo", response_model=GenerateLogoResponse)
async def generate_brand_logo(
    data: GenerateLogoRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a brand logo using Cloudflare Workers AI text-to-image.

    Uses the brand's name, industry, colors, and image style to build an
    optimised prompt. The generated logo is saved to the media library
    and the brand's logo_url is updated automatically.
    """
    import base64

    from app.services.cf_models import CF_TXT2IMG_FREE
    from app.services.inference import _call_workers_ai_image, _get_provider_config, _is_workers_ai_image_model
    from app.services.media_storage import persist_generated_image

    brand = await _get_brand(current_user, db)

    # Build the logo generation prompt from brand context
    brand_name = brand.name
    industry = brand.industry or "technology"
    visual = brand.visual

    if data.color_scheme:
        colors_desc = data.color_scheme
    elif visual and visual.primary_color:
        colors_desc = f"primary {visual.primary_color}"
        if visual.accent_color:
            colors_desc += f" with {visual.accent_color} accent"
    else:
        colors_desc = "professional brand colors"

    style_hint = data.style or "modern minimalist"
    user_desc = data.description.strip()

    prompt = (
        f"Professional logo design for {brand_name}, a {industry} company. "
        f"Style: {style_hint}. Colors: {colors_desc}. "
        f"Clean, scalable, suitable for web and print. "
        f"Simple, memorable, modern. "
    )
    if user_desc:
        prompt += f"Specific direction: {user_desc}. "
    prompt += "No text overlay, no watermark. High quality vector-style logo on transparent or solid background."

    # Generate the image via Cloudflare Workers AI
    team = await _get_team(current_user, db)
    team_id = team.id

    _, model, api_key = await _get_provider_config("cloudflare", team_id, db)
    model = model or CF_TXT2IMG_FREE
    if not _is_workers_ai_image_model(model):
        model = CF_TXT2IMG_FREE

    try:
        result = await _call_workers_ai_image(
            prompt=prompt,
            api_key=api_key,
            model=model,
            width=1024,
            height=1024,
            steps=6,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Logo generation failed: {exc}") from exc

    image_base64 = result["image_base64"]
    image_bytes = base64.b64decode(image_base64)

    # Save to media library
    asset = await persist_generated_image(
        db,
        team_id=team_id,
        user_id=current_user.id,
        image_bytes=image_bytes,
        prompt=prompt,
        source="ai-logo",
        extension=".png",  # logos need alpha — JPEG would flatten transparency
    )

    # Build the logo URL from the media asset
    logo_url = f"/api/v1/media/view/{asset.id}"

    # Update the brand's visual logo_url
    if brand.visual:
        brand.visual.logo_url = logo_url
    else:
        from app.models.brand import BrandVisual
        visual_obj = BrandVisual(brand_id=brand.id, logo_url=logo_url)
        db.add(visual_obj)

    await db.commit()

    return GenerateLogoResponse(
        logo_url=logo_url,
        asset_id=str(asset.id),
        prompt=prompt,
    )


# ── AI Favicon Generator ──────────────────────────────────────────────────────


class GenerateFaviconResponse(BaseModel):
    favicon_url: str
    asset_id: str
    prompt: str


@router.post("/generate-favicon", response_model=GenerateFaviconResponse)
async def generate_brand_favicon(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a favicon from the brand logo using AI.

    Takes the brand's logo concept and generates a simplified 512x512
    square icon suitable for use as a favicon. The result is saved to
    the media library and stored in brand.visual.logo_variants['favicon'].
    """
    import base64

    from app.services.cf_models import CF_TXT2IMG_FREE
    from app.services.inference import _call_workers_ai_image, _get_provider_config, _is_workers_ai_image_model
    from app.services.media_storage import persist_generated_image

    brand = await _get_brand(current_user, db)
    if not brand.visual or not brand.visual.logo_url:
        raise HTTPException(status_code=400, detail="Generate a logo first before creating a favicon")

    brand_name = brand.name
    industry = brand.industry or "technology"
    visual = brand.visual

    colors_desc = f"primary {visual.primary_color or '#0b1220'}"
    if visual.accent_color:
        colors_desc += f" with {visual.accent_color} accent"

    # Build a favicon-optimised prompt — simplified, square, recognizable at small sizes
    prompt = (
        f"Favicon icon for {brand_name}, a {industry} company. "
        f"Derived from the brand logo. Simplified, bold, recognizable at 16x16 pixels. "
        f"Colors: {colors_desc}. "
        f"Square format, centered, minimal detail, high contrast. "
        f"No text, no watermark. Clean vector-style icon on solid background."
    )

    team = await _get_team(current_user, db)
    team_id = team.id

    _, model, api_key = await _get_provider_config("cloudflare", team_id, db)
    model = model or CF_TXT2IMG_FREE
    if not _is_workers_ai_image_model(model):
        model = CF_TXT2IMG_FREE

    try:
        result = await _call_workers_ai_image(
            prompt=prompt,
            api_key=api_key,
            model=model,
            width=512,
            height=512,
            steps=6,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Favicon generation failed: {exc}") from exc

    image_base64 = result["image_base64"]
    image_bytes = base64.b64decode(image_base64)

    # Save to media library
    asset = await persist_generated_image(
        db,
        team_id=team_id,
        user_id=current_user.id,
        image_bytes=image_bytes,
        prompt=prompt,
        source="ai-favicon",
        extension=".png",  # favicons need alpha — JPEG would flatten transparency
    )

    favicon_url = f"/api/v1/media/view/{asset.id}"

    # Store favicon URL in logo_variants
    variants = dict(brand.visual.logo_variants or {})
    variants["favicon"] = favicon_url
    brand.visual.logo_variants = variants

    await db.commit()

    return GenerateFaviconResponse(
        favicon_url=favicon_url,
        asset_id=str(asset.id),
        prompt=prompt,
    )


# ── Brand Monitoring & Intelligence ───────────────────────────────────────────

from app.models.brand_monitoring import BrandMention, CompetitorSnapshot  # noqa: E402
from app.services.brand_monitoring import (  # noqa: E402
    calculate_health_score,
    collect_mentions,
    snapshot_competitor,
)


class MentionOut(BaseModel):
    id: uuid.UUID
    platform: str
    author: str | None = None
    content: str
    url: str | None = None
    sentiment: str | None = None
    sentiment_score: float | None = None
    engagement: int = 0
    mentioned_at: datetime | None = None
    extra_data: dict = {}

    model_config = ConfigDict(from_attributes=True)


class CompetitorSnapshotOut(BaseModel):
    id: uuid.UUID
    competitor_name: str
    platform: str
    follower_count: int | None = None
    engagement_rate: float | None = None
    post_count: int | None = None
    top_post_content: str | None = None
    top_post_engagement: int | None = None
    snapshot_at: datetime

    model_config = ConfigDict(from_attributes=True)


class HealthScoreOut(BaseModel):
    overall: float
    sentiment: float
    reach: float
    share_of_voice: float
    engagement: float
    consistency: float
    mention_count: int
    total_engagement: int
    avg_sentiment: float


@router.get("/mentions", response_model=list[MentionOut])
async def list_brand_mentions(
    platform: str | None = None,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List brand mentions from monitoring sources."""
    brand = await _get_brand(current_user, db)
    query = select(BrandMention).where(BrandMention.brand_id == brand.id)
    if platform:
        query = query.where(BrandMention.platform == platform)
    query = query.order_by(BrandMention.mentioned_at.desc()).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/mentions/collect", response_model=list[MentionOut])
async def collect_brand_mentions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger mention collection from all sources (Twitter, Reddit, Google News)."""
    import os

    brand = await _get_brand(current_user, db)
    twitter_token = os.environ.get("TWITTER_BEARER_TOKEN")
    mentions = await collect_mentions(db, brand, twitter_token)
    return mentions


@router.get("/competitors", response_model=list[CompetitorSnapshotOut])
async def list_competitor_snapshots(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List competitor snapshots."""
    brand = await _get_brand(current_user, db)
    result = await db.execute(
        select(CompetitorSnapshot)
        .where(CompetitorSnapshot.brand_id == brand.id)
        .order_by(CompetitorSnapshot.snapshot_at.desc())
    )
    return result.scalars().all()


@router.post("/competitors/snapshot", response_model=CompetitorSnapshotOut)
async def take_competitor_snapshot(
    competitor_name: str,
    platform: str = "twitter",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Take a snapshot of a competitor's metrics."""
    brand = await _get_brand(current_user, db)
    snapshot = await snapshot_competitor(db, brand, competitor_name, platform)
    return snapshot


@router.get("/health", response_model=HealthScoreOut)
async def get_brand_health(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Calculate and return brand health score."""
    from sqlalchemy import func

    from app.models.content import Post, PostStatus

    brand = await _get_brand(current_user, db)

    # Get mentions from last 30 days
    # Note: brand_mentions.mentioned_at is timestamp without time zone (naive),
    # while posts.created_at is timestamp with time zone (aware).
    # Use naive UTC for the mentions comparison and aware UTC for posts.
    thirty_days_ago_aware = datetime.now(UTC) - timedelta(days=30)
    thirty_days_ago_naive = datetime.utcnow() - timedelta(days=30)
    mentions_result = await db.execute(
        select(BrandMention)
        .where(BrandMention.brand_id == brand.id, BrandMention.mentioned_at >= thirty_days_ago_naive)
    )
    mentions = mentions_result.scalars().all()

    # Get competitor snapshots
    competitors_result = await db.execute(
        select(CompetitorSnapshot).where(CompetitorSnapshot.brand_id == brand.id)
    )
    competitors = competitors_result.scalars().all()

    # Get post count and engagement from last 30 days
    posts_result = await db.execute(
        select(func.count(Post.id))
        .where(
            Post.team_id == brand.team_id,
            Post.status == PostStatus.PUBLISHED,
            Post.created_at >= thirty_days_ago_aware,
        )
    )
    post_count = posts_result.scalar() or 0

    health = calculate_health_score(
        mentions=mentions,
        competitor_snapshots=competitors,
        post_count_30d=post_count,
    )
    return health


# ── Autonomous Brand Content (Phase 6) ────────────────────────────────────────

from app.services.brand_agent import run_autopilot  # noqa: E402
from app.services.trend_scout import scout_trends  # noqa: E402


class AutopilotResultOut(BaseModel):
    created_count: int = 0
    skipped_count: int = 0
    slots_filled: int = 0
    drafts: list[dict] = []
    error: str | None = None
    message: str | None = None


class TrendScoutOut(BaseModel):
    twitter_trends: list[dict] = []
    reddit_hot: list[dict] = []
    top_posts: list[dict] = []


@router.post("/autopilot/run", response_model=AutopilotResultOut)
async def run_brand_autopilot(
    days: int = 7,
    min_compliance_score: int = 4,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Run the autonomous brand content agent.

    Finds empty calendar slots, generates on-brand content from messaging
    pillars, checks compliance, and creates draft posts.
    """
    brand = await _get_brand(current_user, db)
    result = await run_autopilot(
        db=db,
        team_id=brand.team_id,
        user_id=current_user.id,
        days=days,
        min_compliance_score=min_compliance_score,
    )
    return result


@router.get("/trends", response_model=TrendScoutOut)
async def get_trends(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get trending topics from Twitter/X, Reddit, and top-performing posts."""
    import os

    brand = await _get_brand(current_user, db)
    twitter_token = os.environ.get("TWITTER_BEARER_TOKEN")
    result = await scout_trends(db, brand.team_id, twitter_token)
    return result


# ── Digital Business Card (vCard 4.0 + public page data) ──────────────────────


class DigitalCardOut(BaseModel):
    """Public digital business card data — served without auth via share token."""

    model_config = ConfigDict(from_attributes=True)

    brand_name: str
    tagline: str | None = None
    mission: str | None = None
    industry: str | None = None
    website: str | None = None
    # Contact info (from brand or env)
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    # Visual identity
    primary_color: str | None = None
    accent_color: str | None = None
    logo_url: str | None = None
    # Social accounts
    socials: list[dict] = []
    # Messaging pillars
    pillars: list[dict] = []
    # vCard 4.0 text
    vcard: str = ""
    # Share info
    share_token: str | None = None
    card_url: str | None = None


def _build_vcard(
    brand: Brand,
    visual: BrandVisual | None,
    email: str | None,
    phone: str | None,
    website: str | None,
    socials: list[dict],
) -> str:
    """Build a vCard 4.0 (RFC 6350) string."""
    lines = [
        "BEGIN:VCARD",
        "VERSION:4.0",
        f"FN:{brand.name}",
        f"ORG:{brand.name}",
    ]
    if brand.industry:
        lines.append(f"TITLE:{brand.industry}")
    if email:
        lines.append(f"EMAIL;TYPE=work;PREF=1:{email}")
    if phone:
        lines.append(f"TEL;TYPE=work,voice;VALUE=uri;PREF=1:tel:{phone.replace(' ', '')}")
    if website:
        lines.append(f"URL:{website}")
    if brand.tagline:
        lines.append(f"NOTE:{brand.tagline}")
    # Social profiles as URL entries
    for s in socials:
        url = s.get("url")
        if url:
            lines.append(f"URL;TYPE={s.get('platform', 'social')}:{url}")
    # Logo as URI if available
    if visual and visual.logo_url:
        lines.append(f"LOGO;VALUE=uri:{visual.logo_url}")
    lines.append("END:VCARD")
    return "\r\n".join(lines)


@router.get("/digital-card", response_model=DigitalCardOut)
async def get_digital_card(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the digital business card for the current team (auth required)."""
    import os

    brand = await _get_brand(current_user, db)
    visual = brand.visual
    guidelines = brand.guidelines

    # Get social accounts
    from app.models.social_account import SocialAccount

    result = await db.execute(
        select(SocialAccount)
        .where(SocialAccount.team_id == brand.team_id, SocialAccount.status == "active")
        .order_by(SocialAccount.platform)
    )
    accounts = result.scalars().all()

    # Build socials list
    platform_urls = {
        "facebook": "https://facebook.com/{handle}",
        "instagram": "https://instagram.com/{handle}",
        "linkedin": "https://linkedin.com/company/{handle}",
        "twitter": "https://twitter.com/{handle}",
        "threads": "https://threads.net/@{handle}",
        "tiktok": "https://tiktok.com/@{handle}",
    }
    socials = []
    for acc in accounts:
        handle = acc.username or acc.display_name or ""
        url_template = platform_urls.get(acc.platform, "")
        url = url_template.format(handle=handle) if handle and url_template else None
        socials.append({
            "platform": acc.platform,
            "handle": handle,
            "display_name": acc.display_name or handle,
            "url": url,
            "account_type": acc.account_type,
        })

    email = os.environ.get("BUSINESS_EMAIL", "tbaltzakis@cloudless.gr")
    phone = os.environ.get("BUSINESS_PHONE", "+30 697 777 7838")
    website = brand.website_url or "https://cloudless.gr"
    address = "Athens, Greece"

    vcard = _build_vcard(brand, visual, email, phone, website, socials)

    share_token = guidelines.share_token if guidelines else None
    base_url = os.environ.get("FRONTEND_URL", "http://localhost:8082")
    card_url = f"{base_url}/card/{share_token}" if share_token else None

    pillars = []
    if brand.voice and brand.voice.messaging_pillars:
        pillars = brand.voice.messaging_pillars

    return DigitalCardOut(
        brand_name=brand.name,
        tagline=brand.tagline,
        mission=brand.mission,
        industry=brand.industry,
        website=website,
        email=email,
        phone=phone,
        address=address,
        primary_color=visual.primary_color if visual else None,
        accent_color=visual.accent_color if visual else None,
        logo_url=visual.logo_url if visual else None,
        socials=socials,
        pillars=pillars,
        vcard=vcard,
        share_token=share_token,
        card_url=card_url,
    )


@router.get("/digital-card/{token}", response_model=DigitalCardOut)
async def get_digital_card_public(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint — get digital card by share token (no auth required)."""
    import os

    result = await db.execute(
        select(BrandGuidelines).where(BrandGuidelines.share_token == token)
    )
    guidelines = result.scalars().first()
    if not guidelines:
        raise HTTPException(status_code=404, detail="Digital card not found")

    brand = guidelines.brand
    visual = brand.visual

    from app.models.social_account import SocialAccount

    result = await db.execute(
        select(SocialAccount)
        .where(SocialAccount.team_id == brand.team_id, SocialAccount.status == "active")
        .order_by(SocialAccount.platform)
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
    socials = []
    for acc in accounts:
        handle = acc.username or acc.display_name or ""
        url_template = platform_urls.get(acc.platform, "")
        url = url_template.format(handle=handle) if handle and url_template else None
        socials.append({
            "platform": acc.platform,
            "handle": handle,
            "display_name": acc.display_name or handle,
            "url": url,
            "account_type": acc.account_type,
        })

    email = os.environ.get("BUSINESS_EMAIL", "tbaltzakis@cloudless.gr")
    phone = os.environ.get("BUSINESS_PHONE", "+30 697 777 7838")
    website = brand.website_url or "https://cloudless.gr"

    vcard = _build_vcard(brand, visual, email, phone, website, socials)

    base_url = os.environ.get("FRONTEND_URL", "http://localhost:8082")
    card_url = f"{base_url}/card/{token}"

    pillars = []
    if brand.voice and brand.voice.messaging_pillars:
        pillars = brand.voice.messaging_pillars

    return DigitalCardOut(
        brand_name=brand.name,
        tagline=brand.tagline,
        mission=brand.mission,
        industry=brand.industry,
        website=website,
        email=email,
        phone=phone,
        address="Athens, Greece",
        primary_color=visual.primary_color if visual else None,
        accent_color=visual.accent_color if visual else None,
        logo_url=visual.logo_url if visual else None,
        socials=socials,
        pillars=pillars,
        vcard=vcard,
        share_token=token,
        card_url=card_url,
    )
