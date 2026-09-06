import base64
import json
import logging
import os
import re
import uuid
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth import get_current_user
from app.core.config import get_settings
from app.core.limiter import limiter
from app.db.session import get_db
from app.models.content import MediaAsset, Post, PostStatus, PostTarget
from app.models.social_account import SocialAccount
from app.models.user import Team, TeamMember, User
from app.models.workflow import GeneratedWorkflow, PromptTemplate
from app.services import chroma_client, seo
from app.services.cf_models import CF_TEXT_FREE, CF_TXT2IMG_FREE, CONTENT_WORKFLOW_CONFIGS
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
from app.services.plain_english import check_plain_english
from app.worker.celery_app import celery_app
from app.worker.tasks.publishing import process_publish_queue, publish_post_now

logger = logging.getLogger(__name__)
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
    seo_score: dict | None = None
    nlp_report: dict | None = None


class GenerateContentRequest(BaseModel):
    prompt: str
    platform: str
    tone: str = "professional"
    length: str = "medium"
    include_hashtags: bool = True
    include_emojis: bool = True
    provider: str = "dmr"  # DMR local primary; falls back to cloudflare
    model: str | None = None
    template_id: uuid.UUID | None = None


class GenerateContentResponse(BaseModel):
    content: str
    hashtags: list[str]
    suggested_media: str | None = None
    brand_compliance: dict | None = None
    seo_score: dict | None = None
    nlp_report: dict | None = None
    quality: dict | None = None


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


class HashtagTier(BaseModel):
    """A single hashtag with its tier classification."""
    tag: str
    tier: str  # safe, rising, niche
    estimated_reach: str  # broad, mid, low


class SuggestHashtagsTieredResponse(BaseModel):
    """Three-tier hashtag strategy response (safe/rising/niche)."""
    hashtags: list[str]  # flat list for backwards compatibility
    tiers: dict[str, list[HashtagTier]]  # {"safe": [...], "rising": [...], "niche": [...]}
    strategy: str  # description of the strategy used


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
    seo_score: dict | None = None
    nlp_report: dict | None = None
    quality: dict | None = None


class GenerateWorkflowRequest(BaseModel):
    prompt: str
    template_id: uuid.UUID | None = None


class GenerateWorkflowResponse(BaseModel):
    n8n_workflow_json: dict
    variables_used: dict
    template_id: uuid.UUID | None


async def call_ollama(prompt: str, model: str | None = None, schema: dict | None = None) -> dict:
    """Backwards-compatible shim — delegates to DMR (local Docker Model Runner)."""
    return await call_inference(prompt, provider_name="dmr", schema=schema, model_override=model)


class TranscribeResponse(BaseModel):
    text: str
    language: str | None = None
    duration: float | None = None
    detections: dict | None = None  # any extra fields the model returned


# Workers AI rejects request bodies over ~25 MB; keep a conservative cap so the
# frontend recorder (WAV, 16 kHz mono) can never exceed it.
MAX_AUDIO_SIZE_BYTES = 10 * 1024 * 1024


@router.post("/transcribe", response_model=TranscribeResponse)
@limiter.limit("10/minute")
async def transcribe_audio(
    request: Request,
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
    plain_english_issues: list[dict] = []
    average_sentence_words: float = 0.0
    seo_score: dict | None = None
    spellcheck_issues: list[dict] | None = None


class SeoRequest(BaseModel):
    content: str
    platform: str = "linkedin"
    title: str | None = None


class SeoResponse(BaseModel):
    platform: str
    score: dict
    keywords: list[dict]
    meta: dict
    open_graph: dict
    character_count: int
    hashtag_count: int
    link_count: int


@router.post("/generate-image-prompt", response_model=GenerateImagePromptResponse)
async def generate_image_prompt(
    request: GenerateImagePromptRequest,
    current_user: User = Depends(get_current_user),
):
    """Enhance a rough description into an optimised FLUX / SD image prompt."""
    prompt = f"""You are an expert at writing image generation prompts for FLUX and Stable Diffusion models.

Convert this rough idea into a highly detailed, optimised image prompt:

Idea: "{request.description}"
Requested style: {request.style}

Rules:
- Write a rich, descriptive positive prompt (lighting, composition, mood, colours, camera details)
- Tailor the keywords to the requested style
- Keep prompts concise but dense with useful descriptors (under 120 words)
- Write a negative_prompt listing things to exclude

Return JSON with exactly:
- prompt: string (the optimised positive prompt)
- negative_prompt: string (what to exclude)"""

    schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "negative_prompt": {"type": "string"},
        },
        "required": ["prompt", "negative_prompt"],
    }

    result = await call_inference(prompt, provider_name="cloudflare", schema=schema)
    return GenerateImagePromptResponse(
        prompt=result.get("prompt", request.description),
        negative_prompt=result.get("negative_prompt", "blurry, low quality, watermark, text overlay, logo, nsfw"),
    )


class AutoConfigureRequest(BaseModel):
    prompt: str
    context: str = "auto"  # "image" | "carousel" | "auto"


class AutoConfigureResponse(BaseModel):
    task_type: str  # "image" | "carousel" | "text"
    model: str
    steps: int
    style: str
    platform: str
    tone: str
    num_slides: int
    enhanced_prompt: str | None = None
    negative_prompt: str | None = None


@router.post("/auto-configure", response_model=AutoConfigureResponse)
async def auto_configure(
    request: AutoConfigureRequest,
    current_user: User = Depends(get_current_user),
):
    """Analyse a free-text prompt and return the best task type, model, and settings."""
    ctx_hint = {
        "image": " The user is on the image-generation page — default to task_type=image.",
        "carousel": " The user is on the carousel creation page — default to task_type=carousel.",
        "auto": "",
    }.get(request.context, "")

    llm_prompt = (
        f"You are an AI workflow router. Analyse the user's prompt and decide the best task type, "
        f"Cloudflare Workers AI model, and generation settings.{ctx_hint}\n\n"
        f'User prompt: "{request.prompt}"\n\n'
        "Rules:\n"
        '- task_type must be one of: "image", "carousel", "text"\n'
        '- For image tasks use model "@cf/black-forest-labs/flux-1-schnell"\n'
        '- For text/carousel tasks use model "@cf/meta/llama-3.3-70b-instruct-fp8-fast"\n'
        "- steps: 4 (fast, abstract/simple), 6 (balanced, most cases), 8 (best, photorealistic/detailed)\n"
        "- style: one of photorealistic, professional, cinematic, illustration, abstract, dark\n"
        "- platform: one of linkedin, instagram, twitter — infer from context clues "
        "(professional→linkedin, visual/lifestyle→instagram, short→twitter); default to linkedin\n"
        "- tone: one of professional, casual, inspirational, educational, humorous — "
        "infer from prompt; default professional\n"
        "- num_slides: 3-8 for carousel, infer from topic complexity; default 5\n"
        "- enhanced_prompt: if task_type is image, write an optimised FLUX prompt (under 100 words); else null\n"
        "- negative_prompt: if task_type is image, list exclusions; else null\n\n"
        "Return JSON with: task_type, model, steps, style, platform, tone, num_slides, "
        "enhanced_prompt, negative_prompt"
    )

    schema = {
        "type": "object",
        "properties": {
            "task_type": {"type": "string"},
            "model": {"type": "string"},
            "steps": {"type": "integer"},
            "style": {"type": "string"},
            "platform": {"type": "string"},
            "tone": {"type": "string"},
            "num_slides": {"type": "integer"},
            "enhanced_prompt": {"type": "string"},
            "negative_prompt": {"type": "string"},
        },
        "required": ["task_type", "model", "steps", "style", "platform", "tone", "num_slides"],
    }

    def _str_or_join(v: object) -> str | None:
        if v is None:
            return None
        if isinstance(v, list):
            return ", ".join(str(x) for x in v)
        return str(v)

    result = await call_inference(llm_prompt, provider_name="cloudflare", schema=schema)
    return AutoConfigureResponse(
        task_type=str(result.get("task_type", "image")),
        model=str(result.get("model", CF_TXT2IMG_FREE)),
        steps=int(result.get("steps", 6)),
        style=str(result.get("style", "photorealistic")),
        platform=str(result.get("platform", "linkedin")),
        tone=str(result.get("tone", "professional")),
        num_slides=int(result.get("num_slides", 5)),
        enhanced_prompt=_str_or_join(result.get("enhanced_prompt")),
        negative_prompt=_str_or_join(result.get("negative_prompt")),
    )


@router.post("/analyze-content", response_model=AnalyzeContentResponse)
async def analyze_content(
    request: AnalyzeContentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    hashtag_count = request.content.count("#")
    char_count = len(request.content)

    platform_limits = {
        "twitter": 280, "linkedin": 3000, "instagram": 2200,
        "facebook": 63206, "threads": 500, "tiktok": 2200,
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

    try:
        result = await call_ollama(prompt, schema=schema)
    except Exception:
        result = {
            "sentiment": "neutral",
            "readability_score": 7.0,
            "estimated_reach": "medium",
            "suggestions": [],
            "hashtag_score": 5,
            "engagement_prediction": "medium",
        }

    plain_english_issues = [
        {
            "field": issue.field,
            "reason": issue.reason,
            "matches": issue.matches,
        }
        for issue in check_plain_english(request.content)
    ]

    sentences = [s for s in re.split(r"[.!?]+", request.content) if s.strip()]
    avg_sentence_words = (
        sum(len(s.split()) for s in sentences) / max(1, len(sentences))
        if sentences
        else 0.0
    )

    # ── SEO scoring + spellcheck issues ────────────────────────────────
    from app.services import seo as _seo_service

    seo_score = None
    try:
        team_result = await db.execute(
            select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
        )
        team = team_result.scalars().first()
        seo_result = await _seo_service.analyze_seo(
            text=request.content,
            platform=request.platform,
            db=db,
            team_id=team.id if team else None,
        )
        seo_score = seo_result.get("score", {})
    except Exception:
        pass

    spellcheck_issues = None
    try:
        import httpx as _httpx

        from app.core.config import get_settings as _get_settings
        _settings = _get_settings()
        async with _httpx.AsyncClient(timeout=8.0) as _client:
            _resp = await _client.post(
                f"{_settings.LANGUAGETOOL_URL.rstrip('/')}/v2/check",
                data={"text": request.content, "language": "en-US"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            _resp.raise_for_status()
            spellcheck_issues = _resp.json().get("matches", [])
    except Exception:
        pass

    return AnalyzeContentResponse(
        sentiment=result.get("sentiment", "neutral"),
        readability_score=float(result.get("readability_score", 7.0)),
        estimated_reach=result.get("estimated_reach", "medium"),
        suggestions=result.get("suggestions", []) + [i["reason"] for i in plain_english_issues],
        hashtag_score=int(result.get("hashtag_score", 5)),
        engagement_prediction=result.get("engagement_prediction", "medium"),
        plain_english_issues=plain_english_issues,
        average_sentence_words=round(avg_sentence_words, 1),
        seo_score=seo_score,
        spellcheck_issues=spellcheck_issues,
    )


@router.post("/seo", response_model=SeoResponse)
async def analyze_seo_endpoint(
    request: SeoRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Analyze content for SEO: keywords, score, meta title/description, and recommendations."""
    team_result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = team_result.scalars().first()
    team_id = team.id if team else None

    result = await seo.analyze_seo(
        request.content,
        platform=request.platform,
        title=request.title,
        db=db,
        team_id=team_id,
    )
    return SeoResponse(**result)


class GenerateImageRequest(BaseModel):
    prompt: str
    negative_prompt: str = ""
    cfg_scale: float = 3.5
    seed: int = 0
    steps: int = 4
    width: int = 1024
    height: int = 1024
    provider: str = "local-diffusers"  # local-diffusers | cloudflare | nvidia-flux-dev
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
    """Generate an image via a text-to-image provider (local Diffusers, Cloudflare, or NVIDIA FLUX)."""
    import base64

    from app.services.inference import (
        _call_local_diffusers_txt2img,
        _call_workers_ai_image,
        _get_provider_config,
        _is_workers_ai_image_model,
    )

    team_result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = team_result.scalars().first()
    team_id = team.id if team else None

    # Enhance prompt with brand visual identity if available
    enhanced_prompt = request.prompt
    try:
        from app.models.brand import Brand, BrandVisual, BrandVoice
        from app.services.brand_assets import build_brand_image_prompt
        brand_result = await db.execute(select(Brand).where(Brand.team_id == team_id))
        brand = brand_result.scalars().first()
        if brand:
            visual_result = await db.execute(select(BrandVisual).where(BrandVisual.brand_id == brand.id))
            visual = visual_result.scalars().first()
            voice_result = await db.execute(select(BrandVoice).where(BrandVoice.brand_id == brand.id))
            voice = voice_result.scalars().first()
            if visual:
                visual_dict = {
                    "primary_color": visual.primary_color,
                    "accent_color": visual.accent_color,
                    "neutral_colors": visual.neutral_colors,
                    "image_style": visual.image_style,
                    "photography_direction": visual.photography_direction,
                }
                voice_dict = None
                if voice:
                    voice_dict = {"tone_dimensions": voice.tone_dimensions}
                enhanced_prompt = build_brand_image_prompt(visual_dict, voice_dict, request.prompt)
    except Exception:
        pass  # Brand enhancement is optional

    # Check chroma for similar generated images before submitting
    similar: list[str] = []
    if team:
        similar = await chroma_client.query_similar(str(team.id), enhanced_prompt, n_results=3)

    provider_name = request.provider or "local-diffusers"

    if provider_name == "local-diffusers":
        # Local Diffusers text-to-image (SD 1.5, GPU — free, no API key).
        # Falls back to Cloudflare on failure.
        result = None
        try:
            result = await _call_local_diffusers_txt2img(
                prompt=enhanced_prompt,
                negative_prompt=request.negative_prompt,
                width=request.width,
                height=request.height,
                steps=request.steps,
                cfg_scale=request.cfg_scale,
                seed=request.seed,
            )
        except HTTPException as exc:
            logger.warning(f"[ai/generate-image] Local Diffusers failed ({exc.status_code}: {exc.detail[:80]}), falling back to Cloudflare")

        if result is None:
            # Fall back to Cloudflare Workers AI (the ONLY cloud fallback)
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
            try:
                result = await _call_workers_ai_image(
                    prompt=enhanced_prompt,
                    model=model,
                    api_key=api_key,
                    negative_prompt=request.negative_prompt,
                    width=request.width,
                    height=request.height,
                    steps=request.steps,
                    cfg_scale=request.cfg_scale,
                )
            except HTTPException as exc:
                logger.warning(f"[ai/generate-image] CF also failed ({exc.status_code})")

            if result is None:
                raise HTTPException(
                    status_code=502,
                    detail="All image generation providers exhausted (local Diffusers + Cloudflare failed)",
                )

        image_base64 = result["image_base64"]
    elif provider_name == "cloudflare":
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
        result = None
        try:
            result = await _call_workers_ai_image(
                prompt=enhanced_prompt,
                model=model,
                api_key=api_key,
                negative_prompt=request.negative_prompt,
                width=request.width,
                height=request.height,
                steps=request.steps,
                cfg_scale=request.cfg_scale,
            )
        except HTTPException as exc:
            logger.warning(f"[ai/generate-image] CF failed ({exc.status_code})")

        if result is None:
            raise HTTPException(
                status_code=502,
                detail="Cloudflare image generation failed (no fallback configured — local Diffusers is the primary, CF is the only cloud fallback)",
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

    # Auto-save successful generation as a reusable workflow template
    if asset and team:
        try:
            await _save_generation_template(
                db=db,
                team=team,
                user=current_user,
                name=f"Image: {request.prompt[:60]}",
                category="image",
                prompt_template=request.prompt,
                settings={
                    "type": "image",
                    "provider": provider_name,
                    "model": request.model or CF_TXT2IMG_FREE,
                    "steps": request.steps,
                    "negative_prompt": request.negative_prompt,
                    "cfg_scale": request.cfg_scale,
                    "seed": request.seed,
                },
                tags=["image", "auto-saved"],
            )
        except Exception:
            pass

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
    platform: str  # linkedin, twitter, instagram, facebook, threads, tiktok
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
    """Post a saved draft to the selected social platform."""
    from app.models.social_account import SocialAccount

    team_result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = team_result.scalars().first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    # Resolve social account
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

    # Fetch draft from Chroma
    raw = await chroma_client.get_content(str(team.id), request.draft_id)
    if not raw or not raw.startswith("DRAFT:"):
        raise HTTPException(status_code=404, detail="Draft not found")

    try:
        draft_data = json.loads(raw[6:])
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"Draft is corrupted: {exc}")

    # Persist draft image as a media asset (if present)
    media_ids: list[uuid.UUID] = []
    image_base64 = draft_data.get("image_base64") or ""
    if image_base64:
        try:
            image_bytes = base64.b64decode(image_base64)
            asset = await persist_generated_image(
                db,
                team_id=team.id,
                user_id=current_user.id,
                image_bytes=image_bytes,
                prompt=draft_data.get("prompt", "post-draft"),
                source="post-draft",
            )
            media_ids.append(asset.id)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Could not process draft image: {exc}")

    caption = request.caption or draft_data.get("caption") or ""
    hashtags = request.hashtags or draft_data.get("hashtags") or []

    post = Post(
        team_id=team.id,
        user_id=current_user.id,
        status=PostStatus.SCHEDULED,
        content_text=caption,
        media_ids=media_ids,
        hashtags=hashtags,
        link_url=draft_data.get("link_url"),
        platform_specific={request.platform: {"caption": caption, "hashtags": hashtags}},
        scheduled_at=datetime.now(UTC),
    )
    db.add(post)
    await db.flush()
    db.add(PostTarget(post_id=post.id, social_account_id=account.id, status="pending"))
    await db.commit()
    await db.refresh(post)

    # Queue for immediate publishing
    try:
        with celery_app.connection_or_acquire() as conn:
            publish_post_now.apply_async(
                args=[str(post.id), [str(account.id)]],
                connection=conn,
            )
            process_publish_queue.apply_async(connection=conn, countdown=2)
    except Exception as exc:
        return PostDraftResponse(
            success=False,
            post_id=str(post.id),
            message=f"Draft saved but could not be queued: {type(exc).__name__}: {exc}",
        )

    return PostDraftResponse(
        success=True,
        post_id=str(post.id),
        message=f"Draft queued for {request.platform}",
    )


def _validate_comfyui_job_id(job_id: str) -> str:
    """Return a validated ComfyUI job ID.

    The regex matches only safe characters; the original value is returned so
    CodeQL's taint tracker sees the validated input as a sanitizer.
    """
    if not re.fullmatch(r"^[a-zA-Z0-9\-]{1,64}$", job_id):
        raise HTTPException(status_code=400, detail="Invalid job ID")
    return job_id


@router.get("/generate-image/{job_id}")
async def get_image_status(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    safe_job_id = _validate_comfyui_job_id(job_id)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{settings.COMFYUI_URL}/history/{safe_job_id}")
            if resp.status_code == 200:
                history = resp.json()
                if safe_job_id in history:
                    outputs = history[safe_job_id].get("outputs", {})
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
                                        MediaAsset.generation_prompt == f"comfyui:{safe_job_id}"
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
                                        # Keep the human-readable ComfyUI filename and a stable
                                        # internal key for duplicate detection.
                                        asset.filename = filename
                                        asset.generation_prompt = f"comfyui:{safe_job_id}"
                                        asset.alt_text = f"comfyui:{safe_job_id}"
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

    # Load brand context for on-brand generation
    brand_context_str = ""
    brand_dict: dict | None = None
    voice_dict: dict | None = None
    if team:
        from app.models.brand import Brand
        from app.services.brand_compliance import build_brand_system_prompt
        brand_result = await db.execute(
            select(Brand)
            .options(selectinload(Brand.voice))
            .where(Brand.team_id == team.id)
        )
        brand = brand_result.scalars().first()
        if brand:
            brand_dict = {
                "name": brand.name,
                "positioning_statement": brand.positioning_statement,
                "mission": brand.mission,
                "values": brand.values or [],
                "tagline": brand.tagline,
                "target_audience": brand.target_audience or {},
            }
            if brand.voice:
                voice_dict = {
                    "tone_dimensions": brand.voice.tone_dimensions or {},
                    "messaging_pillars": brand.voice.messaging_pillars or [],
                    "banned_phrases": brand.voice.banned_phrases or [],
                    "preferred_phrases": brand.voice.preferred_phrases or [],
                    "example_content": brand.voice.example_content,
                    "voice_signature": brand.voice.voice_signature or {},
                }
            brand_context_str = build_brand_system_prompt(brand_dict, voice_dict)

    # Load saved content prompt template if provided
    saved_template = None
    if request.template_id and team:
        from app.models.workflow import ContentPromptTemplate
        tpl_result = await db.execute(
            select(ContentPromptTemplate).where(
                ContentPromptTemplate.id == request.template_id,
                ContentPromptTemplate.team_id == team.id,
            )
        )
        saved_template = tpl_result.scalar_one_or_none()

    platform_guides = {
        "linkedin": (
            "Professional, thought-leadership style for LinkedIn. 3000 char limit. "
            "SEO: place the primary keyword in the first 140 characters (the mobile preview cutoff) — "
            "this is the hook that earns the 'see more' click. Use 2-3 semantic keyword variations "
            "throughout the body so LinkedIn's search engine can index the post. "
            "Structure for dwell time: hook → value proposition → substantive content (story, framework, "
            "or data) → closing question that invites a reply. Use line breaks for readability. "
            "Make the post save-worthy: include a checklist, framework, or data point someone would "
            "reference later. 3-5 hashtags only — more than 5 triggers spam-like signals. "
            "Mix niche (10K-500K posts), mid-tier, and one branded tag. Hashtags must be semantically "
            "relevant to the post text. Plain everyday English."
        ),
        "twitter": (
            "Concise, conversational for X/Twitter. 280 char limit. Thread-friendly. "
            "1-2 hashtags max — more dilutes the message. Place the keyword early. "
            "Plain everyday English."
        ),
        "instagram": (
            "Visual-first, engaging for Instagram. 2200 char limit. "
            "HARD CAP: maximum 5 hashtags per post (Instagram enforced this in December 2025 — "
            "extra tags are ignored and 20+ generic tags trigger distribution suppression). "
            "Use 3-5 hyper-relevant tags chosen per post, not recycled from a master list. "
            "Mix niche community, branded, and topic tags. Hashtags are classification signals "
            "for the algorithm, not reach drivers — caption keywords and on-screen text matter more. "
            "Use emojis. Plain everyday English."
        ),
        "facebook": (
            "Community-focused, conversational for Facebook. No strict limit. "
            "1-3 hashtags. Plain everyday English."
        ),
        "threads": (
            "Casual, conversational, text-first for Threads. 500 char limit. "
            "Minimal hashtags (0-2). Threads rewards authentic, human-sounding posts "
            "— write like a person, not a brand press release. Lead with a hook or "
            "opinion in the first line. Follow the brand voice signature and tone "
            "dimensions from the BRAND CONTEXT below. Plain everyday English."
        ),
        "tiktok": (
            "Short-form video/photo caption for TikTok. 2200 char limit. "
            "SEO: place the primary keyword in the first 150 characters — TikTok Search "
            "weights the opening caption text highest. Write 150-300 character captions for "
            "maximum indexable keyword surface area. Include long-tail keyword variations. "
            "3-5 targeted hashtags using a 3-tier mix: niche, mid-tier, and broad. "
            "More than 5 dilutes the topic signal and reduces FYP distribution. "
            "Hook in the first line. Use emojis. Plain everyday English."
        ),
    }

    guide = platform_guides.get(request.platform, platform_guides["linkedin"])

    from app.services.plain_english import PLAIN_ENGLISH_RULES, rewrite_plain_english

    # Build prompt — use saved template if available, otherwise default
    brand_section = f"\n\nBRAND CONTEXT:\n{brand_context_str}\n" if brand_context_str else ""
    if saved_template:
        # Replace variables in the user prompt template
        user_prompt = saved_template.user_prompt_template
        for var in saved_template.variables:
            placeholder = "{{" + var + "}}"
            if var == "topic":
                user_prompt = user_prompt.replace(placeholder, request.prompt)
            elif var == "platform":
                user_prompt = user_prompt.replace(placeholder, request.platform)
            elif var == "tone":
                user_prompt = user_prompt.replace(placeholder, request.tone)
            elif var == "brand":
                user_prompt = user_prompt.replace(placeholder, brand_context_str or "")
            elif var == "length":
                user_prompt = user_prompt.replace(placeholder, request.length)
        prompt = f"{saved_template.system_prompt}\n\n{user_prompt}\n\nReturn JSON with: content, hashtags (array), suggested_media (string or null)"
    else:
        prompt = f"""Write a {request.platform} post based on this prompt: "{request.prompt}"
{brand_section}
Platform guidelines: {guide}
Tone: {request.tone}
Length: {request.length}
Include hashtags: {request.include_hashtags}
Include emojis: {request.include_emojis}

{PLAIN_ENGLISH_RULES}

HASHTAG RULES (critical for 2026 platform algorithms):
- Choose hashtags that are semantically relevant to the post content. A disconnect between
  text and hashtags harms distribution.
- Prefer niche and mid-tier hashtags (10K-500K posts) over mega-tags — they outperform 3:1
  on reach-to-engagement ratio because your content can actually compete there.
- Include one branded hashtag (e.g. #cloudless) when relevant.
- Never copy-paste the same hashtag block across posts — platforms penalize this as spam.
- Respect each platform's hashtag cap (see platform guidelines above).

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

    # Score brand compliance if brand exists
    brand_compliance = None
    if team and brand_context_str and content:
        try:
            from app.services.brand_compliance import score_brand_compliance
            brand_compliance = await score_brand_compliance(
                content=content,
                brand=brand_dict,
                voice=voice_dict,
                platform=request.platform,
            )
        except Exception:
            pass  # non-fatal — compliance scoring is best-effort

    # ── Quality pipeline: NLP + spellcheck + SEO + auto-improve ───────
    from app.services.quality_pipeline import apply_quality_pipeline

    raw_hashtags = result.get("hashtags", [])
    quality = await apply_quality_pipeline(
        content=content,
        platform=request.platform,
        hashtags=raw_hashtags,
        db=db,
        team_id=team.id if team else None,
        provider_name=request.provider or "cloudflare",
        model=request.model,
        target_score=90,
        max_iterations=2,
        topic=request.prompt,
        tone=request.tone,
    )

    return GenerateContentResponse(
        content=quality.content,
        hashtags=quality.hashtags,
        suggested_media=result.get("suggested_media"),
        brand_compliance=brand_compliance,
        seo_score=quality.seo_score or None,
        nlp_report=quality.nlp_report or None,
        quality=quality.to_dict(),
    )


@router.post("/generate-hashtags", response_model=SuggestHashtagsResponse)
@router.post("/suggest-hashtags", response_model=SuggestHashtagsResponse)
async def suggest_hashtags(
    request: SuggestHashtagsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Platform-specific hashtag caps (2026 algorithm best practices).
    platform_caps: dict[str, int] = {
        "linkedin": 5,
        "twitter": 2,
        "instagram": 5,  # hard platform cap since December 2025
        "facebook": 3,
        "threads": 2,
        "tiktok": 5,
    }
    platform = (request.platform or "linkedin").lower()
    cap = platform_caps.get(platform, 5)
    requested = request.resolved_max()
    # Never exceed the platform cap, but allow fewer if the user asks for fewer.
    count = min(requested, cap)

    platform_context = {
        "linkedin": "LinkedIn — professional audience, 3-5 niche/mid-tier hashtags semantically relevant to the post content.",
        "twitter": "X/Twitter — 1-2 hashtags max, concise and topical.",
        "instagram": "Instagram — 3-5 hyper-relevant hashtags (hard 5-tag cap). Mix niche community, branded, and topic tags.",
        "facebook": "Facebook — 1-3 hashtags, community-focused.",
        "threads": "Threads — 0-2 hashtags, minimal.",
        "tiktok": "TikTok — 3-5 hashtags using a 3-tier mix: niche, mid-tier, and broad. Must match the caption topic.",
    }
    guide = platform_context.get(platform, platform_context["linkedin"])

    prompt = f"""Suggest {count} hashtags for this {platform} post.

Platform: {guide}

HASHTAG STRATEGY (2026 best practices):
- Choose hashtags semantically relevant to the post content. A disconnect between text and
  hashtags harms distribution on all platforms.
- Prefer niche and mid-tier hashtags (10K-500K posts) over mega-tags — they outperform 3:1
  on reach-to-engagement ratio because content can actually compete there.
- Include one branded hashtag (e.g. cloudless) when relevant.
- Do not include generic spam-like tags (e.g. #motivation, #love, #follow) unless directly
  relevant to the post topic.

Post content:
"{request.content}"

Return JSON with: hashtags (array of strings without #)"""

    schema = {
        "type": "object",
        "properties": {
            "hashtags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["hashtags"],
    }

    # Use the full inference chain (Cloudflare-first) instead of Ollama-only,
    # so hashtag suggestions benefit from the same provider fallback as content generation.
    result = await call_inference(prompt, provider_name="cloudflare", db=db, schema=schema)

    hashtags = [str(h).lstrip("#").strip() for h in result.get("hashtags") or [] if str(h).strip()]
    return SuggestHashtagsResponse(hashtags=hashtags[:count])


@router.post("/suggest-hashtags-tiered", response_model=SuggestHashtagsTieredResponse)
async def suggest_hashtags_tiered(
    request: SuggestHashtagsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Suggest hashtags with three-tier strategy: safe, rising, niche.

    Inspired by PulseTag's three-tier approach:
    - **Safe**: high-volume, broad tags for baseline visibility
    - **Rising**: trending, mid-volume tags for current relevance
    - **Niche**: specific, low-competition tags for high-intent audiences

    Returns both a flat ``hashtags`` list (backwards compatible) and a
    ``tiers`` dict with classified tags.
    """
    platform_caps: dict[str, int] = {
        "linkedin": 5,
        "twitter": 2,
        "instagram": 5,
        "facebook": 3,
        "threads": 2,
        "tiktok": 5,
    }
    platform = (request.platform or "linkedin").lower()
    cap = platform_caps.get(platform, 5)
    count = min(request.resolved_max(), cap)

    prompt = f"""Suggest {count} hashtags for this {platform} post, classified into three tiers.

THREE-TIER HASHTAG STRATEGY:
- "safe": 1-2 high-volume, broad hashtags (1M+ posts) for baseline visibility
- "rising": 1-2 trending, mid-volume hashtags (100K-1M posts) for current relevance
- "niche": 1-2 specific, low-competition hashtags (<100K posts) for high-intent audiences

Rules:
- Hashtags must be semantically relevant to the post content
- Include one branded hashtag (e.g. cloudless) in the niche tier when relevant
- No generic spam tags (e.g. #motivation, #love, #follow)
- Total hashtags must not exceed {count}

Post content:
"{request.content}"

Return JSON with:
- "safe": array of {{"tag": "hashtag_without_#", "estimated_reach": "broad"}}
- "rising": array of {{"tag": "hashtag_without_#", "estimated_reach": "mid"}}
- "niche": array of {{"tag": "hashtag_without_#", "estimated_reach": "low"}}"""

    schema = {
        "type": "object",
        "properties": {
            "safe": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tag": {"type": "string"},
                        "estimated_reach": {"type": "string"},
                    },
                    "required": ["tag", "estimated_reach"],
                },
            },
            "rising": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tag": {"type": "string"},
                        "estimated_reach": {"type": "string"},
                    },
                    "required": ["tag", "estimated_reach"],
                },
            },
            "niche": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tag": {"type": "string"},
                        "estimated_reach": {"type": "string"},
                    },
                    "required": ["tag", "estimated_reach"],
                },
            },
        },
        "required": ["safe", "rising", "niche"],
    }

    result = await call_inference(prompt, provider_name="cloudflare", db=db, schema=schema)

    tiers: dict[str, list[HashtagTier]] = {"safe": [], "rising": [], "niche": []}
    flat: list[str] = []

    for tier_name in ("safe", "rising", "niche"):
        for item in result.get(tier_name) or []:
            tag = str(item.get("tag", "")).lstrip("#").strip()
            if not tag:
                continue
            reach = str(item.get("estimated_reach", "")).strip()
            tiers[tier_name].append(HashtagTier(tag=tag, tier=tier_name, estimated_reach=reach))
            flat.append(tag)

    return SuggestHashtagsTieredResponse(
        hashtags=flat[:count],
        tiers=tiers,
        strategy=f"Three-tier: {len(tiers['safe'])} safe + {len(tiers['rising'])} rising + {len(tiers['niche'])} niche",
    )


@router.post("/best-time-to-post", response_model=BestTimeResponse)
async def best_time_to_post(
    request: BestTimeRequest,
    current_user: User = Depends(get_current_user),
    db=Depends(get_db),
):
    """Analyze historical engagement data to recommend best posting times.

    Uses PostAnalyticsSnapshot data for the account to find the day-of-week
    and hour combinations with the highest average engagement_rate. Falls
    back to platform defaults when insufficient data exists (fewer than 10
    snapshots).
    """
    from collections import defaultdict

    from app.models.analytics import PostAnalyticsSnapshot

    result = await db.execute(
        select(SocialAccount).where(SocialAccount.id == request.account_id)
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # Query historical analytics snapshots for this account
    snapshots_result = await db.execute(
        select(PostAnalyticsSnapshot)
        .where(PostAnalyticsSnapshot.social_account_id == account.id)
        .order_by(PostAnalyticsSnapshot.captured_at.desc())
        .limit(200)
    )
    snapshots = snapshots_result.scalars().all()

    # Platform defaults (used when insufficient data)
    platform_defaults = {
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
        "threads": [
            {"day": "Monday", "time": "11:00", "timezone": "Europe/Athens"},
            {"day": "Wednesday", "time": "12:00", "timezone": "Europe/Athens"},
            {"day": "Friday", "time": "10:00", "timezone": "Europe/Athens"},
        ],
        "tiktok": [
            {"day": "Tuesday", "time": "14:00", "timezone": "Europe/Athens"},
            {"day": "Thursday", "time": "16:00", "timezone": "Europe/Athens"},
            {"day": "Sunday", "time": "13:00", "timezone": "Europe/Athens"},
        ],
    }

    # Need at least 10 snapshots for meaningful analysis
    if len(snapshots) < 10:
        defaults = platform_defaults.get(account.platform, platform_defaults["linkedin"])
        return BestTimeResponse(best_times=defaults)

    # Also get the published_at timestamps from PostTarget to know when posts were published
    # Group engagement by day-of-week and hour
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    # engagement_by_slot: {(day_idx, hour): [engagement_rates]}
    engagement_by_slot: dict[tuple[int, int], list[float]] = defaultdict(list)

    for snap in snapshots:
        # Use captured_at as proxy for when the post was active
        # Ideally we'd use the post's published_at, but snapshots capture metrics over time
        dt = snap.captured_at
        if dt is None:
            continue
        day_idx = dt.weekday()
        hour = dt.hour
        engagement_by_slot[(day_idx, hour)].append(snap.engagement_rate)

    # Calculate average engagement per slot
    slot_scores: list[tuple[tuple[int, int], float]] = []
    for slot, rates in engagement_by_slot.items():
        avg = sum(rates) / len(rates)
        slot_scores.append((slot, avg))

    # Sort by engagement score descending
    slot_scores.sort(key=lambda x: x[1], reverse=True)

    # Take top 3 slots
    best_times = []
    for (day_idx, hour), score in slot_scores[:3]:
        best_times.append({
            "day": day_names[day_idx],
            "time": f"{hour:02d}:00",
            "timezone": "Europe/Athens",
            "avg_engagement_rate": round(score, 4),
        })

    if not best_times:
        defaults = platform_defaults.get(account.platform, platform_defaults["linkedin"])
        return BestTimeResponse(best_times=defaults)

    return BestTimeResponse(best_times=best_times)


@router.post("/improve-content", response_model=ImproveContentResponse)
async def improve_content(
    request: ImproveContentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
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
    improved = result.get("improved_content", request.content)

    # ── Quality pipeline: spellcheck + NLP + SEO + auto-improve ───────
    from app.services.quality_pipeline import apply_quality_pipeline

    team_result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = team_result.scalars().first()

    quality = await apply_quality_pipeline(
        content=improved,
        platform=request.platform,
        hashtags=[],
        db=db,
        team_id=team.id if team else None,
        provider_name="cloudflare",
        target_score=90,
        max_iterations=2,
    )

    return ImproveContentResponse(
        improved_content=quality.content,
        changes=result.get("changes", []),
        seo_score=quality.seo_score or None,
        nlp_report=quality.nlp_report or None,
        quality=quality.to_dict() if quality.improved else None,
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

    # Add LLM processing node (always needed — content generation is core)
    node_y += 100
    nodes.append({
        "name": "Process Content",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 1,
        "position": [250, node_y],
        "parameters": {
            "url": f"{settings.DMR_URL}/chat/completions",
            "method": "POST",
            "jsonParameters": True,
            "options": {
                "model": settings.DMR_TEXT_MODEL,
                "prompt": "Process: {{ $json.content }}",
                "format": "json",
            },
        },
    })

    # Add image generation if needed (via social-api Cloudflare Workers AI)
    if intent.get("needs_image"):
        node_y += 100
        nodes.append({
            "name": "Generate Image",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 1,
            "position": [250, node_y],
            "parameters": {
                "url": "={{ ($env.SOCIAL_API_URL || 'http://social-api:8000') + '/api/v1/ai/generate-image' }}",
                "method": "POST",
                "jsonParameters": True,
                "options": {
                    "prompt": "{{ $json.prompt || $json.content }}",
                    "provider": "cloudflare",
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

    # ── Spellcheck the caption + SEO scoring ───────────────────────────
    from app.services import seo as _seo_service
    from app.services.spellcheck import auto_correct as _auto_correct

    raw_hashtags = result.get("hashtags", [])
    try:
        cleaned_caption = await _auto_correct(cleaned_caption)
    except Exception:
        pass  # spellcheck is advisory

    # SEO scoring on caption + hashtags
    seo_score = None
    try:
        full_text = cleaned_caption
        if raw_hashtags:
            tag_str = " ".join(f"#{h.lstrip('#')}" for h in raw_hashtags)
            full_text = f"{full_text}\n\n{tag_str}"
        seo_result = await _seo_service.analyze_seo(
            text=full_text,
            platform=request.platform,
            db=db,
            team_id=team_id,
        )
        seo_score = seo_result.get("score", {})
    except Exception:
        pass  # SEO is advisory

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
        hashtags=raw_hashtags,
        seo_score=seo_score,
        nlp_report=_nlp_report.to_dict() if hasattr(_nlp_report, "to_dict") else None,
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


class GenerateCarouselPipelineResponse(BaseModel):
    slides: list[CarouselPipelineSlideResult]
    suggested_caption: str
    hashtags: list[str]
    media_ids: list[uuid.UUID]
    models: dict
    nlp_report: dict = {}
    seo_score: dict | None = None


@router.post("/generate-carousel-pipeline", response_model=GenerateCarouselPipelineResponse)
async def generate_carousel_pipeline(
    request: GenerateCarouselPipelineRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """CF-only carousel pipeline:

    1. LLM slide copy
    2. NLP checker + plain-English fixer
    3. FLUX schnell txt2img background generation
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
        allow_fallback=True,
    )
    logger.info(f"[carousel-pipeline] nlp {nlp_report.to_dict()}")

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
        logger.info(f"[carousel-pipeline] slide {i + 1}/{len(cleaned_slides)} txt2img")
        pipe = await _call_cf_image_pipeline(
            prompt=visual,
            enhance_prompt=enhance,
            txt2img_model=request.txt2img_model,
            allow_fallback=False,
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

    # ── Spellcheck the caption + SEO scoring ───────────────────────────
    from app.services import seo as _seo_service
    from app.services.spellcheck import auto_correct as _auto_correct

    try:
        cleaned_caption = await _auto_correct(cleaned_caption)
    except Exception:
        pass  # spellcheck is advisory

    seo_score = None
    try:
        full_text = cleaned_caption
        if copy.hashtags:
            tag_str = " ".join(f"#{h.lstrip('#')}" for h in copy.hashtags)
            full_text = f"{full_text}\n\n{tag_str}"
        seo_result = await _seo_service.analyze_seo(
            text=full_text,
            platform=request.platform,
            db=db,
            team_id=team.id,
        )
        seo_score = seo_result.get("score", {})
    except Exception:
        pass  # SEO is advisory

    pipeline_response = GenerateCarouselPipelineResponse(
        slides=slide_results,
        suggested_caption=cleaned_caption,
        hashtags=copy.hashtags,
        media_ids=media_ids,
        models={
            "text": request.text_model,
            "txt2img": request.txt2img_model,
            "nlp": "plain-english-check-fix",
            "spellcheck": "languagetool",
            "seo": "platform-scored",
        },
        nlp_report=nlp_report.to_dict(),
        seo_score=seo_score,
    )

    # Auto-save successful generation as a reusable workflow template
    if media_ids and team:
        try:
            await _save_generation_template(
                db=db,
                team=team,
                user=current_user,
                name=f"Carousel: {request.topic[:60]}",
                category="carousel",
                prompt_template=request.topic,
                settings={
                    "type": "carousel",
                    "platform": request.platform,
                    "num_slides": request.num_slides,
                    "tone": request.tone,
                    "text_model": request.text_model,
                    "txt2img_model": request.txt2img_model,
                    "slide_count": len(slide_results),
                },
                tags=["carousel", "auto-saved", request.platform],
            )
        except Exception:
            pass

    return pipeline_response


class RunCarouselAndPublishRequest(BaseModel):
    topic: str = (
        "How cloudless.gr helps teams ship serverless apps without managing servers"
    )
    num_slides: int = 7
    tone: str = "clear and friendly"
    include_cta: bool = True
    text_model: str = CF_TEXT_FREE
    text_provider: str = "dmr"  # DMR (local) primary; falls back to cloudflare on failure
    txt2img_model: str = CF_TXT2IMG_FREE
    target_account_id: str | None = None
    publish: bool = True
    wait_for_publish: bool = False
    custom_slides: list[dict] | None = None  # override AI-generated copy with exact slide content
    custom_caption: str | None = None  # override AI-generated caption
    custom_hashtags: list[str] | None = None  # override AI-generated hashtags


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

    result = await run_cloudless_carousel_pipeline(
        db=db,
        user=current_user,
        team=team,
        topic=request.topic,
        num_slides=request.num_slides,
        tone=request.tone,
        include_cta=request.include_cta,
        text_model=request.text_model,
        text_provider=request.text_provider,
        txt2img_model=request.txt2img_model,
        target_account_id=request.target_account_id,
        publish=request.publish,
        wait_for_publish=request.wait_for_publish,
        custom_slides=request.custom_slides,
        custom_caption=request.custom_caption,
        custom_hashtags=request.custom_hashtags,
    )

    # Auto-save successful run as a reusable workflow template
    try:
        await _save_generation_template(
            db=db,
            team=team,
            user=current_user,
            name=f"Carousel: {request.topic[:60]}",
            category="carousel",
            prompt_template=request.topic,
            settings={
                "type": "carousel",
                "platform": "linkedin",
                "num_slides": request.num_slides,
                "tone": request.tone,
                "text_model": request.text_model,
                "txt2img_model": request.txt2img_model,
            },
            tags=["carousel", "auto-saved"],
        )
    except Exception:
        pass  # never fail the main response

    return result


# ── Generation template helpers ───────────────────────────────────────────────

class SaveGenerationTemplateRequest(BaseModel):
    name: str
    category: str = "generation"
    prompt_template: str
    settings: dict = {}
    tags: list[str] = []
    is_public: bool = False


class SaveGenerationTemplateResponse(BaseModel):
    id: uuid.UUID
    name: str
    category: str | None
    tags: list[str]
    created_at: datetime


async def _save_generation_template(
    *,
    db: AsyncSession,
    team,
    user,
    name: str,
    category: str,
    prompt_template: str,
    settings: dict,
    tags: list[str],
    is_public: bool = False,
) -> PromptTemplate:
    """Upsert a PromptTemplate with the generation settings (idempotent by name+team)."""
    existing = await db.execute(
        select(PromptTemplate).where(
            PromptTemplate.team_id == team.id,
            PromptTemplate.name == name,
        )
    )
    tmpl = existing.scalar_one_or_none()
    if tmpl:
        tmpl.n8n_workflow_json = settings
        tmpl.tags = tags
    else:
        tmpl = PromptTemplate(
            team_id=team.id,
            user_id=user.id,
            name=name,
            description=f"Auto-saved from successful {category} generation",
            prompt_template=prompt_template,
            n8n_workflow_json=settings,
            category=category,
            tags=tags,
            is_public=is_public,
        )
        db.add(tmpl)
    await db.commit()
    await db.refresh(tmpl)
    assert tmpl is not None
    return tmpl


@router.post("/save-generation-template", response_model=SaveGenerationTemplateResponse)
async def save_generation_template(
    request: SaveGenerationTemplateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save a successful generation run as a reusable template in the Workflows page."""
    team_result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = team_result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    tmpl = await _save_generation_template(
        db=db,
        team=team,
        user=current_user,
        name=request.name,
        category=request.category,
        prompt_template=request.prompt_template,
        settings=request.settings,
        tags=request.tags,
        is_public=request.is_public,
    )
    return SaveGenerationTemplateResponse(
        id=tmpl.id,
        name=tmpl.name,
        category=tmpl.category,
        tags=tmpl.tags,
        created_at=tmpl.created_at,
    )


@router.get("/workflow-config/{content_type}")
async def get_workflow_config(
    content_type: str,
    current_user: User = Depends(get_current_user),
):
    """Return the auto-selected model config for a given content type."""
    config = CONTENT_WORKFLOW_CONFIGS.get(content_type)
    if not config:
        raise HTTPException(status_code=404, detail=f"No workflow config for content type: {content_type}")
    return config


class SpellcheckMatch(BaseModel):
    message: str
    offset: int
    length: int
    replacements: list[str]
    rule_id: str
    context: str


class SpellcheckRequest(BaseModel):
    text: str
    language: str = "en-US"


class SpellcheckResponse(BaseModel):
    matches: list[SpellcheckMatch]
    language: str


@router.post("/spellcheck", response_model=SpellcheckResponse)
async def spellcheck(
    request: SpellcheckRequest,
    current_user: User = Depends(get_current_user),
):
    """Check spelling and grammar via the self-hosted LanguageTool service."""
    if not request.text.strip():
        return SpellcheckResponse(matches=[], language=request.language)

    lt_url = settings.LANGUAGETOOL_URL.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{lt_url}/v2/check",
                data={"text": request.text, "language": request.language},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"LanguageTool error: {exc.response.status_code}")
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="LanguageTool service unreachable — is it running?")

    data = resp.json()
    matches = []
    for m in data.get("matches", []):
        matches.append(SpellcheckMatch(
            message=m.get("message", ""),
            offset=m.get("offset", 0),
            length=m.get("length", 0),
            replacements=[r["value"] for r in m.get("replacements", [])[:5]],
            rule_id=m.get("rule", {}).get("id", ""),
            context=m.get("context", {}).get("text", ""),
        ))

    return SpellcheckResponse(matches=matches, language=request.language)


@router.post("/seed-default-workflows")
async def seed_default_workflows(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upsert one default PromptTemplate per content type so Workflows page shows them."""
    team_result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = team_result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    # Delete ALL existing default templates for this team first (handles duplicates
    # created by parallel calls from React Strict Mode double-invocation).
    await db.execute(
        delete(PromptTemplate).where(
            PromptTemplate.team_id == team.id,
            PromptTemplate.name.like("[Default]%"),
        )
    )
    await db.flush()

    seeded: list[str] = []
    for content_type, config in CONTENT_WORKFLOW_CONFIGS.items():
        template_name = f"[Default] {config['name']}"
        primary_model = config.get("text_model") or config.get("txt2img_model", "")
        prompt_text = (
            f"Default {content_type} workflow. "
            f"Primary model: {primary_model}. "
            f"Fallback: {config.get('hf_text_fallback') or config.get('hf_txt2img_fallback', 'HF free tier')}."
        )
        db.add(PromptTemplate(
            id=str(uuid.uuid4()),
            name=template_name,
            description=config["description"],
            prompt_template=prompt_text,
            category=content_type,
            tags=[content_type, "default", "auto"],
            is_public=False,
            team_id=team.id,
            n8n_workflow_json=config,
        ))
        seeded.append(template_name)

    await db.commit()
    return {"seeded": seeded}


