from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import httpx
import json
import uuid
from datetime import datetime, UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.api.auth import get_current_user
from app.core.config import get_settings
from app.models.user import User, Team, TeamMember
from app.models.social_account import SocialAccount
from app.models.workflow import PromptTemplate, GeneratedWorkflow
from app.services import chroma_client

router = APIRouter()
settings = get_settings()


class GenerateContentRequest(BaseModel):
    prompt: str
    platform: str
    tone: str = "professional"
    length: str = "medium"
    include_hashtags: bool = True
    include_emojis: bool = True


class GenerateContentResponse(BaseModel):
    content: str
    hashtags: List[str]
    suggested_media: str | None = None


class SuggestHashtagsRequest(BaseModel):
    content: str
    platform: str
    max_hashtags: int = 10


class SuggestHashtagsResponse(BaseModel):
    hashtags: List[str]


class BestTimeRequest(BaseModel):
    account_id: uuid.UUID


class BestTimeResponse(BaseModel):
    best_times: List[dict]


class ImproveContentRequest(BaseModel):
    content: str
    platform: str
    goal: str = "engagement"


class ImproveContentResponse(BaseModel):
    improved_content: str
    changes: List[str]


class GenerateWorkflowRequest(BaseModel):
    prompt: str
    template_id: uuid.UUID | None = None


class GenerateWorkflowResponse(BaseModel):
    n8n_workflow_json: dict
    variables_used: dict
    template_id: uuid.UUID | None


async def call_ollama(prompt: str, model: str = None, schema: dict = None) -> dict:
    """Call Ollama API with optional structured output."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        payload = {
            "model": model or settings.OLLAMA_DEFAULT_MODEL,
            "prompt": prompt,
            "stream": False,
        }
        if schema:
            payload["format"] = "json"
            payload["schema"] = schema

        resp = await client.post(f"{settings.OLLAMA_URL}/api/generate", json=payload)
        if resp.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Ollama error: {resp.text}")

        result = resp.json()
        response_text = result.get("response", "")

        if schema:
            try:
                return json.loads(response_text)
            except json.JSONDecodeError:
                # Try to extract JSON from response
                import re
                json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
                if json_match:
                    return json.loads(json_match.group())
                raise HTTPException(status_code=500, detail="Failed to parse Ollama response")

        return {"text": response_text}


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

    result = await call_ollama(prompt, schema=schema)
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
            .where(PromptTemplate.category == intent.get("intent"), PromptTemplate.is_public == True)
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


