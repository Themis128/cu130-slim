import json
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
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
from app.services.inference import call_inference, get_team_id_for_user

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
    provider: str = "ollama"
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
    provider: str = "ollama"
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
    """Backwards-compatible shim — delegates to the unified inference service."""
    return await call_inference(prompt, provider_name="ollama", schema=schema, model_override=model)


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
    fill_pct = char_count / limit if limit else 0

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
    width: int = 1024
    height: int = 1024
    steps: int = 20
    cfg_scale: float = 7.0


class GenerateImageResponse(BaseModel):
    job_id: str
    status: str
    image_url: str | None = None
    similar_content: list[str] = []


@router.post("/generate-image", response_model=GenerateImageResponse)
async def generate_image(
    request: GenerateImageRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team_result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = team_result.scalars().first()

    # Check chroma for similar generated images before submitting
    similar: list[str] = []
    if team:
        similar = await chroma_client.query_similar(str(team.id), request.prompt, n_results=3)

    # Submit prompt to ComfyUI queue directly
    comfyui_prompt = {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "cfg": request.cfg_scale,
                "denoise": 1,
                "latent_image": ["5", 0],
                "model": ["4", 0],
                "negative": ["7", 0],
                "positive": ["6", 0],
                "sampler_name": "euler",
                "scheduler": "normal",
                "seed": 42,
                "steps": request.steps,
            },
        },
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "v1-5-pruned-emaonly.safetensors"}},
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"batch_size": 1, "height": request.height, "width": request.width},
        },
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1], "text": request.prompt}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1], "text": request.negative_prompt}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "social_", "images": ["8", 0]}},
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{settings.COMFYUI_URL}/prompt",
                json={"prompt": comfyui_prompt, "client_id": str(current_user.id)},
            )
            resp.raise_for_status()
            job_id = resp.json().get("prompt_id", "")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"ComfyUI unavailable: {exc}")

    # Store prompt in chroma so future generations can detect duplicates
    if team and request.prompt:
        await chroma_client.add_content(str(team.id), job_id, request.prompt)

    return GenerateImageResponse(job_id=job_id, status="queued", similar_content=similar)


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

    return GenerateCarouselResponse(
        slides=slides,
        suggested_caption=result.get("suggested_caption", ""),
        hashtags=result.get("hashtags", []),
    )


