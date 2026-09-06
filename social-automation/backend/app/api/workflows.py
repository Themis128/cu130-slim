import uuid
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.api.deps import TeamId
from app.core.config import get_settings
from app.db.session import get_db
from app.models.user import Team, TeamMember, User
from app.models.workflow import ContentPromptTemplate, GeneratedWorkflow, PromptTemplate

router = APIRouter()
settings = get_settings()


class PromptTemplateCreate(BaseModel):
    name: str
    description: str | None = None
    prompt_template: str
    n8n_workflow_json: dict
    category: str | None = None
    tags: list[str] = []
    is_public: bool = False


class PromptTemplateResponse(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    user_id: uuid.UUID | None
    name: str
    description: str | None
    prompt_template: str
    n8n_workflow_json: dict
    category: str | None
    tags: list[str]
    is_public: bool
    usage_count: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class WorkflowGenerateRequest(BaseModel):
    prompt: str
    template_id: uuid.UUID | None = None


class WorkflowGenerateResponse(BaseModel):
    n8n_workflow_json: dict
    variables_used: dict
    template_id: uuid.UUID | None


class GeneratedWorkflowResponse(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    user_id: uuid.UUID | None
    prompt_text: str
    n8n_workflow_json: dict
    n8n_workflow_id: str | None
    status: str
    template_id: uuid.UUID | None
    variables_used: dict
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


@router.get("", response_model=list[GeneratedWorkflowResponse])
async def list_workflows(
    status: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        return []

    query = select(GeneratedWorkflow).where(GeneratedWorkflow.team_id == team.id)
    if status:
        query = query.where(GeneratedWorkflow.status == status)
    query = query.order_by(GeneratedWorkflow.created_at.desc())

    rows = await db.execute(query)
    return rows.scalars().all()


@router.get("/templates", response_model=list[PromptTemplateResponse])
async def list_templates(
    category: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        return []

    query = select(PromptTemplate).where(PromptTemplate.team_id == team.id)
    if category:
        query = query.where(PromptTemplate.category == category)
    query = query.order_by(PromptTemplate.usage_count.desc())

    result = await db.execute(query)
    return result.scalars().all()


@router.post("/templates", response_model=PromptTemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_template(
    template_data: PromptTemplateCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    template = PromptTemplate(
        team_id=team.id,
        user_id=current_user.id,
        name=template_data.name,
        description=template_data.description,
        prompt_template=template_data.prompt_template,
        n8n_workflow_json=template_data.n8n_workflow_json,
        category=template_data.category,
        tags=template_data.tags,
        is_public=template_data.is_public,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)

    return template


@router.get("/templates/{template_id}", response_model=PromptTemplateResponse)
async def get_template(
    template_id: uuid.UUID,
    team_id: TeamId,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single prompt template by ID."""
    result = await db.execute(
        select(PromptTemplate).where(PromptTemplate.id == template_id, PromptTemplate.team_id == team_id)
    )
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.patch("/templates/{template_id}", response_model=PromptTemplateResponse)
async def update_template(
    template_id: uuid.UUID,
    updates: dict,
    team_id: TeamId,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a prompt template."""
    result = await db.execute(
        select(PromptTemplate).where(PromptTemplate.id == template_id, PromptTemplate.team_id == team_id)
    )
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    allowed_fields = {
        "name", "description", "prompt_template", "n8n_workflow_json",
        "category", "tags", "is_public",
    }
    for key, value in updates.items():
        if key in allowed_fields:
            setattr(template, key, value)

    await db.commit()
    await db.refresh(template)
    return template


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: uuid.UUID,
    team_id: TeamId,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a prompt template."""
    result = await db.execute(
        select(PromptTemplate).where(PromptTemplate.id == template_id, PromptTemplate.team_id == team_id)
    )
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    await db.delete(template)
    await db.commit()
# ---------------------------------------------------------------------------
# Phase 5 — Content Prompt Templates (per pillar/tone/platform)
# ---------------------------------------------------------------------------


class ContentTemplateCreate(BaseModel):
    name: str
    pillar_id: uuid.UUID | None = None
    platform: str | None = None
    tone: str | None = None
    system_prompt: str
    user_prompt_template: str
    variables: list[str] = []
    is_default: bool = False


class ContentTemplateOut(BaseModel):
    id: uuid.UUID
    name: str
    pillar_id: uuid.UUID | None
    platform: str | None
    tone: str | None
    system_prompt: str
    user_prompt_template: str
    variables: list[str]
    is_default: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


@router.get("/content-templates", response_model=list[ContentTemplateOut])
async def list_content_templates(
    pillar_id: uuid.UUID | None = None,
    platform: str | None = None,
    tone: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team_id = await _resolve_team_id(current_user, db)
    q = select(ContentPromptTemplate).where(ContentPromptTemplate.team_id == team_id)
    if pillar_id:
        q = q.where(ContentPromptTemplate.pillar_id == pillar_id)
    if platform:
        q = q.where(ContentPromptTemplate.platform == platform)
    if tone:
        q = q.where(ContentPromptTemplate.tone == tone)
    q = q.order_by(ContentPromptTemplate.is_default.desc(), ContentPromptTemplate.name)
    result = await db.execute(q)
    return result.scalars().all()


@router.post("/content-templates", response_model=ContentTemplateOut, status_code=status.HTTP_201_CREATED)
async def create_content_template(
    data: ContentTemplateCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team_id = await _resolve_team_id(current_user, db)
    tpl = ContentPromptTemplate(team_id=team_id, **data.model_dump())
    db.add(tpl)
    await db.commit()
    await db.refresh(tpl)
    return tpl


@router.patch("/content-templates/{template_id}", response_model=ContentTemplateOut)
async def update_content_template(
    template_id: uuid.UUID,
    data: ContentTemplateCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team_id = await _resolve_team_id(current_user, db)
    result = await db.execute(
        select(ContentPromptTemplate).where(ContentPromptTemplate.id == template_id, ContentPromptTemplate.team_id == team_id)
    )
    tpl = result.scalar_one_or_none()
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")
    for field, value in data.model_dump().items():
        setattr(tpl, field, value)
    await db.commit()
    await db.refresh(tpl)
    return tpl


@router.delete("/content-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_content_template(
    template_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team_id = await _resolve_team_id(current_user, db)
    result = await db.execute(
        select(ContentPromptTemplate).where(ContentPromptTemplate.id == template_id, ContentPromptTemplate.team_id == team_id)
    )
    tpl = result.scalar_one_or_none()
    if tpl:
        await db.delete(tpl)
        await db.commit()



@router.get("/{workflow_id}", response_model=GeneratedWorkflowResponse)
async def get_workflow(
    workflow_id: uuid.UUID,
    team_id: TeamId,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single generated workflow by ID."""
    result = await db.execute(
        select(GeneratedWorkflow).where(GeneratedWorkflow.id == workflow_id, GeneratedWorkflow.team_id == team_id)
    )
    workflow = result.scalar_one_or_none()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_id: uuid.UUID,
    team_id: TeamId,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a generated workflow."""
    result = await db.execute(
        select(GeneratedWorkflow).where(GeneratedWorkflow.id == workflow_id, GeneratedWorkflow.team_id == team_id)
    )
    workflow = result.scalar_one_or_none()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    await db.delete(workflow)
    await db.commit()


@router.post("/{workflow_id}/undeploy")
async def undeploy_workflow(
    workflow_id: uuid.UUID,
    team_id: TeamId,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Undeploy a workflow from n8n (deactivate + remove)."""
    result = await db.execute(
        select(GeneratedWorkflow).where(GeneratedWorkflow.id == workflow_id, GeneratedWorkflow.team_id == team_id)
    )
    workflow = result.scalar_one_or_none()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if not workflow.n8n_workflow_id:
        raise HTTPException(status_code=400, detail="Workflow is not deployed")

    async with httpx.AsyncClient() as client:
        headers = {"X-N8N-API-KEY": settings.N8N_API_KEY}
        # Deactivate first, then delete
        await client.post(
            f"{settings.N8N_API_URL}/api/v1/workflows/{workflow.n8n_workflow_id}/deactivate",
            headers=headers,
        )
        resp = await client.delete(
            f"{settings.N8N_API_URL}/api/v1/workflows/{workflow.n8n_workflow_id}",
            headers=headers,
        )
        if resp.status_code not in (200, 204):
            raise HTTPException(status_code=500, detail=f"n8n undeploy failed: {resp.text}")

    workflow.n8n_workflow_id = None
    workflow.status = "draft"
    await db.commit()

    return {"message": "Workflow undeployed", "status": "draft"}


@router.post("/generate", response_model=WorkflowGenerateResponse)
async def generate_workflow(
    request: WorkflowGenerateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate an n8n workflow from a natural-language prompt using AI.

    Uses Cloudflare Workers AI (or fallback) to generate a structured n8n
    workflow JSON from the user's prompt. If a template_id is provided, the
    template's workflow JSON is used as a base and variables are extracted.
    """
    import re

    from app.services.inference import call_inference

    template = None
    if request.template_id:
        result = await db.execute(select(PromptTemplate).where(PromptTemplate.id == request.template_id))
        template = result.scalar_one_or_none()

    variables_used: dict = {}
    n8n_workflow: dict = {}

    if template:
        # Use template as base — extract variables from prompt_template
        vars_found = re.findall(r"{{(\w+)}}", template.prompt_template)
        for var in vars_found:
            variables_used[var] = f"<{var}>"
        n8n_workflow = template.n8n_workflow_json.copy()
        n8n_workflow["name"] = f"Generated: {request.prompt[:50]}"
    else:
        # Generate workflow JSON using AI
        result = await db.execute(
            select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
        )
        team = result.scalars().first()

        ai_prompt = (
            "You are an n8n workflow generator. Given a natural-language description, "
            "generate a valid n8n workflow JSON object.\n\n"
            "The workflow must include:\n"
            '- "name": a short workflow name\n'
            '- "nodes": array of node objects, each with name, type, typeVersion, position, and parameters\n'
            '- "connections": object mapping node names to their connections\n'
            '- "settings": {"executionOrder": "v1"}\n\n'
            "Common n8n node types:\n"
            '- "n8n-nodes-base.scheduleTrigger" — triggers on a schedule\n'
            '- "n8n-nodes-base.webhook" — triggers on HTTP request\n'
            '- "n8n-nodes-base.httpRequest" — makes HTTP API calls\n'
            '- "n8n-nodes-base.set" — sets variables\n'
            '- "n8n-nodes-base.if" — conditional branching\n'
            '- "n8n-nodes-base.start" — manual start\n\n'
            f"User request: {request.prompt}\n\n"
            "Return ONLY the JSON workflow object, no explanation."
        )

        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "nodes": {"type": "array", "items": {"type": "object"}},
                "connections": {"type": "object"},
                "settings": {"type": "object"},
            },
            "required": ["name", "nodes", "connections"],
        }

        try:
            ai_result = await call_inference(
                prompt=ai_prompt,
                db=db,
                team_id=team.id if team else None,
                schema=schema,
                endpoint="workflows/generate",
            )
            n8n_workflow = ai_result if isinstance(ai_result, dict) else {}
            if "nodes" not in n8n_workflow:
                n8n_workflow = _fallback_workflow(request.prompt)
        except Exception:
            n8n_workflow = _fallback_workflow(request.prompt)

    # Save generated workflow
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()

    gen_workflow = GeneratedWorkflow(
        team_id=team.id if team else uuid.uuid4(),
        user_id=current_user.id,
        prompt_text=request.prompt,
        n8n_workflow_json=n8n_workflow,
        template_id=request.template_id,
        variables_used=variables_used,
    )
    db.add(gen_workflow)
    await db.commit()

    return WorkflowGenerateResponse(
        n8n_workflow_json=n8n_workflow,
        variables_used=variables_used,
        template_id=request.template_id,
    )


def _fallback_workflow(prompt: str) -> dict:
    """Generate a basic n8n workflow structure as fallback."""
    from app.core.config import get_settings

    _settings = get_settings()
    api_base = (_settings.CORS_ORIGINS[0].rstrip("/") if _settings.CORS_ORIGINS else "http://localhost:8083")
    return {
        "name": f"Generated: {prompt[:50]}",
        "nodes": [
            {
                "name": "Schedule Trigger",
                "type": "n8n-nodes-base.scheduleTrigger",
                "typeVersion": 1.1,
                "position": [250, 300],
                "parameters": {
                    "rule": {
                        "interval": [{"field": "hours", "hoursInterval": 1}],
                    },
                },
            },
            {
                "name": "HTTP Request",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.1,
                "position": [450, 300],
                "parameters": {
                    "method": "POST",
                    "url": f"{api_base}/api/v1/ai/generate-content",
                    "options": {},
                },
            },
        ],
        "connections": {
            "Schedule Trigger": {
                "main": [
                    [{"node": "HTTP Request", "type": "main", "index": 0}],
                ],
            },
        },
        "settings": {"executionOrder": "v1"},
    }


@router.post("/import-cloudless-carousel")
async def import_cloudless_carousel(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Register the Cloudless CF→LinkedIn n8n workflow in app templates + workflows."""
    import json
    import os

    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    n8n_id = "cloudless-cf-carousel-linkedin"
    name = "Cloudless CF Carousel → LinkedIn Company"
    paths = [
        os.environ.get("CLOUDLESS_N8N_WORKFLOW_PATH", ""),
        "/app/n8n-workflows/cloudless-carousel-pipeline.json",
        "/tmp/cloudless-carousel-pipeline.json",
    ]
    workflow = None
    for path in paths:
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                workflow = json.load(f)
            break
    if workflow is None:
        raise HTTPException(
            status_code=500,
            detail="Workflow JSON not found in container; copy to /tmp or set CLOUDLESS_N8N_WORKFLOW_PATH",
        )

    tmpl = (
        await db.execute(
            select(PromptTemplate).where(
                PromptTemplate.team_id == team.id,
                PromptTemplate.name == name,
            )
        )
    ).scalar_one_or_none()
    prompt_tpl = (
        "Publish a {{num_slides}}-slide LinkedIn carousel about {{topic}} "
        "as the cloudless.gr Company Page via Cloudflare Workers AI."
    )
    desc = "Schedule/webhook → social-api CF carousel → LinkedIn Company Page"
    if tmpl:
        tmpl.n8n_workflow_json = workflow
        tmpl.description = desc
        tmpl.prompt_template = prompt_tpl
        tmpl.category = "linkedin"
        tmpl.tags = ["cloudless", "carousel", "cloudflare", "n8n"]
        tmpl.is_public = True
    else:
        tmpl = PromptTemplate(
            team_id=team.id,
            user_id=current_user.id,
            name=name,
            description=desc,
            prompt_template=prompt_tpl,
            n8n_workflow_json=workflow,
            category="linkedin",
            tags=["cloudless", "carousel", "cloudflare", "n8n"],
            is_public=True,
        )
        db.add(tmpl)
    await db.flush()

    gen = (
        await db.execute(
            select(GeneratedWorkflow).where(
                GeneratedWorkflow.team_id == team.id,
                GeneratedWorkflow.n8n_workflow_id == n8n_id,
            )
        )
    ).scalar_one_or_none()
    prompt_text = (
        "cloudless-cf-carousel-linkedin: Cloudflare txt2img carousel "
        "with NLP plain-English fix, CF→HF→Ollama fallback, publish as cloudless.gr Company Page."
    )
    variables = {
        "n8n_workflow_id": n8n_id,
        "webhook": "/webhook/cloudless-carousel",
        "schedule": "next: Fri 28 Aug 03:15 Europe/Athens (CF reset); then every 2 days 19:00 Athens",
        "target_account_id": "4a8d9440-47d2-4bda-bd11-3776fd9022ba",
        "endpoint": "/api/v1/ai/run-carousel-and-publish",
    }
    if gen:
        gen.n8n_workflow_json = workflow
        gen.status = "deployed"
        gen.prompt_text = prompt_text
        gen.template_id = tmpl.id
        gen.variables_used = variables
        action = "updated"
    else:
        gen = GeneratedWorkflow(
            team_id=team.id,
            user_id=current_user.id,
            prompt_text=prompt_text,
            n8n_workflow_json=workflow,
            n8n_workflow_id=n8n_id,
            status="deployed",
            template_id=tmpl.id,
            variables_used=variables,
        )
        db.add(gen)
        action = "created"
    await db.commit()
    await db.refresh(gen)

    return {
        "action": action,
        "generated_workflow_id": str(gen.id),
        "template_id": str(tmpl.id),
        "n8n_workflow_id": n8n_id,
        "status": gen.status,
        "name": name,
    }


@router.post("/deploy/{workflow_id}")
async def deploy_workflow(
    workflow_id: uuid.UUID,
    team_id: TeamId,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(GeneratedWorkflow).where(GeneratedWorkflow.id == workflow_id, GeneratedWorkflow.team_id == team_id))
    workflow = result.scalar_one_or_none()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Deploy to n8n
    async with httpx.AsyncClient() as client:
        headers = {"X-N8N-API-KEY": settings.N8N_API_KEY}
        resp = await client.post(
            f"{settings.N8N_API_URL}/api/v1/workflows",
            headers=headers,
            json=workflow.n8n_workflow_json,
        )
        if resp.status_code not in (200, 201):
            raise HTTPException(status_code=500, detail=f"n8n deploy failed: {resp.text}")

        n8n_workflow = resp.json()
        workflow.n8n_workflow_id = n8n_workflow.get("id")
        workflow.status = "deployed"
        await db.commit()

    return {"message": "Workflow deployed", "n8n_workflow_id": workflow.n8n_workflow_id}


@router.post("/execute/{workflow_id}")
async def execute_workflow(
    workflow_id: uuid.UUID,
    data: dict | None = None,
    team_id: TeamId,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(GeneratedWorkflow).where(GeneratedWorkflow.id == workflow_id, GeneratedWorkflow.team_id == team_id))
    workflow = result.scalar_one_or_none()
    if not workflow or not workflow.n8n_workflow_id:
        raise HTTPException(status_code=404, detail="Workflow not found or not deployed")

    if data is None:
        data = {}

    async with httpx.AsyncClient() as client:
        headers = {"X-N8N-API-KEY": settings.N8N_API_KEY}
        resp = await client.post(
            f"{settings.N8N_API_URL}/api/v1/workflows/{workflow.n8n_workflow_id}/execute",
            headers=headers,
            json={"data": data},
        )
        if resp.status_code not in (200, 201):
            raise HTTPException(status_code=500, detail=f"n8n execute failed: {resp.text}")

        execution = resp.json()

    return {"message": "Execution started", "execution_id": execution.get("id")}


@router.get("/{workflow_id}/executions")
async def get_workflow_executions(
    workflow_id: uuid.UUID,
    limit: int = 10,
    team_id: TeamId,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch recent n8n execution history for a deployed workflow."""
    result = await db.execute(select(GeneratedWorkflow).where(GeneratedWorkflow.id == workflow_id, GeneratedWorkflow.team_id == team_id))
    workflow = result.scalar_one_or_none()
    if not workflow or not workflow.n8n_workflow_id:
        return []

    async with httpx.AsyncClient() as client:
        headers = {"X-N8N-API-KEY": settings.N8N_API_KEY}
        resp = await client.get(
            f"{settings.N8N_API_URL}/api/v1/executions",
            headers=headers,
            params={"workflowId": workflow.n8n_workflow_id, "limit": limit},
        )
        if resp.status_code != 200:
            return []

        data = resp.json()
        executions = data.get("data", data) if isinstance(data, dict) else data

    runs = []
    for ex in executions if isinstance(executions, list) else []:
        status_raw = ex.get("status", "unknown")
        status = "success" if status_raw == "success" else "failed" if status_raw in ("failed", "error") else "running"
        finished_at = ex.get("stoppedAt") or ex.get("finishedAt")
        runs.append({
            "id": ex.get("id"),
            "status": status,
            "label": f"Run {status}",
            "time": finished_at or ex.get("startedAt", ""),
        })
    return runs



async def _resolve_team_id(user: User, db: AsyncSession) -> uuid.UUID:
    result = await db.execute(select(Team).join(TeamMember).where(TeamMember.user_id == user.id))
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return team.id
