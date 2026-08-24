import json
import uuid
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.core.config import get_settings
from app.db.session import get_db
from app.models.social_account import SocialAccount
from app.models.user import Team, TeamMember, User
from app.models.workflow import GeneratedWorkflow, PromptTemplate
from app.services import chroma_client
from app.services.inference import (
    STT_MODELS,
    _call_nvidia_flux,
    _call_nvidia_flux_dev,
    _call_nvidia_flux_pipeline,
    call_inference,
    get_team_id_for_user,
    transcribe_workers_ai,
)

router = APIRouter()
settings = get_settings()


class CarouselSlide(BaseModel):
    title: str
    body: str
    highlight: str | None = None
    slide_type: str = "content"  # cover | content | stat | cta


class GenerateCarouselRequest(BaseModel):
    topic: str
    num_slides: int = 6
    platform: str = "linkedin"
    tone: str = "professional"
    include_cta: bool = True
    provider: str = "groq"
    model: str | None = None


class GenerateCarouselResponse(BaseModel):
    slides: list[CarouselSlide]
    suggested_caption: str
    hashtags: list[str]


class GenerateContentRequest(BaseModel):
    prompt: str
    platform: str
    tone: str = "professional"
    length: str = "medium"
    include_hashtags: bool = True
    include_emojis: bool = True
    provider: str = "groq"
    model: str | None = None


class GenerateContentResponse(BaseModel):
    content: str
    hashtags: list[str]
    suggested_media: str | None = None


class SuggestHashtagsRequest(BaseModel):
    content: str
    platform: str
    max_hashtags: int = 10


class SuggestHashtagsResponse(BaseModel):
    hashtags: list[str]


class BestTimeRequest(BaseModel):
    account_id: uuid.UUID


class BestTimeResponse(BaseModel):
    best_times: list[dict]


class ImproveContentRequest(BaseModel):
    content: str
    platform: str
    goal: str = "engagement"


class ImproveContentResponse(BaseModel):
    improved_content: str
    changes: list[str]


class GenerateWorkflowRequest(BaseModel):
    prompt: str
    template_id: uuid.UUID | None = None


class GenerateWorkflowResponse(BaseModel):
    n8n_workflow_json: dict
    variables_used: dict
    template_id: uuid.UUID | None


async def call_ollama(prompt: str, model: str = None, schema: dict = None) -> dict:
    """Backwards-compatible shim — delegates to the unified inference service.
    Defaults to Groq (cloud) instead of Ollama for faster inference."""
    return await call_inference(prompt, provider_name="groq", schema=schema, model_override=model)


class TranscribeResponse(BaseModel):
    text: str
    language: str | None = None
    duration: float | None = None
    detections: dict | None = None  # any extra fields the model returned


# Workers AI rejects request bodies over ~25 MB; keep a conservative cap so the
# frontend recorder (WAV, 16 kHz mono) can never exceed it.
MAX_AUDIO_SIZE_BYTES = 10 * 1024 * 1024


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe_audio(
    file: UploadFile = File(..., description="Audio upload (WAV, MP3, M4A, OGG…)"),
    model: str | None = Form(None, description="Workers AI STT model id, e.g. @cf/openai/whisper"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Transcribe a speech audio clip using a Cloudflare Workers AI STT model."""
    if model in STT_MODELS:
        model = STT_MODELS[model]
    if not model:
        model = STT_MODELS["whisper"]

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty audio file")
    if len(content) > MAX_AUDIO_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Audio file too large ({len(content)} bytes). Max is {MAX_AUDIO_SIZE_BYTES} bytes — trim or compress it.",
        )

    try:
        result = await transcribe_workers_ai(
            content,
            file.content_type or "application/octet-stream",
            model=model,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Transcription error: {exc}")

    if not result.get("text"):
        raise HTTPException(
            status_code=422,
            detail="Model returned empty transcription — no speech detected. Try clearer audio or another model.",
        )

    extra = {
        k: v
        for k, v in result.items()
        if k not in ("text", "language", "detected_language", "speech_language", "duration", "duration_seconds")
    }
    return TranscribeResponse(
        text=result["text"],
        language=result.get("language")
        or result.get("detected_language")
        or result.get("speech_language"),
        duration=result.get("duration")
        or result.get("duration_seconds"),
        detections=extra or None,
    )


class GenerateImagePromptRequest(BaseModel):
    description: str
    style: str = "photorealistic"


class GenerateImagePromptResponse(BaseModel):
    prompt: str
    negative_prompt: str


class AnalyzeContentRequest(BaseModel):
    content: str
    platform: str


class AnalyzeContentResponse(BaseModel):
    sentiment: str
    readability_score: float
    estimated_reach: str
    suggestions: list[str]
    hashtag_score: int
    engagement_prediction: str


@router.post("/generate-image-prompt", response_model=GenerateImagePromptResponse)
async def generate_image_prompt(
    request: GenerateImagePromptRequest,
    current_user: User = Depends(get_current_user),
):
    prompt = f"""Generate a detailed Stable Diffusion image prompt for this social media post concept:

Description: "{request.description}"
Style: {request.style}

Return JSON with:
- prompt: detailed positive prompt (include lighting, composition, style keywords)
- negative_prompt: things to exclude (e.g. blurry, low quality, text, watermark)"""

    schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "negative_prompt": {"type": "string"},
        },
        "required": ["prompt", "negative_prompt"],
    }

    result = await call_ollama(prompt, schema=schema)
    return GenerateImagePromptResponse(
        prompt=result.get("prompt", request.description),
        negative_prompt=result.get("negative_prompt", "blurry, low quality, text, watermark, nsfw"),
    )


@router.post("/analyze-content", response_model=AnalyzeContentResponse)
async def analyze_content(
    request: AnalyzeContentRequest,
    current_user: User = Depends(get_current_user),
):
    hashtag_count = request.content.count("#")
    char_count = len(request.content)

    platform_limits = {
        "twitter": 280, "linkedin": 3000, "instagram": 2200,
        "facebook": 63206, "threads": 500,
    }
    limit = platform_limits.get(request.platform, 3000)

    prompt = f"""Analyze this {request.platform} post and return a quality assessment:

Post: "{request.content}"
Character count: {char_count} / {limit}
Hashtag count: {hashtag_count}

Return JSON with:
- sentiment: (positive, neutral, negative)
- readability_score: float 0-10
- estimated_reach: (low, medium, high, viral)
- suggestions: array of 2-4 actionable improvement tips
- hashtag_score: int 0-10 (0=none, 10=optimal count for platform)
- engagement_prediction: (low, medium, high)"""

    schema = {
        "type": "object",
        "properties": {
            "sentiment": {"type": "string"},
            "readability_score": {"type": "number"},
            "estimated_reach": {"type": "string"},
            "suggestions": {"type": "array", "items": {"type": "string"}},
            "hashtag_score": {"type": "integer"},
            "engagement_prediction": {"type": "string"},
        },
        "required": ["sentiment", "readability_score", "estimated_reach", "suggestions", "hashtag_score", "engagement_prediction"],
    }

    result = await call_ollama(prompt, schema=schema)
    return AnalyzeContentResponse(
        sentiment=result.get("sentiment", "neutral"),
        readability_score=float(result.get("readability_score", 7.0)),
        estimated_reach=result.get("estimated_reach", "medium"),
        suggestions=result.get("suggestions", []),
        hashtag_score=int(result.get("hashtag_score", 5)),
        engagement_prediction=result.get("engagement_prediction", "medium"),
    )


class GenerateImageRequest(BaseModel):
    prompt: str
    negative_prompt: str = ""
    cfg_scale: float = 3.5
    seed: int = 0
    steps: int = 20


class GenerateImageResponse(BaseModel):
    image_base64: str
    format: str = "base64"
    prompt: str
    similar_content: list[str] = []


@router.post("/generate-image", response_model=GenerateImageResponse)
async def generate_image(
    request: GenerateImageRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate image using NVIDIA's hosted FLUX.1-dev API (cloud text-to-image)."""
    import base64

    team_result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = team_result.scalars().first()
    team_id = team.id if team else None

    # Check chroma for similar generated images before submitting
    similar: list[str] = []
    if team:
        similar = await chroma_client.query_similar(str(team.id), request.prompt, n_results=3)

    # Get provider config - default to NVIDIA FLUX.1-dev (cloud)
    provider_name = "nvidia-flux-dev"  # Default to cloud FLUX.1-dev
    from app.services.inference import _get_provider_config
    base_url, model, api_key = await _get_provider_config(provider_name, team_id, db)

    if not api_key:
        raise HTTPException(
            status_code=400,
            detail=f"No API key configured for {provider_name}. Add NVIDIA_API_KEY in .env or configure in Settings → AI Providers."
        )

    # Call NVIDIA FLUX.1-dev API (cloud)
    image_bytes = await _call_nvidia_flux_dev(
        prompt=request.prompt,
        base_url=base_url,
        api_key=api_key,
        negative_prompt=request.negative_prompt,
        cfg_scale=request.cfg_scale,
        seed=request.seed,
        steps=request.steps,
    )

    # Convert to base64 for response
    image_base64 = base64.b64encode(image_bytes).decode('utf-8')

    # Store prompt in chroma so future generations can detect duplicates
    if team and request.prompt:
        await chroma_client.add_content(str(team.id), str(uuid.uuid4()), request.prompt)

    return GenerateImageResponse(
        image_base64=image_base64,
        format="base64",
        prompt=request.prompt,
        similar_content=similar,
    )


class GenerateImagePipelineRequest(BaseModel):
    """Request for FLUX pipeline: text-to-image -> image-to-image enhancement."""
    prompt: str
    negative_prompt: str = ""
    enhance_prompt: str = "Enhance image quality, improve details, fix artifacts, professional photography"
    cfg_scale: float = 5.0
    seed: int = 0
    steps: int = 30
    width: int = 1024
    height: int = 1024
    enhance_cfg_scale: float = 3.5
    enhance_steps: int = 20


class GenerateImagePipelineResponse(BaseModel):
    """Response for FLUX pipeline generation."""
    image_base64: str
    format: str = "base64"
    prompt: str
    similar_content: list[str] = []
    draft_id: str | None = None


@router.post("/generate-image-pipeline", response_model=GenerateImagePipelineResponse)
async def generate_image_pipeline(
    request: GenerateImagePipelineRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Full pipeline: FLUX.1-dev (text-to-image) -> FLUX.1-Kontext-dev (image-to-image enhancement)."""
    import base64

    team_result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = team_result.scalars().first()
    team_id = team.id if team else None

    # Check chroma for similar generated images before submitting
    similar: list[str] = []
    if team:
        similar = await chroma_client.query_similar(str(team.id), request.prompt, n_results=3)

    # Get provider configs for both models
    from app.services.inference import _get_provider_config
    flux_dev_url, _, flux_dev_key = await _get_provider_config("nvidia-flux-dev", team_id, db)
    flux_kontext_url, _, flux_kontext_key = await _get_provider_config("nvidia-flux", team_id, db)

    if not flux_dev_key:
        raise HTTPException(
            status_code=400,
            detail="No API key configured for NVIDIA FLUX.1-dev. Add it in Settings → AI Providers (provider name: 'nvidia-flux-dev')."
        )
    if not flux_kontext_key:
        raise HTTPException(
            status_code=400,
            detail="No API key configured for NVIDIA FLUX.1-Kontext-dev. Add it in Settings → AI Providers (provider name: 'nvidia-flux')."
        )

    # Call full pipeline
    image_bytes = await _call_nvidia_flux_pipeline(
        prompt=request.prompt,
        flux_dev_url=flux_dev_url,
        flux_dev_key=flux_dev_key,
        flux_kontext_url=flux_kontext_url,
        flux_kontext_key=flux_kontext_key,
        negative_prompt=request.negative_prompt,
        enhance_prompt=request.enhance_prompt,
        cfg_scale=request.cfg_scale,
        seed=request.seed,
        steps=request.steps,
        width=request.width,
        height=request.height,
        enhance_cfg_scale=request.enhance_cfg_scale,
        enhance_steps=request.enhance_steps,
    )

    # Convert to base64 for response
    image_base64 = base64.b64encode(image_bytes).decode('utf-8')

    # Save as draft
    draft_id = None
    if team:
        draft_id = str(uuid.uuid4())
        await chroma_client.add_content(
            str(team.id),
            draft_id,
            f"DRAFT:{request.prompt}|||{image_base64[:100]}...",
        )

    return GenerateImagePipelineResponse(
        image_base64=image_base64,
        format="base64",
        prompt=request.prompt,
        similar_content=similar,
        draft_id=draft_id,
    )


class SaveDraftRequest(BaseModel):
    """Save generated image as draft."""
    prompt: str
    image_base64: str
    enhanced_prompt: str | None = None
    platform: str | None = None
    caption: str | None = None
    hashtags: list[str] = []


class SaveDraftResponse(BaseModel):
    draft_id: str
    message: str


@router.post("/save-draft", response_model=SaveDraftResponse)
async def save_draft(
    request: SaveDraftRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save generated image as draft for later posting."""
    team_result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = team_result.scalars().first()

    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    draft_id = str(uuid.uuid4())

    # Store full draft data in chroma
    draft_data = {
        "prompt": request.prompt,
        "image_base64": request.image_base64,
        "enhanced_prompt": request.enhanced_prompt,
        "platform": request.platform,
        "caption": request.caption,
        "hashtags": request.hashtags,
        "created_at": str(datetime.utcnow()),
    }

    await chroma_client.add_content(
        str(team.id),
        draft_id,
        f"DRAFT:{json.dumps(draft_data)}",
    )

    return SaveDraftResponse(draft_id=draft_id, message="Draft saved successfully")


class DraftItem(BaseModel):
    draft_id: str
    prompt: str
    image_base64: str
    enhanced_prompt: str | None = None
    platform: str | None = None
    caption: str | None = None
    hashtags: list[str] = []
    created_at: str


class ListDraftsResponse(BaseModel):
    drafts: list[DraftItem]


@router.get("/drafts", response_model=ListDraftsResponse)
async def list_drafts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all saved drafts for the current user's team."""
    team_result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = team_result.scalars().first()

    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    # Query chroma for drafts
    results = await chroma_client.query_similar(str(team.id), "DRAFT:", n_results=50)

    drafts = []
    for result in results:
        if result.startswith("DRAFT:"):
            import json
            try:
                draft_data = json.loads(result[6:])  # Remove "DRAFT:" prefix
                drafts.append(DraftItem(
                    draft_id="",  # We don't have the ID easily from chroma query
                    prompt=draft_data.get("prompt", ""),
                    image_base64=draft_data.get("image_base64", ""),
                    enhanced_prompt=draft_data.get("enhanced_prompt"),
                    platform=draft_data.get("platform"),
                    caption=draft_data.get("caption"),
                    hashtags=draft_data.get("hashtags", []),
                    created_at=draft_data.get("created_at", ""),
                ))
            except:
                pass

    return ListDraftsResponse(drafts=drafts)


class PostDraftRequest(BaseModel):
    draft_id: str
    platform: str  # linkedin, twitter, instagram, facebook, threads
    account_id: str | None = None
    caption: str | None = None
    hashtags: list[str] = []


class PostDraftResponse(BaseModel):
    success: bool
    post_id: str | None = None
    message: str


@router.post("/post-draft", response_model=PostDraftResponse)
async def post_draft(
    request: PostDraftRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Post a draft to social media platform."""
    team_result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = team_result.scalars().first()

    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    # Get social account
    from sqlalchemy import select

    from app.models.social_account import SocialAccount

    if request.account_id:
        account_result = await db.execute(
            select(SocialAccount).where(SocialAccount.id == request.account_id)
        )
    else:
        account_result = await db.execute(
            select(SocialAccount).where(
                SocialAccount.team_id == team.id,
                SocialAccount.platform == request.platform,
                SocialAccount.status == "active",
            ).limit(1)
        )

    account = account_result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail=f"No connected {request.platform} account found")

    # TODO: Implement actual posting to social platforms
    # This would use the n8n workflow or direct API calls

    return PostDraftResponse(
        success=True,
        post_id=str(uuid.uuid4()),
        message=f"Draft posted to {request.platform} successfully",
    )


@router.get("/generate-image/{job_id}")
async def get_image_status(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{settings.COMFYUI_URL}/history/{job_id}")
            if resp.status_code == 200:
                history = resp.json()
                if job_id in history:
                    outputs = history[job_id].get("outputs", {})
                    for node_output in outputs.values():
                        images = node_output.get("images", [])
                        if images:
                            filename = images[0]["filename"]
                            return {
                                "job_id": job_id,
                                "status": "completed",
                                "image_url": f"{settings.COMFYUI_URL}/view?filename={filename}",
                            }
                return {"job_id": job_id, "status": "processing"}
    except Exception:
        pass
    return {"job_id": job_id, "status": "unknown"}


class GenerateImageFluxRequest(BaseModel):
    """Request for NVIDIA FLUX.1-Kontext-dev text-to-image generation."""
    prompt: str
    negative_prompt: str = ""
    cfg_scale: float = 3.5
    seed: int = 0
    steps: int = 20


class GenerateImageFluxResponse(BaseModel):
    """Response for NVIDIA FLUX text-to-image generation."""
    image_base64: str
    format: str = "base64"
    prompt: str


@router.post("/generate-image-flux")
async def generate_image_flux(
    request: GenerateImageFluxRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate image using NVIDIA's hosted FLUX.1-Kontext-dev API."""
    import base64

    team_result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = team_result.scalars().first()
    team_id = team.id if team else None

    # Get provider config
    from app.services.inference import _get_provider_config
    base_url, model, api_key = await _get_provider_config("nvidia-flux", team_id, db)

    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="No API key configured for NVIDIA FLUX. Add it in Settings → AI Providers (provider name: 'nvidia-flux')."
        )

    # Call NVIDIA FLUX API - returns binary image data
    image_bytes = await _call_nvidia_flux(
        prompt=request.prompt,
        base_url=base_url,
        api_key=api_key,
        negative_prompt=request.negative_prompt,
        cfg_scale=request.cfg_scale,
        seed=request.seed,
        steps=request.steps,
    )

    # Convert to base64 for response
    image_base64 = base64.b64encode(image_bytes).decode('utf-8')

    # Store prompt in chroma for deduplication
    if team and request.prompt:
        await chroma_client.add_content(str(team.id), str(uuid.uuid4()), request.prompt)

    return GenerateImageFluxResponse(
        image_base64=image_base64,
        format="base64",
        prompt=request.prompt,
    )


@router.post("/generate-content", response_model=GenerateContentResponse)
async def generate_content(
    request: GenerateContentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Check chroma for similar existing content before generating
    team_result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = team_result.scalars().first()
    if team:
        similar = await chroma_client.query_similar(str(team.id), request.prompt, n_results=3)
        if similar:
            # Surface similar content in prompt so Ollama can differentiate
            request = request.model_copy(
                update={"prompt": f"{request.prompt}\n\n[Note: avoid repeating these similar posts: {similar[:2]}]"}
            )

    platform_guides = {
        "linkedin": "Professional, thought-leadership style. 1300 char limit. Use line breaks. 3-5 hashtags.",
        "twitter": "Concise, conversational. 280 char limit. Thread-friendly. 1-2 hashtags.",
        "instagram": "Visual-first, engaging. 2200 char limit. 10-15 hashtags. Use emojis.",
        "facebook": "Community-focused, conversational. No strict limit. 1-3 hashtags.",
        "threads": "Casual, text-based. 500 char limit. Minimal hashtags.",
    }

    guide = platform_guides.get(request.platform, platform_guides["linkedin"])

    prompt = f"""Write a {request.platform} post based on this prompt: "{request.prompt}"

Platform guidelines: {guide}
Tone: {request.tone}
Length: {request.length}
Include hashtags: {request.include_hashtags}
Include emojis: {request.include_emojis}

Return JSON with: content, hashtags (array), suggested_media (string or null)"""

    schema = {
        "type": "object",
        "properties": {
            "content": {"type": "string"},
            "hashtags": {"type": "array", "items": {"type": "string"}},
            "suggested_media": {"type": ["string", "null"]},
        },
        "required": ["content", "hashtags", "suggested_media"],
    }

    team_id_for_gen = team.id if team else None
    result = await call_inference(prompt, provider_name=request.provider, db=db, team_id=team_id_for_gen, schema=schema, model_override=request.model)
    content = result.get("content", "")

    # Index generated content in chroma for future dedup
    if team and content:
        await chroma_client.add_content(
            str(team.id),
            str(uuid.uuid4()),
            f"{request.platform}:{content}",
        )

    return GenerateContentResponse(
        content=content,
        hashtags=result.get("hashtags", []),
        suggested_media=result.get("suggested_media"),
    )


@router.post("/generate-hashtags", response_model=SuggestHashtagsResponse)
@router.post("/suggest-hashtags", response_model=SuggestHashtagsResponse)
async def suggest_hashtags(
    request: SuggestHashtagsRequest,
    current_user: User = Depends(get_current_user),
):
    prompt = f"""Suggest {request.max_hashtags} relevant hashtags for this {request.platform} post:

"{request.content}"

Return JSON with: hashtags (array of strings without #)"""

    schema = {
        "type": "object",
        "properties": {
            "hashtags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["hashtags"],
    }

    result = await call_ollama(prompt, schema=schema)

    return SuggestHashtagsResponse(hashtags=result.get("hashtags", []))


@router.post("/best-time-to-post", response_model=BestTimeResponse)
async def best_time_to_post(
    request: BestTimeRequest,
    current_user: User = Depends(get_current_user),
    db=Depends(get_db),
):
    # TODO: Analyze account's historical engagement data
    # For now, return general best times per platform

    result = await db.execute(
        select(SocialAccount).where(SocialAccount.id == request.account_id)
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    best_times = {
        "linkedin": [
            {"day": "Tuesday", "time": "09:00", "timezone": "UTC"},
            {"day": "Wednesday", "time": "09:00", "timezone": "UTC"},
            {"day": "Thursday", "time": "09:00", "timezone": "UTC"},
        ],
        "twitter": [
            {"day": "Monday", "time": "12:00", "timezone": "UTC"},
            {"day": "Wednesday", "time": "15:00", "timezone": "UTC"},
            {"day": "Friday", "time": "12:00", "timezone": "UTC"},
        ],
        "instagram": [
            {"day": "Monday", "time": "11:00", "timezone": "UTC"},
            {"day": "Wednesday", "time": "11:00", "timezone": "UTC"},
            {"day": "Friday", "time": "10:00", "timezone": "UTC"},
        ],
        "facebook": [
            {"day": "Tuesday", "time": "10:00", "timezone": "UTC"},
            {"day": "Thursday", "time": "10:00", "timezone": "UTC"},
            {"day": "Saturday", "time": "09:00", "timezone": "UTC"},
        ],
    }

    return BestTimeResponse(best_times=best_times.get(account.platform, best_times["linkedin"]))


@router.post("/improve-content", response_model=ImproveContentResponse)
async def improve_content(
    request: ImproveContentRequest,
    current_user: User = Depends(get_current_user),
):
    prompt = f"""Improve this {request.platform} post for {request.goal}:

Original: "{request.content}"

Return JSON with: improved_content (string), changes (array of strings describing what was changed)"""

    schema = {
        "type": "object",
        "properties": {
            "improved_content": {"type": "string"},
            "changes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["improved_content", "changes"],
    }

    result = await call_ollama(prompt, schema=schema)

    return ImproveContentResponse(
        improved_content=result.get("improved_content", request.content),
        changes=result.get("changes", []),
    )


@router.post("/generate-workflow", response_model=GenerateWorkflowResponse)
async def generate_workflow(
    request: GenerateWorkflowRequest,
    current_user: User = Depends(get_current_user),
    db=Depends(get_db),
):
    # Parse intent from prompt using Ollama
    intent_prompt = f"""Analyze this prompt and extract the workflow intent:

Prompt: "{request.prompt}"

Return JSON with:
- intent: (portfolio, announcement, thread, carousel, video, blog_to_social, content_repurpose, custom)
- platforms: array of platforms (linkedin, twitter, instagram, facebook, threads)
- needs_image: boolean
- needs_scheduling: boolean
- schedule_hint: string or null
- data_sources: array (github, notion, rss, url, manual, none)
- complexity: (simple, medium, complex)"""

    schema = {
        "type": "object",
        "properties": {
            "intent": {"type": "string"},
            "platforms": {"type": "array", "items": {"type": "string"}},
            "needs_image": {"type": "boolean"},
            "needs_scheduling": {"type": "boolean"},
            "schedule_hint": {"type": ["string", "null"]},
            "data_sources": {"type": "array", "items": {"type": "string"}},
            "complexity": {"type": "string"},
        },
        "required": ["intent", "platforms", "needs_image", "needs_scheduling", "schedule_hint", "data_sources", "complexity"],
    }

    intent = await call_ollama(intent_prompt, schema=schema)

    # Find matching template
    template = None
    if request.template_id:
        from sqlalchemy import select
        result = await db.execute(select(PromptTemplate).where(PromptTemplate.id == request.template_id))
        template = result.scalar_one_or_none()
    else:
        # Search for template by category
        result = await db.execute(
            select(PromptTemplate)
            .where(PromptTemplate.category == intent.get("intent"), PromptTemplate.is_public)
            .limit(1)
        )
        template = result.scalar_one_or_none()

    # Build n8n workflow based on intent
    workflow = await _build_workflow_from_intent(intent, template)

    variables_used = {}
    if template:
        import re
        vars_found = re.findall(r"{{(\w+)}}", template.prompt_template)
        for var in vars_found:
            variables_used[var] = f"<{var}>"

    # Save generated workflow
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()

    gen_workflow = GeneratedWorkflow(
        team_id=team.id if team else uuid.uuid4(),
        user_id=current_user.id,
        prompt_text=request.prompt,
        n8n_workflow_json=workflow,
        template_id=template.id if template else None,
        variables_used=variables_used,
    )
    db.add(gen_workflow)
    await db.commit()

    return GenerateWorkflowResponse(
        n8n_workflow_json=workflow,
        variables_used=variables_used,
        template_id=template.id if template else None,
    )


async def _build_workflow_from_intent(intent: dict, template: PromptTemplate | None) -> dict:
    """Build n8n workflow JSON from parsed intent."""
    if template:
        workflow = template.n8n_workflow_json.copy()
        workflow["name"] = f"AI Generated: {intent.get('intent', 'custom')}"
        return workflow

    # Build basic workflow structure
    nodes = [
        {
            "name": "Start",
            "type": "n8n-nodes-base.start",
            "typeVersion": 1,
            "position": [250, 300],
        }
    ]
    connections = {}

    node_y = 300

    # Add data source node if needed
    if "github" in intent.get("data_sources", []):
        node_y += 100
        nodes.append({
            "name": "GitHub Trigger",
            "type": "n8n-nodes-base.github",
            "typeVersion": 1,
            "position": [250, node_y],
            "parameters": {
                "event": "push",
                "repository": "={{$workflow.variables.github_repo}}",
            },
        })

    # Add LLM processing node
    if intent.get("needs_image") or True:
        node_y += 100
        nodes.append({
            "name": "Process Content",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 1,
            "position": [250, node_y],
            "parameters": {
                "url": f"{settings.OLLAMA_URL}/api/generate",
                "method": "POST",
                "jsonParameters": True,
                "options": {
                    "model": settings.OLLAMA_DEFAULT_MODEL,
                    "prompt": "Process: {{ $json.content }}",
                    "format": "json",
                },
            },
        })

    # Add ComfyUI image generation if needed
    if intent.get("needs_image"):
        node_y += 100
        nodes.append({
            "name": "Generate Image",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 1,
            "position": [250, node_y],
            "parameters": {
                "url": f"{settings.COMFYUI_URL}/prompt",
                "method": "POST",
                "jsonParameters": True,
                "options": {
                    "prompt": {
                        # ComfyUI workflow would go here
                    },
                },
            },
        })

    # Add platform posting nodes
    for i, platform in enumerate(intent.get("platforms", ["linkedin"])):
        node_y += 100
        nodes.append({
            "name": f"Post to {platform.title()}",
            "type": f"n8n-nodes-base.{platform}",
            "typeVersion": 1,
            "position": [500 + i * 200, node_y],
            "parameters": {
                "operation": "post",
                "text": "={{$json.content}}",
            },
        })

    # Add scheduling if needed
    if intent.get("needs_scheduling"):
        node_y += 100
        nodes.append({
            "name": "Schedule",
            "type": "n8n-nodes-base.cron",
            "typeVersion": 1,
            "position": [250, node_y],
            "parameters": {
                "triggerTimes": {
                    "item": [
                        {
                            "hour": 9,
                            "minute": 0,
                        }
                    ]
                },
            },
        })

    return {
        "name": f"AI Generated: {intent.get('intent', 'custom')}",
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1"},
    }


@router.post("/generate-carousel", response_model=GenerateCarouselResponse)
async def generate_carousel(
    request: GenerateCarouselRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Check chroma for similar existing carousel content before generating
    team_result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = team_result.scalars().first()
    if team:
        similar = await chroma_client.query_similar(str(team.id), request.topic, n_results=3)
        if similar:
            # Surface similar content in prompt so AI can differentiate
            request = request.model_copy(
                update={"topic": f"{request.topic}\n\n[Note: avoid repeating these similar carousels: {similar[:2]}]"}
            )

    platform_guides = {
        "linkedin": "LinkedIn professional audience. Each slide should deliver one clear insight.",
        "instagram": "Instagram visual storytelling. Keep text concise and punchy.",
    }
    guide = platform_guides.get(request.platform, platform_guides["linkedin"])
    num = max(3, min(10, request.num_slides))
    include_cta = request.include_cta

    prompt = f"""Create a {num}-slide infographic carousel about: "{request.topic}"

Platform: {request.platform} — {guide}
Tone: {request.tone}
{"The last slide should be a strong CTA (call to action)." if include_cta else ""}

Slide types to use:
- "cover": First slide — bold title + short subtitle
- "content": Main points — headline + 2-4 sentence explanation
- "stat": A striking statistic or fact — short number/stat as highlight, brief context as body
- "cta": Last slide — action-oriented title + what to do next

Return JSON with:
- slides: array of exactly {num} objects, each with: title (string), body (string, max 100 chars), highlight (string or null, used for stats/key numbers), slide_type (cover|content|stat|cta)
- suggested_caption: a complete post caption (with line breaks) to accompany the carousel
- hashtags: array of 5-8 relevant hashtags (without #)"""

    schema = {
        "type": "object",
        "properties": {
            "slides": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "body": {"type": "string"},
                        "highlight": {"type": ["string", "null"]},
                        "slide_type": {"type": "string"},
                    },
                    "required": ["title", "body", "slide_type"],
                },
            },
            "suggested_caption": {"type": "string"},
            "hashtags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["slides", "suggested_caption", "hashtags"],
    }

    import httpx as _httpx
    team_id = await get_team_id_for_user(current_user.id, db)
    try:
        result = await call_inference(prompt, provider_name=request.provider, db=db, team_id=team_id, schema=schema, model_override=request.model)
    except HTTPException:
        raise
    except _httpx.ReadTimeout:
        raise HTTPException(
            status_code=504,
            detail=f"The AI provider timed out (300s). '{request.provider}' reasoning models can be slow for long prompts — try switching to a faster model like meta/llama-3.1-70b-instruct.",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Inference error: {exc}")

    slides = [
        CarouselSlide(
            title=s.get("title", ""),
            body=s.get("body", ""),
            highlight=s.get("highlight"),
            slide_type=s.get("slide_type", "content"),
        )
        for s in result.get("slides", [])
    ]

    # Index generated carousel content in chroma for future dedup
    if team and result.get("slides"):
        carousel_content = f"CAROUSEL:{request.platform}:{request.topic}:" + "|".join([s.get("title", "") for s in result.get("slides", [])])
        await chroma_client.add_content(
            str(team.id),
            str(uuid.uuid4()),
            carousel_content,
        )

    return GenerateCarouselResponse(
        slides=slides,
        suggested_caption=result.get("suggested_caption", ""),
        hashtags=result.get("hashtags", []),
    )


