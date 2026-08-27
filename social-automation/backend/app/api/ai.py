import json
import os
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
from app.models.content import MediaAsset
from app.models.social_account import SocialAccount
from app.models.user import Team, TeamMember, User
from app.models.workflow import GeneratedWorkflow, PromptTemplate
from app.services import chroma_client
from app.services.inference import (
    STT_MODELS,
    _call_cf_image_pipeline,
    _call_nvidia_flux,
    _call_nvidia_flux_dev,
    _call_nvidia_flux_pipeline,
    call_inference,
    get_team_id_for_user,
    retrieve_workers_ai_batch,
    submit_workers_ai_batch,
    transcribe_workers_ai,
)
from app.services.media_storage import persist_generated_image
from app.services.cf_models import CF_IMG2IMG_FREE, CF_TEXT_FREE, CF_TXT2IMG_FREE

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
    provider: str = "cloudflare"
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
    provider: str = "cloudflare"
    model: str | None = None


class GenerateContentResponse(BaseModel):
    content: str
    hashtags: list[str]
    suggested_media: str | None = None


class SuggestHashtagsRequest(BaseModel):
    content: str
    platform: str
    max_hashtags: int = 10
    # Frontend historically sent `count`; accept either.
    count: int | None = None

    def resolved_max(self) -> int:
        return self.count if self.count is not None else self.max_hashtags


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
    # Frontend historically sent `instruction`; accept either.
    instruction: str | None = None

    def resolved_goal(self) -> str:
        return self.instruction or self.goal


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
# ---------------------------------------------------------------------------
# Workers AI Batch Inference
# ---------------------------------------------------------------------------


class BatchInferenceItem(BaseModel):
    """A single request inside a batch — model-specific payload fields plus an
    optional ``external_reference`` echoed back in the batch response."""

    external_reference: str | None = None

    model_config = {"extra": "allow"}  # allow arbitrary model-specific inputs


class BatchInferenceSubmitRequest(BaseModel):
    model: str  # Workers AI model id, e.g. @cf/baai/bge-m3
    requests: list[BatchInferenceItem]


class BatchInferenceRetrieveRequest(BaseModel):
    model: str
    request_id: str


class BatchInferenceSubmitResponse(BaseModel):
    request_id: str | None
    status: str
    model: str


@router.post("/workers-ai/batch", response_model=BatchInferenceSubmitResponse)
async def submit_batch_inference(
    payload: BatchInferenceSubmitRequest,
    current_user: User = Depends(get_current_user),
):
    """Queue a batch of inference requests against a Cloudflare Workers AI model.

    Returns a ``request_id`` — poll ``POST /ai/workers-ai/batch/retrieve`` with it
    to fetch the results once processing completes.
    """
    items = [
        {k: v for k, v in item.model_dump(exclude_none=True).items()}
        for item in payload.requests
    ]
    try:
        result = await submit_workers_ai_batch(payload.model, items)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Batch submission error: {exc}")
    return BatchInferenceSubmitResponse(**result)


@router.post("/workers-ai/batch/retrieve")
async def retrieve_batch_inference(
    payload: BatchInferenceRetrieveRequest,
    current_user: User = Depends(get_current_user),
):
    """Retrieve (or poll) the results of a previously submitted batch request."""
    try:
        return await retrieve_workers_ai_batch(payload.model, payload.request_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Batch retrieval error: {exc}")


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
    steps: int = 4
    provider: str = "cloudflare"  # cloudflare | nvidia-flux-dev
    model: str | None = None  # e.g. "@cf/black-forest-labs/flux-1-schnell"


class GenerateImageResponse(BaseModel):
    image_base64: str
    format: str = "base64"
    prompt: str
    similar_content: list[str] = []
    asset_id: uuid.UUID | None = None
    storage_path: str | None = None


@router.post("/generate-image", response_model=GenerateImageResponse)
async def generate_image(
    request: GenerateImageRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate an image via a text-to-image provider (NVIDIA FLUX.1-dev or Cloudflare Workers AI)."""
    import base64

    from app.services.inference import (
        _call_workers_ai_image,
        _get_provider_config,
        _is_workers_ai_image_model,
    )

    team_result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = team_result.scalars().first()
    team_id = team.id if team else None

    # Check chroma for similar generated images before submitting
    similar: list[str] = []
    if team:
        similar = await chroma_client.query_similar(str(team.id), request.prompt, n_results=3)

    provider_name = request.provider or "cloudflare"

    if provider_name == "cloudflare":
        # Cloudflare Workers AI text-to-image (SDXL / FLUX models).
        from app.services.cf_models import CF_TXT2IMG_FREE

        _, model, api_key = await _get_provider_config("cloudflare", team_id, db)
        model = request.model or model or CF_TXT2IMG_FREE
        if not _is_workers_ai_image_model(model):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{model}' is not a Workers AI text-to-image model. Use a model like "
                    f"'@cf/stabilityai/stable-diffusion-xl-base-1.0' or '@cf/black-forest-labs/flux-1-schnell'."
                ),
            )
        result = await _call_workers_ai_image(
            prompt=request.prompt,
            model=model,
            api_key=api_key,
            negative_prompt=request.negative_prompt,
            steps=request.steps,
            cfg_scale=request.cfg_scale,
        )
        image_base64 = result["image_base64"]
    else:
        # NVIDIA FLUX.1-dev (cloud) — default provider.
        base_url, model, api_key = await _get_provider_config(provider_name, team_id, db)
        if not api_key:
            raise HTTPException(
                status_code=400,
                detail=f"No API key configured for {provider_name}. Add NVIDIA_API_KEY in .env or configure in Settings → AI Providers."
            )
        image_bytes = await _call_nvidia_flux_dev(
            prompt=request.prompt,
            base_url=base_url,
            api_key=api_key,
            negative_prompt=request.negative_prompt,
            cfg_scale=request.cfg_scale,
            seed=request.seed,
            steps=request.steps,
        )
        image_base64 = base64.b64encode(image_bytes).decode('utf-8')

    # Store prompt in chroma so future generations can detect duplicates
    if team and request.prompt:
        await chroma_client.add_content(str(team.id), str(uuid.uuid4()), request.prompt)

    # Persist to disk + media_assets table so it appears in the Media Library
    asset = None
    if team:
        asset = await persist_generated_image(
            db,
            team_id=team.id,
            user_id=current_user.id,
            image_bytes=base64.b64decode(image_base64),
            prompt=request.prompt,
            source="ai-generated",
        )

    return GenerateImageResponse(
        image_base64=image_base64,
        format="base64",
        prompt=request.prompt,
        similar_content=similar,
        asset_id=asset.id if asset else None,
        storage_path=asset.storage_path if asset else None,
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
    asset_id: uuid.UUID | None = None
    storage_path: str | None = None


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

    # Persist to disk + media_assets table so it appears in the Media Library
    asset = None
    if team:
        asset = await persist_generated_image(
            db,
            team_id=team.id,
            user_id=current_user.id,
            image_bytes=image_bytes,
            prompt=request.prompt,
            source="ai-generated",
        )

    return GenerateImagePipelineResponse(
        image_base64=image_base64,
        format="base64",
        prompt=request.prompt,
        similar_content=similar,
        draft_id=draft_id,
        asset_id=asset.id if asset else None,
        storage_path=asset.storage_path if asset else None,
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
            except Exception:
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
    db: AsyncSession = Depends(get_db),
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
                            subfolder = images[0].get("subfolder", "")
                            folder_type = images[0].get("type", "output")

                            # Persist the generated image into media_assets so it
                            # shows up in the Media Library (once per job).
                            asset_id = None
                            storage_path = None
                            team_result = await db.execute(
                                select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
                            )
                            team = team_result.scalars().first()
                            if team:
                                existing = await db.execute(
                                    select(MediaAsset).where(
                                        MediaAsset.generation_prompt == f"comfyui:{job_id}"
                                    )
                                )
                                if existing.scalars().first() is None:
                                    img_resp = await client.get(
                                        f"{settings.COMFYUI_URL}/view",
                                        params={
                                            "filename": filename,
                                            "subfolder": subfolder,
                                            "type": folder_type,
                                        },
                                    )
                                    if img_resp.status_code == 200:
                                        ext = os.path.splitext(filename)[1] or ".png"
                                        asset = await persist_generated_image(
                                            db,
                                            team_id=team.id,
                                            user_id=current_user.id,
                                            image_bytes=img_resp.content,
                                            prompt=f"comfyui:{job_id}",
                                            source="comfyui",
                                            extension=ext,
                                        )
                                        # Keep the human-readable ComfyUI filename
                                        asset.filename = filename
                                        await db.commit()
                                        await db.refresh(asset)
                                        asset_id = asset.id
                                        storage_path = asset.storage_path

                            return {
                                "job_id": job_id,
                                "status": "completed",
                                "image_url": f"{settings.COMFYUI_URL}/view?filename={filename}",
                                "asset_id": str(asset_id) if asset_id else None,
                                "storage_path": storage_path,
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
    asset_id: uuid.UUID | None = None
    storage_path: str | None = None


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

    # Persist to disk + media_assets table so it appears in the Media Library
    asset = None
    if team:
        asset = await persist_generated_image(
            db,
            team_id=team.id,
            user_id=current_user.id,
            image_bytes=image_bytes,
            prompt=request.prompt,
            source="ai-generated",
        )

    return GenerateImageFluxResponse(
        image_base64=image_base64,
        format="base64",
        prompt=request.prompt,
        asset_id=asset.id if asset else None,
        storage_path=asset.storage_path if asset else None,
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
        "linkedin": "Professional, thought-leadership style. 1300 char limit. Use line breaks. 3-5 hashtags. Plain everyday English.",
        "twitter": "Concise, conversational. 280 char limit. Thread-friendly. 1-2 hashtags. Plain everyday English.",
        "instagram": "Visual-first, engaging. 2200 char limit. 10-15 hashtags. Use emojis. Plain everyday English.",
        "facebook": "Community-focused, conversational. No strict limit. 1-3 hashtags. Plain everyday English.",
        "threads": "Casual, text-based. 500 char limit. Minimal hashtags. Plain everyday English.",
    }

    guide = platform_guides.get(request.platform, platform_guides["linkedin"])

    from app.services.plain_english import PLAIN_ENGLISH_RULES, rewrite_plain_english

    prompt = f"""Write a {request.platform} post based on this prompt: "{request.prompt}"

Platform guidelines: {guide}
Tone: {request.tone}
Length: {request.length}
Include hashtags: {request.include_hashtags}
Include emojis: {request.include_emojis}

{PLAIN_ENGLISH_RULES}

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
    content = await rewrite_plain_english(
        content,
        provider_name=request.provider or "cloudflare",
        model=request.model or (CF_TEXT_FREE if (request.provider or "cloudflare") == "cloudflare" else None),
        db=db,
        team_id=team_id_for_gen,
        context=f"{request.platform} post",
    )

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
    prompt = f"""Suggest {request.resolved_max()} relevant hashtags for this {request.platform} post:

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
            {"day": "Tuesday", "time": "09:00", "timezone": "Europe/Athens"},
            {"day": "Wednesday", "time": "09:00", "timezone": "Europe/Athens"},
            {"day": "Thursday", "time": "09:00", "timezone": "Europe/Athens"},
        ],
        "twitter": [
            {"day": "Monday", "time": "12:00", "timezone": "Europe/Athens"},
            {"day": "Wednesday", "time": "15:00", "timezone": "Europe/Athens"},
            {"day": "Friday", "time": "12:00", "timezone": "Europe/Athens"},
        ],
        "instagram": [
            {"day": "Monday", "time": "11:00", "timezone": "Europe/Athens"},
            {"day": "Wednesday", "time": "11:00", "timezone": "Europe/Athens"},
            {"day": "Friday", "time": "10:00", "timezone": "Europe/Athens"},
        ],
        "facebook": [
            {"day": "Tuesday", "time": "10:00", "timezone": "Europe/Athens"},
            {"day": "Thursday", "time": "10:00", "timezone": "Europe/Athens"},
            {"day": "Saturday", "time": "09:00", "timezone": "Europe/Athens"},
        ],
    }

    return BestTimeResponse(best_times=best_times.get(account.platform, best_times["linkedin"]))


@router.post("/improve-content", response_model=ImproveContentResponse)
async def improve_content(
    request: ImproveContentRequest,
    current_user: User = Depends(get_current_user),
):
    prompt = f"""Improve this {request.platform} post for {request.resolved_goal()}:

Original: "{request.content}"

Also rewrite into plain everyday English so non-experts understand it. Avoid jargon and buzzwords.
Keep the meaning. Prefer short sentences and common words.

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
        "linkedin": "LinkedIn audience. Each slide delivers one clear idea in plain English.",
        "instagram": "Instagram visual storytelling. Short, punchy, easy words.",
    }
    guide = platform_guides.get(request.platform, platform_guides["linkedin"])
    num = max(3, min(10, request.num_slides))
    include_cta = request.include_cta

    from app.services.plain_english import PLAIN_ENGLISH_RULES, run_nlp_check_and_fix

    prompt = f"""Create a {num}-slide infographic carousel about: "{request.topic}"

Platform: {request.platform} — {guide}
Tone: {request.tone} (still use plain everyday English)
{"The last slide should be a strong CTA (call to action)." if include_cta else ""}

{PLAIN_ENGLISH_RULES}

Slide types to use:
- "cover": First slide — bold title + short subtitle
- "content": Main points — headline + 1-2 short sentences
- "stat": A striking statistic or fact — short number/stat as highlight, brief context as body
- "cta": Last slide — clear next step in simple words

Return JSON with:
- slides: array of exactly {num} objects, each with: title (string, plain English), body (string, max 100 chars, plain English),
  highlight (string or null, used for stats/key numbers), slide_type (cover|content|stat|cta)
- suggested_caption: a complete post caption (with line breaks) in plain English
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
            detail=(
                f"The AI provider timed out (300s). '{request.provider}' reasoning models can be "
                "slow for long prompts — try switching to a faster model like meta/llama-3.1-70b-instruct."
            ),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Inference error: {exc}")

    # NLP checker + fixer: flag jargon / hard sentences, then rewrite to plain English
    cleaned_slides, cleaned_caption, _nlp_report = await run_nlp_check_and_fix(
        slides=list(result.get("slides") or []),
        caption=result.get("suggested_caption", ""),
        provider_name=request.provider or "cloudflare",
        model=request.model or (CF_TEXT_FREE if (request.provider or "cloudflare") == "cloudflare" else None),
        db=db,
        team_id=team_id,
        force_fix=True,
    )

    slides = [
        CarouselSlide(
            title=s.get("title", ""),
            body=s.get("body", ""),
            highlight=s.get("highlight"),
            slide_type=s.get("slide_type", "content"),
        )
        for s in cleaned_slides
    ]

    # Index generated carousel content in chroma for future dedup
    if team and cleaned_slides:
        carousel_content = f"CAROUSEL:{request.platform}:{request.topic}:" + "|".join([s.get("title", "") for s in cleaned_slides])
        await chroma_client.add_content(
            str(team.id),
            str(uuid.uuid4()),
            carousel_content,
        )

    return GenerateCarouselResponse(
        slides=slides,
        suggested_caption=cleaned_caption,
        hashtags=result.get("hashtags", []),
    )


class CarouselPipelineSlideResult(BaseModel):
    slide_type: str
    title: str
    body: str
    highlight: str | None = None
    image_prompt: str
    enhance_prompt: str
    media_id: uuid.UUID | None = None
    storage_path: str | None = None


class GenerateCarouselPipelineRequest(BaseModel):
    topic: str
    num_slides: int = 7
    platform: str = "linkedin"
    tone: str = "professional"
    include_cta: bool = True
    text_model: str = CF_TEXT_FREE
    txt2img_model: str = CF_TXT2IMG_FREE
    img2img_model: str = CF_IMG2IMG_FREE
    strength: float = 0.45


class GenerateCarouselPipelineResponse(BaseModel):
    slides: list[CarouselPipelineSlideResult]
    suggested_caption: str
    hashtags: list[str]
    media_ids: list[uuid.UUID]
    models: dict
    nlp_report: dict = {}


@router.post("/generate-carousel-pipeline", response_model=GenerateCarouselPipelineResponse)
async def generate_carousel_pipeline(
    request: GenerateCarouselPipelineRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """CF-only carousel pipeline:

    1. LLM slide copy
    2. NLP checker + plain-English fixer
    3. FLUX schnell txt2img → SD img2img enhance (draft-only if enhance fails)
    4. Persist to media library
    """
    import base64

    from app.services.plain_english import run_nlp_check_and_fix

    team_result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = team_result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    # 1) Slide copy via Cloudflare LLM (also runs NLP inside generate_carousel)
    copy = await generate_carousel(
        GenerateCarouselRequest(
            topic=request.topic,
            num_slides=request.num_slides,
            platform=request.platform,
            tone=request.tone,
            include_cta=request.include_cta,
            provider="cloudflare",
            model=request.text_model,
        ),
        current_user=current_user,
        db=db,
    )

    # 2) Explicit NLP checker + fixer stage (always)
    slide_dicts = [
        {
            "slide_type": s.slide_type,
            "title": s.title,
            "body": s.body,
            "highlight": s.highlight,
        }
        for s in copy.slides
    ]
    cleaned_slides, cleaned_caption, nlp_report = await run_nlp_check_and_fix(
        slides=slide_dicts,
        caption=copy.suggested_caption,
        provider_name="cloudflare",
        model=request.text_model,
        db=db,
        team_id=team.id,
        force_fix=True,
    )
    print(f"[carousel-pipeline] nlp {nlp_report.to_dict()}", flush=True)

    media_ids: list[uuid.UUID] = []
    slide_results: list[CarouselPipelineSlideResult] = []

    for i, slide in enumerate(cleaned_slides):
        title = slide.get("title") or ""
        body = slide.get("body") or ""
        visual = (
            f"LinkedIn carousel background for slide about '{title}'. "
            f"{body}. Dark navy abstract tech aesthetic, cyan and soft orange accents, "
            f"clean modern composition, square 1:1, no readable text, no logos, no watermark."
        )
        enhance = (
            f"Enhance quality and content of this LinkedIn carousel background about '{title}'. "
            f"Sharper details, richer cyan/orange lighting on dark navy, professional polish, "
            f"stronger visual metaphor for: {body}. No text, no logos."
        )
        print(f"[carousel-pipeline] slide {i + 1}/{len(cleaned_slides)} txt2img→img2img", flush=True)
        pipe = await _call_cf_image_pipeline(
            prompt=visual,
            enhance_prompt=enhance,
            txt2img_model=request.txt2img_model,
            img2img_model=request.img2img_model,
            strength=request.strength,
        )
        asset = await persist_generated_image(
            db,
            team_id=team.id,
            user_id=current_user.id,
            image_bytes=base64.b64decode(pipe["image_base64"]),
            prompt=f"carousel-pipeline:{title}",
            source="cf-carousel-pipeline",
        )
        media_ids.append(asset.id)
        slide_results.append(
            CarouselPipelineSlideResult(
                slide_type=slide.get("slide_type") or "content",
                title=title,
                body=body,
                highlight=slide.get("highlight"),
                image_prompt=visual,
                enhance_prompt=enhance,
                media_id=asset.id,
                storage_path=asset.storage_path,
            )
        )

    return GenerateCarouselPipelineResponse(
        slides=slide_results,
        suggested_caption=cleaned_caption,
        hashtags=copy.hashtags,
        media_ids=media_ids,
        models={
            "text": request.text_model,
            "txt2img": request.txt2img_model,
            "img2img": request.img2img_model,
            "nlp": "plain-english-check-fix",
        },
        nlp_report=nlp_report.to_dict(),
    )


class RunCarouselAndPublishRequest(BaseModel):
    topic: str = (
        "How cloudless.gr helps teams ship serverless apps without managing servers"
    )
    num_slides: int = 7
    tone: str = "clear and friendly"
    include_cta: bool = True
    text_model: str = CF_TEXT_FREE
    txt2img_model: str = CF_TXT2IMG_FREE
    img2img_model: str = CF_IMG2IMG_FREE
    strength: float = 0.42
    target_account_id: str | None = None
    publish: bool = True
    wait_for_publish: bool = False


@router.post("/run-carousel-and-publish")
async def run_carousel_and_publish(
    request: RunCarouselAndPublishRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """n8n-friendly full pipeline: copy → NLP → CF images → brand → post → LinkedIn org.

    Intended for the Cloudless n8n schedule/webhook workflow.
    """
    from app.services.carousel_pipeline import run_cloudless_carousel_pipeline

    team_result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = team_result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    return await run_cloudless_carousel_pipeline(
        db=db,
        user=current_user,
        team=team,
        topic=request.topic,
        num_slides=request.num_slides,
        tone=request.tone,
        include_cta=request.include_cta,
        text_model=request.text_model,
        txt2img_model=request.txt2img_model,
        img2img_model=request.img2img_model,
        strength=request.strength,
        target_account_id=request.target_account_id,
        publish=request.publish,
        wait_for_publish=request.wait_for_publish,
    )


